"""Cron preview and exact planned-time associations for the task timetable."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..cron_schedule import cron_times, normalize_cron
from ..database import fetch_all, transaction
from ..security import ready_user
from ..services import workflow_schedules

router = APIRouter(prefix='/api/schedules')
MAX_ENTRIES = 2000


def _aware(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError('时间必须带时区')
    return parsed.astimezone(timezone.utc)


class CronPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expression: str = Field(max_length=1024)
    after: datetime | None = None


class PlanPreview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    trigger_type: str = Field(max_length=16)
    trigger_config: dict = Field(default_factory=dict)
    after: datetime | None = None


@router.post('/preview-plan')
def preview_plan(payload: PlanPreview, user: dict = Depends(ready_user)):
    del user
    try:
        return workflow_schedules.preview(payload.trigger_type, payload.trigger_config, payload.after)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/workflows')
def workflow_timetable(start: datetime, end: datetime, user: dict = Depends(ready_user)):
    del user
    try:
        start, end = _aware(start), _aware(end)
        if not timedelta(0) < end-start <= timedelta(days=7):
            raise ValueError('时间表范围应大于零且不超过七天')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with transaction() as conn:
        return workflow_schedules.timetable(conn, start, end)


@router.post('/preview')
def preview(payload: CronPreview, user: dict = Depends(ready_user)):
    del user
    try:
        after = _aware(payload.after or datetime.now(timezone.utc))
        expression = normalize_cron(payload.expression)
        times = cron_times(expression, after, 5)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'expression': expression, 'timezone': 'Asia/Shanghai',
            'after': after.isoformat(), 'times': [t.isoformat(timespec='seconds') for t in times]}


def _round_entry(conn, record):
    record = dict(record)
    execution = conn.execute('SELECT id,status FROM executions WHERE id=?', (record['execution_id'],)).fetchone()
    rerun = conn.execute('SELECT id,status FROM executions WHERE id=?', (record['rerun_execution_id'],)).fetchone()
    job = None
    if record['maintenance_job_id'] and conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_jobs'").fetchone():
        job = conn.execute('SELECT status,note FROM maintenance_jobs WHERE id=?', (record['maintenance_job_id'],)).fetchone()
    state = record['state']
    reason = record['reason']
    if state == 'pending':
        state = 'waiting'
    elif state == 'active':
        if record['maintenance_decision'] == 'entered':
            state = 'maintenance'
            reason = reason or (job['note'] if job else '') or '自动维护或修复重跑中'
        else:
            state = execution['status'] if execution else 'unsettled'
            if state not in {'pending', 'running'}:
                state = 'unsettled'
                reason = reason or '等待结果、维护决定和收尾确认'
    elif state == 'settled':
        state = record.get('final_status') or (rerun['status'] if rerun else (execution['status'] if execution else 'settled'))
        reason = reason or record.get('result_summary', '')
    return {'task': {'id': record['task_id'], 'name': record['task_name'], 'trigger_type': 'cron'},
            'plannedAt': record['scheduled_for'], 'execution': dict(execution) if execution else None,
            'round_id': record['id'], 'state': state, 'reason': reason,
            'maintenance_job_id': record['maintenance_job_id'],
            'maintenance_status': job['status'] if job else None,
            'maintenance_decision': record['maintenance_decision'],
            'maintenance_reason': record.get('maintenance_reason', ''),
            'maintenance_reason_code': record.get('maintenance_reason_code', ''),
            'rerun_execution': dict(rerun) if rerun else None,
            'merged_from': record['coalesced_from']}


@router.get('/cron')
def cron_timetable(start: datetime, end: datetime, user: dict = Depends(ready_user)):
    del user
    try:
        start, end = _aware(start), _aware(end)
        if not timedelta(0) < end - start <= timedelta(days=7):
            raise ValueError('时间表范围应大于零且不超过七天')
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    entries = {}
    with transaction() as conn:
        rows = conn.execute('''SELECT * FROM cron_rounds WHERE scheduled_for>=? AND scheduled_for<?
            ORDER BY scheduled_for,id LIMIT ?''',
            (start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds'), MAX_ENTRIES + 1)).fetchall()
        for row in rows[:MAX_ENTRIES]:
            entries[(row['task_id'], row['scheduled_for'])] = _round_entry(conn, row)
        tasks = conn.execute("SELECT id,name,trigger_config,created_at,next_run_at FROM tasks WHERE enabled=1 AND archived=0 AND trigger_type='cron' AND first_environment_ready=1").fetchall()
    has_more = len(rows) > MAX_ENTRIES
    import json
    now = datetime.now(timezone.utc)
    # The same parser calculates timetable points; the browser never guesses Cron.
    for task in tasks:
        # Historical points come only from the ledger. Editing or re-enabling
        # a schedule cannot invent missed points for previous days.
        after = max(start - timedelta(microseconds=1), now, _aware(task['created_at']))
        if task['next_run_at']:
            after = max(after, _aware(task['next_run_at']) - timedelta(microseconds=1))
        try:
            expression = json.loads(task['trigger_config'])['expression']
            while len(entries) < MAX_ENTRIES:
                point = cron_times(expression, after, 1)[0]
                if point >= end:
                    break
                slot = point.isoformat(timespec='seconds')
                key = (task['id'], slot)
                if key not in entries:
                    entries[key] = {'task': {'id': task['id'], 'name': task['name'], 'trigger_type': 'cron'},
                                    'plannedAt': slot, 'execution': None, 'round_id': None,
                                    'state': 'scheduled' if point > now else 'missed', 'reason': '',
                                    'maintenance_job_id': None, 'maintenance_status': None,
                                    'maintenance_decision': None, 'maintenance_reason': '',
                                    'maintenance_reason_code': '',
                                    'rerun_execution': None, 'merged_from': None}
                after = point
            else:
                has_more = True
        except (ValueError, KeyError, TypeError):
            # Saved invalid configuration remains visible in the trigger ledger.
            continue
    return {'entries': sorted(entries.values(), key=lambda e: (e['plannedAt'], e['task']['id'])),
            'has_more': has_more}


@router.get('/cron-runs')
def cron_history(task_id: int, before_id: int | None = None, user: dict = Depends(ready_user)):
    del user
    with transaction() as conn:
        rows = conn.execute('''SELECT * FROM cron_rounds WHERE task_id=? AND id<?
            ORDER BY id DESC LIMIT 50''', (task_id, before_id or 9223372036854775807)).fetchall()
        return {'items': [_round_entry(conn, row) for row in rows],
                'next_before_id': rows[-1]['id'] if len(rows) == 50 else None}
