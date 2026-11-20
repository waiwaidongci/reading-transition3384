"""切分算法的独立 oracle 测试：不依赖数据库与 Redis。

运行：
    cd backend && python -m pytest ../test-artifacts/gb-0043/test_split_oracle.py -q
"""
import itertools
import pathlib
import random
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[2] / 'backend'
sys.path.insert(0, str(BACKEND))

from src.services.split_algorithm import (  # noqa: E402
    VALIDATION, TRAIN, build_groups, build_result, materialize, plan_groups,
    quick_infeasible, solve_split,
)


def session(collection, reference, path):
    return {'collection_id': collection, 'collection_name': 'C%s' % collection,
            'reference': reference, 'session': reference, 'path': path}


def collections(count):
    return [{'id': cid, 'key': 'K%d' % cid, 'name': 'C%d' % cid} for cid in range(1, count + 1)]


def brute_force(plan):
    """暴力枚举每组归属，作为独立 oracle。"""
    groups, cids = plan['groups'], plan['collection_ids']
    for choices in itertools.product((0, 1), repeat=len(groups)):
        sums = {cid: 0 for cid in cids}
        for group, choice in zip(groups, choices):
            if choice == 0:
                for cid in cids:
                    sums[cid] += group['counts'][cid]
        if all(plan['lower'][cid] <= sums[cid] <= plan['upper'][cid] for cid in cids):
            return True
    return False


def random_plan(rng):
    collections_n = rng.choice([1, 2, 3])
    sessions = []
    for index in range(rng.randint(1, 9)):
        route = 'g%d>x%d>y%d' % (index, index, index)
        for cid in range(1, collections_n + 1):
            for copy in range(rng.choice([1, 1, 2, 2, 3])):
                sessions.append(session(cid, 'R%d-%d-%d' % (index, cid, copy), route))
    return plan_groups(sessions, collections(collections_n))


def test_groups_follow_roadbook_sharing_and_transitive_links():
    sessions = [
        session(1, 'S1', 'A>B>C>D>E'),
        session(1, 'S2', 'X>B>C>D>Y'),
        session(2, 'S3', 'Q>C>D>E>R'),
        session(2, 'S4', 'M>N'),
        session(3, 'S5', 'M>N'),
        session(3, 'S6', 'P>Q'),
    ]
    plan = plan_groups(sessions, collections(3))
    membership = {}
    for group in plan['groups']:
        for index in group['members']:
            membership[sessions[index]['reference']] = group['id']
    # S1 与 S2 共享 B>C>D；S1 与 S3 共享 C>D>E；S2 与 S3 不直接共享，也不共享整条路线。
    assert membership['S1'] == membership['S2'] == membership['S3']
    # 同一路线即使跨文集也是同一组。
    assert membership['S4'] == membership['S5']
    # 只共享两章不算共享连续三章。
    assert membership['S6'] != membership['S4']


def test_route_and_trigram_groups_must_not_cross_sides():
    rng = random.Random(7)
    for _ in range(150):
        plan = random_plan(rng)
        outcome = solve_split(plan, 5)
        assert outcome['status'] in ('solved', 'infeasible')
        if outcome['status'] == 'solved':
            result = build_result(plan, materialize(plan, outcome), outcome['nodes'])
            assert result['checks']['verified'] is True
            by_reference = {row['reference']: row['side'] for row in result['rows']}
            assert set(by_reference.values()) <= {TRAIN, VALIDATION}


def test_solver_matches_brute_force_oracle():
    rng = random.Random(1234)
    for _ in range(300):
        plan = random_plan(rng)
        expected = brute_force(plan)
        outcome = solve_split(plan, 5)
        assert (outcome['status'] == 'solved') is expected, (plan['lower'], plan['upper'],
                                                             [g['counts'] for g in plan['groups']])
        if expected:
            result = build_result(plan, materialize(plan, outcome), outcome['nodes'])
            for item in result['collections']:
                assert item['train'] >= 1 and item['validation'] >= 1
                assert abs(item['train'] - item['validation']) <= 1


def test_only_proven_infeasible_is_reported():
    same_route = [session(cid, 'R%d-%d' % (cid, i), 'A>B>C') for cid in (1, 2) for i in range(4)]
    plan = plan_groups(same_route, collections(2))
    assert quick_infeasible(plan)
    assert solve_split(plan, 5)['status'] == 'infeasible'

    # 单文集 8 个会话、组大小 3/3/2：单组都不超上限，但只能凑出 0/2/3/5/6/8，
    # 无法让两侧各 4 人 => quick_infeasible 看不出来，必须交给精确搜索证明。
    paths = ['A>B>C'] * 3 + ['D>E>F'] * 3 + ['G>H>I'] * 2
    sessions = [session(1, 'S%d' % i, path) for i, path in enumerate(paths)]
    plan = plan_groups(sessions, collections(1))
    assert quick_infeasible(plan) is None
    assert brute_force(plan) is False
    assert solve_split(plan, 5)['status'] == 'infeasible'


def test_pending_resume_finishes_with_saved_progress():
    rng = random.Random(5)
    plan = random_plan(rng)
    direct = solve_split(plan, 10)
    assert direct['status'] == 'solved'

    class Clock:
        def __init__(self, step=0.0005):
            self.t, self.step = 0.0, step

        def __call__(self):
            self.t += self.step
            return self.t

    clock = Clock()
    progress, rounds = None, 0
    while True:
        rounds += 1
        outcome = solve_split(plan, 0.002, checkpoint=progress, clock=clock)
        if outcome['status'] != 'pending' or rounds > 300:
            break
        progress = outcome['progress']
    assert outcome['status'] == 'solved' and rounds > 1
    first = build_result(plan, materialize(plan, direct), direct['nodes'])
    resumed = build_result(plan, materialize(plan, outcome), outcome['nodes'])
    assert first['checksum'] == resumed['checksum']


def test_checkpoint_of_other_plan_is_ignored():
    b = plan_groups([session(1, 'B%d' % i, 'X>Y>Z' if i < 2 else 'P%d>Q%d>R%d' % (i, i, i))
                     for i in range(4)], collections(1))
    stale = {'order': ['Z9999'], 'decisions': [0, 1], 'frames': [1, 2, 0], 'nodes': 77}
    outcome = solve_split(b, 5, checkpoint=stale)
    assert outcome['status'] in ('solved', 'infeasible')
    assert outcome['restored'] == ''  # 进度属于别的分组顺序时被忽略，从空进度开始

    malformed = {'order': [group['id'] for group in sorted(b['groups'], key=lambda g: (-g['size'], g['id']))],
                 'decisions': [5], 'frames': [0, 0], 'nodes': 3}
    assert solve_split(b, 5, checkpoint=malformed)['restored'] == ''
