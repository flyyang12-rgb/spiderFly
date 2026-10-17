"""Workflow plans: atomic occurrence, complete round snapshot and plan advancement."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3

from fastapi import HTTPException

from ..cron_schedule import previous_cron_time
from ..database import transaction, utc_now
from ..scheduling import CHINA_TZ, compute_next_run, normalize_trigger
from . import workflows

logger = logging.getLogger(__name__)
PLAN_TYPES = {'manual', 'daily', 'weekly', 'cron'}


def ensure_schema(conn):
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(workflows)')}
    for name, definition in (('schedule_revision', 'INTEGER NOT NULL DEFAULT 1'), ('last_triggered_at', 'TEXT')):
        if name not in columns:
            conn.execute(f'ALTER TABLE workflows ADD COLUMN {name} {definition}')
    conn.execute('''CREATE TABLE IF NOT EXISTS workflow_triggers (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id INTEGER NOT NULL,
        workflow_name TEXT NOT NULL, scheduled_for TEXT NOT NULL, trigger_type TEXT NOT NULL,
        config TEXT NOT NULL, schedule_revision INTEGER NOT NULL, definition_revision INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('registered','active','settled','skipped','error')),
        run_id INTEGER UNIQUE, reason TEXT NOT NULL DEFAULT '',
        coalesced_from TEXT, coalesced_to TEXT, created_at TEXT NOT NULL,
        enqueued_at TEXT, ended_at TEXT, final_status TEXT,
        UNIQUE(workflow_id,scheduled_for))''')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_workflow_trigger_active ON workflow_triggers(workflow_id) WHERE state IN ('registered','active')")
    conn.execute('CREATE INDEX IF NOT EXISTS ix_workflow_trigger_history ON workflow_triggers(workflow_id,id DESC)')


def _time(value=None):
    point = datetime.fromisoformat(value) if isinstance(value, str) else value
    point = point or datetime.now(timezone.utc)
    if point.tzinfo is None:
        raise ValueError('计划时间必须带时区')
    return point.astimezone(timezone.utc)


def _iso(value=None):
    return _time(value).isoformat(timespec='seconds')


def validate_members(conn, task_ids):
    workflows._validate_members(conn, task_ids)
    conflicts = []
    for task_id in task_ids:
        task = workflows._member(conn, task_id)
        if not task['enabled'] or task['trigger_type'] != 'manual':
            conflicts.append(f"{task['name']}（{'已停用' if not task['enabled'] else '已有独立计划'}）")
    if conflicts:
        raise HTTPException(409, '开启流程计划前，请启用成员并将其触发方式设为手动：' + '、'.join(conflicts))


def assert_task_plan_allowed(conn, task_id, trigger_type):
    if trigger_type == 'manual' or not workflows.has_schema(conn):
        return
    row = conn.execute('''SELECT w.name FROM workflows w JOIN workflow_members m ON m.workflow_id=w.id
        WHERE m.task_id=? AND w.archived=0 AND w.schedule_enabled=1 LIMIT 1''', (task_id,)).fetchone()
    if row:
        raise HTTPException(409, f"任务已由工作流“{row['name']}”统一定时，请先关闭该流程计划再设置独立计划")


def update_plan(conn, workflow_id, trigger_type, config, enabled, revision, actor=None, now=None, *, bump_revision=True):
    definition = workflows._definition(conn, workflow_id, active=True)
    if definition['revision'] != revision:
        raise HTTPException(409, '工作流已被其他操作修改，请刷新后重试')
    if trigger_type not in PLAN_TYPES:
        raise ValueError('工作流仅支持手动、每日、每周和 Cron')
    normalized = normalize_trigger(trigger_type, config)
    if enabled and trigger_type == 'manual':
        raise ValueError('手动流程不能开启定时计划')
    if enabled:
        ids = [row['task_id'] for row in conn.execute('SELECT task_id FROM workflow_members WHERE workflow_id=? ORDER BY position', (workflow_id,))]
        validate_members(conn, ids)
    encoded = workflows._json(normalized)
    changed = (definition['trigger_type'] != trigger_type or definition['trigger_config'] != encoded)
    next_run = definition['next_run_at']
    if not enabled:
        next_run = None
    elif not definition['schedule_enabled'] or changed:
        next_run = compute_next_run(trigger_type, normalized, after=_time(now))
    conn.execute('''UPDATE workflows SET trigger_type=?,trigger_config=?,schedule_enabled=?,next_run_at=?,
        schedule_revision=schedule_revision+1,revision=revision+?,updated_by=?,updated_at=? WHERE id=? AND revision=?''',
        (trigger_type, encoded, int(enabled), next_run, int(bump_revision), actor, _iso(now), workflow_id, revision))
    return workflows.get_definition(conn, workflow_id)


def preview(trigger_type, config, after=None):
    if trigger_type not in PLAN_TYPES:
        raise ValueError('工作流仅支持手动、每日、每周和 Cron')
    normalized = normalize_trigger(trigger_type, config)
    base = _time(after)
    point = base
    times = []
    for _ in range(5):
        value = compute_next_run(trigger_type, normalized, point)
        if value is None:
            break
        times.append(value)
        point = _time(value)
    return {'trigger_type': trigger_type, 'trigger_config': normalized, 'timezone': 'Asia/Shanghai',
            'after': _iso(base), 'times': times}


def latest_slot(trigger_type, config, now):
    point = _time(now)
    if trigger_type == 'cron':
        return previous_cron_time(config['expression'], point).isoformat(timespec='seconds')
    local = point.astimezone(CHINA_TZ)
    clock = datetime.strptime(config['time'], '%H:%M').time()
    for offset in range(8):
        day = local.date() - timedelta(days=offset)
        if trigger_type == 'weekly' and day.isoweekday() not in config['weekdays']:
            continue
        candidate = datetime.combine(day, clock, CHINA_TZ)
        if candidate <= local:
            return _iso(candidate)
    raise ValueError('无法计算最近一次流程计划')


def reconcile(conn=None):
    with workflows._connection(conn) as opened:
        if not workflows._table(opened, 'workflow_triggers'):
            return
        for trigger in opened.execute("SELECT * FROM workflow_triggers WHERE state='active'").fetchall():
            run = opened.execute('SELECT * FROM workflow_runs WHERE id=?', (trigger['run_id'],)).fetchone()
            if not run:
                # Missing facts must not release the outstanding occurrence.
                opened.execute("UPDATE workflow_triggers SET reason='关联流程运行缺失，等待人工核对' WHERE id=?", (trigger['id'],))
                continue
            first = opened.execute('''SELECT e.created_at FROM workflow_steps s JOIN executions e ON e.id=s.execution_id
                WHERE s.run_id=? ORDER BY s.position LIMIT 1''', (run['id'],)).fetchone()
            if first and not trigger['enqueued_at']:
                opened.execute('UPDATE workflow_triggers SET enqueued_at=? WHERE id=?', (first['created_at'], trigger['id']))
            if run['state'] not in workflows.ACTIVE:
                opened.execute("UPDATE workflow_triggers SET state='settled',ended_at=?,final_status=?,reason=? WHERE id=?",
                    (run['ended_at'] or utc_now(), run['state'], run['reason'], trigger['id']))


def register_due(workflow_id, now=None, expected_next=None, expected_revision=None):
    now = _iso(now)
    folder = None
    committed = False
    try:
        with transaction() as conn:
            reconcile(conn)
            definition = workflows._definition(conn, workflow_id)
            first = definition['next_run_at']
            if definition['archived'] or not definition['schedule_enabled'] or not first or _time(first) > _time(now):
                return None
            if expected_next is not None and first != expected_next:
                return None
            if expected_revision is not None and definition['schedule_revision'] != expected_revision:
                return None
            config = json.loads(definition['trigger_config'])
            slot = latest_slot(definition['trigger_type'], config, now)
            if _time(slot) < _time(first):
                raise ValueError('保存的流程计划点与表达式不一致')
            next_run = compute_next_run(definition['trigger_type'], config, now)
            previous = conn.execute('SELECT * FROM workflow_triggers WHERE workflow_id=? AND scheduled_for=?', (workflow_id, slot)).fetchone()
            if previous:
                conn.execute('UPDATE workflows SET next_run_at=? WHERE id=?', (next_run, workflow_id))
                return dict(previous)
            occupied = conn.execute('''SELECT id FROM workflow_runs WHERE workflow_id=? AND
                (state IN ('waiting','running','stopping') OR (created_at<=? AND ended_at>=?)) LIMIT 1''', (workflow_id, slot, slot)).fetchone()
            ledger_busy = conn.execute("SELECT id FROM workflow_triggers WHERE workflow_id=? AND state IN ('registered','active')", (workflow_id,)).fetchone()
            reason = '上一轮未结束，已跳过' if occupied or ledger_busy else ''
            merged = _time(slot) > _time(first)
            if merged:
                reason += ('；' if reason else '') + '停机漏点合并，只处理最近计划点'
            state = 'skipped' if occupied or ledger_busy else 'registered'
            trigger_id = int(conn.execute('''INSERT INTO workflow_triggers(workflow_id,workflow_name,scheduled_for,
                trigger_type,config,schedule_revision,definition_revision,state,reason,coalesced_from,coalesced_to,created_at,ended_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''', (workflow_id, definition['name'], slot, definition['trigger_type'],
                definition['trigger_config'], definition['schedule_revision'], definition['revision'], state, reason,
                first if merged else None, slot if merged else None, now, now if state == 'skipped' else None)).lastrowid)
            if state == 'registered':
                conn.execute('SAVEPOINT workflow_acceptance')
                try:
                    ids = [row['task_id'] for row in conn.execute('SELECT task_id FROM workflow_members WHERE workflow_id=? ORDER BY position', (workflow_id,))]
                    validate_members(conn, ids)
                    run = workflows.start_run(workflow_id, None, f'workflow-plan-{trigger_id}',
                        intent={'scheduled_for': slot, 'schedule_revision': definition['schedule_revision']},
                        conn=conn, source='schedule', scheduled_for=slot)
                    snapshot = conn.execute('SELECT snapshot_storage_dir FROM workflow_steps WHERE run_id=? ORDER BY position LIMIT 1', (run['id'],)).fetchone()
                    folder = Path(snapshot['snapshot_storage_dir']).parent if snapshot else None
                    conn.execute("UPDATE workflow_triggers SET state='active',run_id=? WHERE id=?", (run['id'], trigger_id))
                    conn.execute('RELEASE workflow_acceptance')
                except (HTTPException, ValueError, OSError) as exc:
                    conn.execute('ROLLBACK TO workflow_acceptance')
                    conn.execute('RELEASE workflow_acceptance')
                    reason = str(exc.detail if isinstance(exc, HTTPException) else exc)
                    conn.execute("UPDATE workflow_triggers SET state='error',reason=?,ended_at=? WHERE id=?", (reason[:2000], now, trigger_id))
            conn.execute('UPDATE workflows SET next_run_at=?,last_triggered_at=? WHERE id=?', (next_run, slot, workflow_id))
            reconcile(conn)
            result = dict(conn.execute('SELECT * FROM workflow_triggers WHERE id=?', (trigger_id,)).fetchone())
        committed = True
        return result
    finally:
        if not committed and folder and folder.resolve().parent == workflows.SNAPSHOT_ROOT.resolve() and folder.is_dir():
            shutil.rmtree(folder)


def scan_due(now=None):
    now = _iso(now)
    with transaction() as conn:
        if not workflows.has_schema(conn):
            return
        reconcile(conn)
        rows = [dict(row) for row in conn.execute('''SELECT id,next_run_at,schedule_revision FROM workflows
            WHERE archived=0 AND schedule_enabled=1 AND next_run_at<=? ORDER BY next_run_at,id''', (now,))]
    for row in rows:
        try:
            register_due(row['id'], now, row['next_run_at'], row['schedule_revision'])
        except Exception:
            logger.exception('流程 %s 计划登记失败，保留计划点待下次核对', row['id'])


def _entry(conn, row):
    record = dict(row)
    run = workflows.get_run(conn, record['run_id'], include_steps=False) if record['run_id'] and conn.execute('SELECT 1 FROM workflow_runs WHERE id=?', (record['run_id'],)).fetchone() else None
    state = record['final_status'] if record['state'] == 'settled' else (run['status'] if run else record['state'])
    return {'object_type': 'workflow', 'workflow_id': record['workflow_id'],
        'task': {'id': record['workflow_id'], 'name': record['workflow_name'], 'trigger_type': record['trigger_type']},
        'plannedAt': record['scheduled_for'], 'state': state, 'reason': (run['reason'] if run and run['reason'] else record['reason']),
        'execution': None, 'run_id': record['run_id'], 'trigger_id': record['id'],
        'merged_from': record['coalesced_from'], 'merged_to': record['coalesced_to']}


def history(conn, workflow_id, before_id=None):
    rows = conn.execute('SELECT * FROM workflow_triggers WHERE workflow_id=? AND id<? ORDER BY id DESC LIMIT 50', (workflow_id, before_id or 9223372036854775807)).fetchall()
    return {'items': [_entry(conn, row) for row in rows], 'next_before_id': rows[-1]['id'] if len(rows) == 50 else None}


def timetable(conn, start, end, now=None, limit=2000):
    start, end, now = _time(start), _time(end), _time(now)
    rows = conn.execute('SELECT * FROM workflow_triggers WHERE scheduled_for>=? AND scheduled_for<? ORDER BY scheduled_for,id LIMIT ?', (_iso(start), _iso(end), limit+1)).fetchall()
    entries = {(row['workflow_id'], row['scheduled_for']): _entry(conn, row) for row in rows[:limit]}
    has_more = len(rows) > limit
    for row in conn.execute('SELECT * FROM workflows WHERE archived=0 AND schedule_enabled=1 ORDER BY id'):
        after = max(start-timedelta(microseconds=1), now, _time(row['next_run_at'])-timedelta(microseconds=1)) if row['next_run_at'] else max(start-timedelta(microseconds=1), now)
        config = json.loads(row['trigger_config'])
        while len(entries) < limit:
            point = compute_next_run(row['trigger_type'], config, after)
            if point is None or _time(point) >= end:
                break
            entries[(row['id'], point)] = {'object_type': 'workflow', 'workflow_id': row['id'],
                'task': {'id': row['id'], 'name': row['name'], 'trigger_type': row['trigger_type']},
                'plannedAt': point, 'state': 'scheduled', 'reason': '', 'execution': None,
                'run_id': None, 'trigger_id': None, 'merged_from': None, 'merged_to': None}
            after = _time(point)
        else:
            has_more = True
    return {'entries': sorted(entries.values(), key=lambda item: (item['plannedAt'], item['workflow_id'])), 'has_more': has_more}
