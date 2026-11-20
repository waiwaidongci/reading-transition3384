"""会话切分任务：受理时冻结会话快照、排队、超时续跑与逐会话核验结果。

- 受理时把所选文集的会话逐行复制进任务；之后来源改名、追加记录都不会影响本次切分。
- worker 只领取 queued 状态的任务，重复投递或重复重试不会重复发布结果。
- 搜索超出时间预算时进入 pending 并保留搜索进度；续跑从该进度继续，不从头重算。
"""
import logging
import time
from datetime import datetime, timezone
from fastapi import HTTPException
from redis import Redis
from sqlalchemy import select, update

from ..config.settings import REDIS_URL

from ..models.source import Source
from ..models.record import Record
from ..models.split import SplitJob
from ..utils.serialization import row, now
from .audit import log
from .split_algorithm import plan_groups, quick_infeasible, solve_split, materialize, build_result

queue = Redis.from_url(REDIS_URL)
QUEUE_KEY = 'analysis:queue'


def list_jobs(db):
    hidden = ('sessions', 'collections', 'progress', 'result')
    jobs = []
    for job in db.scalars(select(SplitJob).order_by(SplitJob.id.desc()).limit(60)):
        item = {k: v for k, v in row(job).items() if k not in hidden}
        item['progress'] = _progress(job)
        jobs.append(item)
    return jobs


def get_job(db, id):
    job = db.get(SplitJob, id)
    if not job:
        raise HTTPException(404, '未找到切分任务')
    return job


def _progress(job):
    progress = job.progress or {}
    return {'nodes': progress.get('nodes', 0), 'decided': len(progress.get('decisions', []))}


def detail(db, id):
    job = get_job(db, id)
    data = row(job)
    for key in ('sessions', 'progress'):
        data.pop(key, None)
    result = job.result or {}
    data['result'] = result if job.state in ('completed', 'infeasible') else None
    data['summary'] = result.get('summary', [])
    data['checks'] = result.get('checks')
    data['reason'] = result.get('reason', '')
    data['progress'] = _progress(job)
    return data


def _snapshot(db, collection_ids):
    ids = []
    for cid in collection_ids:
        if cid not in ids:
            ids.append(cid)
    collections, sessions = [], []
    for cid in ids:
        source = db.get(Source, cid)
        if not source:
            raise HTTPException(404, '未找到文集 #%s' % cid)
        collections.append({'id': source.id, 'key': source.key, 'name': source.name})
        for record in db.scalars(select(Record).where(Record.source_id == source.id).order_by(Record.id)):
            values = record.values or {}
            sessions.append({'collection_id': source.id, 'collection_name': source.name,
                             'collection_key': source.key, 'reference': record.reference,
                             'session': values.get('session', ''), 'path': values.get('path', '')})
    return ids, collections, sessions


def _enqueue(db, job):
    """投递失败时只报告，不改变任务状态，避免丢掉待定进度。"""
    try:
        queue.rpush(QUEUE_KEY, 'split:%s' % job.id)
        return True
    except Exception:
        logging.exception('split queue unavailable')
        return False


def create_job(db, user, data):
    ids, collections, sessions = _snapshot(db, data.collection_ids)
    plan = plan_groups(sessions, collections)
    hint = quick_infeasible(plan)
    stamp = now()
    job = SplitJob(name=data.name, state='queued', collection_ids=ids, collections=collections,
                   sessions=sessions, budget_seconds=data.budget_seconds, progress=None, result=None,
                   error=hint or '', nodes=0, attempts=0, created_at=stamp, updated_at=stamp)
    if hint:
        job.state = 'infeasible'
        job.result = {'status': 'infeasible', 'reason': hint, 'groups': len(plan['groups']),
                      'collections': collections}
    db.add(job)
    db.flush()
    log(db, user, 'split.infeasible' if hint else 'split.queued', job.id, data.name)
    db.commit()
    if not hint and not _enqueue(db, job):
        raise HTTPException(503, '队列暂不可用，任务已保存为排队中，可在稍后续跑')
    return detail(db, job.id)


STALE_SECONDS = 90


def _stale(job, seconds=STALE_SECONDS):
    """worker 中途崩溃时任务会停在 running；超过阈值即可按已存进度续跑。"""
    try:
        updated = datetime.fromisoformat(job.updated_at)
        return (datetime.now(timezone.utc) - updated).total_seconds() > seconds
    except (TypeError, ValueError):
        return False


def resume_job(db, user, id):
    """从已保存的搜索进度续跑；接受排队中、超时待定，以及长时间无进展的 running。"""
    job = get_job(db, id)
    allowed = ('queued', 'pending')
    claim = update(SplitJob).where(SplitJob.id == id, SplitJob.state.in_(allowed))
    if job.state == 'running' and _stale(job):
        claim = update(SplitJob).where(SplitJob.id == id, SplitJob.state == 'running',
                                        SplitJob.updated_at == job.updated_at)
    claimed = db.execute(claim.values(state='queued', error='', updated_at=now())).rowcount
    if not claimed:
        db.rollback()
        raise HTTPException(409, '只有排队中、超时待定或已中断的任务可以续跑')
    log(db, user, 'split.resumed', id, job.name)
    db.commit()
    if not _enqueue(db, get_job(db, id)):
        raise HTTPException(503, '队列暂不可用，请稍后再次续跑')
    return detail(db, id)


def export_payload(db, id):
    job = get_job(db, id)
    if job.state != 'completed':
        raise HTTPException(409, '切分尚未完成')
    return job.result


def execute(id, clock=time.monotonic):
    """worker 领取任务；只有 queued 状态会被处理，因此重复投递不会重复发布。"""
    from ..config.database import Session
    with Session() as db:
        claimed = db.execute(update(SplitJob)
                             .where(SplitJob.id == id, SplitJob.state == 'queued')
                             .values(state='running', attempts=SplitJob.attempts + 1, updated_at=now())).rowcount
        db.commit()
        if not claimed:
            return None
        job = db.get(SplitJob, id)
        try:
            plan = plan_groups(job.sessions, job.collections)
            hint = quick_infeasible(plan)
            if hint:
                outcome = {'status': 'infeasible', 'nodes': 0}
            else:
                outcome = solve_split(plan, job.budget_seconds, checkpoint=job.progress,
                                      save=lambda progress: _save(db, job, progress), clock=clock)
            if outcome['status'] == 'infeasible':
                job.state = 'infeasible'
                job.result = {'status': 'infeasible',
                              'reason': hint or '穷尽全部分组组合后仍无合法切分',
                              'groups': len(plan['groups']), 'collections': job.collections,
                              'nodes': outcome.get('nodes', 0)}
                job.error = ''
                job.progress = None
                log(db, 'worker', 'split.infeasible', job.id)
            elif outcome['status'] == 'solved':
                side = materialize(plan, outcome)
                result = build_result(plan, side, outcome.get('nodes', 0))
                result.update({'status': 'solved', 'restored': outcome.get('restored', ''),
                               'group_count': len(plan['groups'])})
                job.result = result
                job.state = 'completed'
                job.error = ''
                job.progress = None
                job.nodes = outcome.get('nodes', 0)
                log(db, 'worker', 'split.completed', job.id)
            else:
                job.progress = outcome['progress']
                job.state = 'pending'
                job.nodes = outcome.get('nodes', 0)
                job.error = '搜索超出时间预算，已保留进度，可继续续跑'
                log(db, 'worker', 'split.pending', job.id)
        except Exception as error:
            job.state = 'failed'
            job.error = str(error)[:2000]
            log(db, 'worker', 'split.failed', job.id, job.error)
        job.updated_at = now()
        db.commit()
        return job.state


def _save(db, job, progress):
    job.progress = progress
    job.nodes = progress.get('nodes', 0)
    job.updated_at = now()
    db.commit()
