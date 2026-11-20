"""会话切分的 HTTP 全流程测试：受理冻结、并发补录隔离、重复执行不重复发布、
超时待定与续跑、无解报告、权限与审计。

运行（需要可用的 DATABASE_URL；默认用临时 SQLite）：
    cd backend && python -m pytest ../test-artifacts/gb-0043/test_split_flow.py -q
"""
import os
import pathlib
import sys
import tempfile

BACKEND = pathlib.Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))

DB_FILE = pathlib.Path(tempfile.gettempdir()) / 'split-flow-test.db'
if DB_FILE.exists():
    DB_FILE.unlink()
os.environ['DATABASE_URL'] = 'sqlite:///%s' % DB_FILE
os.environ['ARTIFACT_DIR'] = str(pathlib.Path(tempfile.gettempdir()) / 'split-flow-artifacts')
os.environ['SEED_DEMO'] = 'false'

import pytest  # noqa: E402


class FakeQueue:
    def __init__(self):
        self.items = []

    def rpush(self, key, value):
        self.items.append(value)
        return len(self.items)

    def ping(self):
        return True

    def blpop(self, key, timeout=0):
        return None


@pytest.fixture(scope='module')
def env():
    from src.services import runs as runs_service, splits as split_service
    fake = FakeQueue()
    runs_service.queue = fake
    split_service.queue = fake
    import src.main as main
    main.queue = fake
    from src.config.database import Base, engine, Session
    from src.models import user, source, record, run, note, audit, split  # noqa: F401
    from src.models.user import User
    from src.models.source import Source
    from src.models.record import Record
    from src.utils.security import hash_password
    from scripts.seed_demo import transition_paths
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'analyst', 'viewer'):
            db.add(User(email=role + '@flow.demo', password_hash=hash_password('Research2026!'), role=role))
        for batch in range(3):
            source = Source(key='SY-%02d' % batch, name='C%d' % batch, filename='c%d.csv' % batch, description='')
            db.add(source)
            db.flush()
            for index, path in enumerate(transition_paths(batch)):
                db.add(Record(source_id=source.id, reference='R%d-%04d' % (batch, index),
                              values={'session': 'S%d-%04d' % (batch, index), 'path': '>'.join(path)}))
        db.commit()
    from fastapi.testclient import TestClient
    with TestClient(main.app) as client:
        yield client, split_service, fake


def headers(client, email):
    token = client.post('/auth/login', json={'email': email, 'password': 'Research2026!'}).json()['token']
    return {'Authorization': 'Bearer ' + token}


def seeded_ids(client, auth):
    """只取种子里的三个常规文集，避免其他用例新增的小文集干扰。"""
    return [source['id'] for source in client.get('/sources', headers=auth).json()
            if source['key'].startswith('SY-')]


def create(client, auth, collection_ids, name='切分', budget=5):
    response = client.post('/splits', json={'name': name, 'collection_ids': collection_ids, 'budget_seconds': budget},
                           headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def test_accept_freezes_snapshot_against_concurrent_appends(env):
    client, service, fake = env
    auth = headers(client, 'analyst@flow.demo')
    ids = seeded_ids(client, auth)
    job = create(client, auth, ids)
    frozen = sum(1 for _ in job.get('collection_ids', []))
    assert frozen == len(ids)
    # 受理后补录：新的会话不能进入这次切分。
    added = client.post('/sources/%d/records' % ids[0], headers=auth,
                        json={'records': [{'reference': 'LATE-1', 'values': {'session': 'LATE-1', 'path': '甲>乙>丙'}}]})
    assert added.status_code == 200
    assert service.execute(job['id']) == 'completed'
    detail = client.get('/splits/%d' % job['id'], headers=auth).json()
    references = {row['reference'] for row in detail['result']['rows']}
    assert 'LATE-1' not in references
    assert len(references) == 720


def test_repeated_execution_does_not_republish(env):
    client, service, fake = env
    auth = headers(client, 'analyst@flow.demo')
    ids = seeded_ids(client, auth)
    job = create(client, auth, ids, name='重复投递')
    assert service.execute(job['id']) == 'completed'
    first = client.get('/splits/%d' % job['id'], headers=auth).json()
    assert service.execute(job['id']) is None
    second = client.get('/splits/%d' % job['id'], headers=auth).json()
    assert first['result']['checksum'] == second['result']['checksum']
    assert second['attempts'] == 1
    published = [e for e in client.get('/audit', headers=auth).json()
                 if e['action'] == 'split.completed' and int(e['target']) == job['id']]
    assert len(published) == 1


def test_timeout_is_pending_then_resume_finishes(env):
    client, service, fake = env
    auth = headers(client, 'analyst@flow.demo')
    ids = seeded_ids(client, auth)
    job = create(client, auth, ids, name='超时待定', budget=0.0001)

    class Slow:
        def __init__(self):
            self.t = 0.0

        def __call__(self):
            self.t += 10
            return self.t

    assert service.execute(job['id'], clock=Slow()) == 'pending'
    pending = client.get('/splits/%d' % job['id'], headers=auth).json()
    assert pending['state'] == 'pending' and '预算' in pending['error']
    assert client.get('/splits/%d/export' % job['id'], headers=auth).status_code == 409
    assert client.post('/splits/%d/resume' % job['id'], headers=auth).status_code == 200
    assert service.execute(job['id']) == 'completed'
    done = client.get('/splits/%d' % job['id'], headers=auth).json()
    assert done['result']['checks']['verified'] is True
    assert client.post('/splits/%d/resume' % job['id'], headers=auth).status_code == 409


def test_infeasible_is_reported_with_reason(env):
    client, service, fake = env
    auth = headers(client, 'analyst@flow.demo')
    source = client.post('/sources', headers=auth,
                         json={'name': '单会话文集', 'filename': 'one.csv', 'description': '只有一个会话'}).json()
    client.post('/sources/%d/records' % source['id'], headers=auth,
                json={'records': [{'reference': 'ONE-1', 'values': {'session': 'ONE-1', 'path': '甲>乙>丙'}}]})
    job = create(client, auth, [source['id']], name='单会话无解')
    assert job['state'] == 'infeasible'
    assert '无法在两侧都有人' in job['error']
    assert client.get('/splits/%d/export' % job['id'], headers=auth).status_code == 409


def test_cross_collection_shared_route_stays_on_one_side(env):
    client, service, fake = env
    auth = headers(client, 'analyst@flow.demo')
    ids = seeded_ids(client, auth)
    job = create(client, auth, ids, name='共用路线')
    assert service.execute(job['id']) == 'completed'
    detail = client.get('/splits/%d' % job['id'], headers=auth).json()
    assert detail['result']['checks']['no_route_leak'] is True
    assert detail['result']['checks']['no_trigram_leak'] is True
    for item in detail['result']['collections']:
        assert item['train'] >= 1 and item['validation'] >= 1
        assert item['difference'] <= 1


def test_permissions_and_audit(env):
    client, service, fake = env
    analyst = headers(client, 'analyst@flow.demo')
    viewer = headers(client, 'viewer@flow.demo')
    ids = seeded_ids(client, analyst)
    job = create(client, analyst, ids, name='权限检查')
    assert service.execute(job['id']) == 'completed'
    assert client.post('/splits', headers=viewer, json={'name': 'x', 'collection_ids': ids,
                                                        'budget_seconds': 5}).status_code == 403
    assert client.post('/splits/%d/resume' % job['id'], headers=viewer).status_code == 403
    assert client.get('/splits/%d/export' % job['id'], headers=viewer).status_code == 200
    assert client.get('/splits', headers={}).status_code == 401
    events = [event['action'] for event in client.get('/audit', headers=analyst).json()]
    assert 'split.queued' in events and 'split.completed' in events
    assert 'split.resumed' in events
