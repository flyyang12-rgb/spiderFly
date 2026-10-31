"""Fixed delay plans: persist one countdown after the whole run has finished."""
from __future__ import annotations

import json
import logging

from ..database import transaction, utc_now
from ..scheduling import compute_next_run

logger = logging.getLogger(__name__)


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS interval_schedule_state (
        object_type TEXT NOT NULL, object_id INTEGER NOT NULL,
        completion_key TEXT NOT NULL, waiting INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(object_type,object_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS interval_executions (
        execution_id INTEGER PRIMARY KEY REFERENCES executions(id) ON DELETE CASCADE)''')


def reset(conn, object_type, object_id):
    ensure_schema(conn)
    conn.execute('DELETE FROM interval_schedule_state WHERE object_type=? AND object_id=?',
                 (object_type, object_id))


def refresh(conn, object_type, item, now):
    """Rearm only on a new completion, preserving an overdue countdown on restart."""
    from . import workflows
    object_id = item['id']
    table = 'tasks' if object_type == 'task' else 'workflows'
    if object_type == 'task':
        busy = bool(workflows.task_busy_reason(conn, object_id))
        from ..runner import _execution_controls
        active_ids = tuple(_execution_controls.copy())
        if active_ids:
            busy = busy or bool(conn.execute(f"SELECT 1 FROM executions WHERE task_id=? AND id IN ({','.join('?' for _ in active_ids)})",
                                             (object_id, *active_ids)).fetchone())
        if workflows._table(conn, 'interval_executions'):
            busy = busy or bool(conn.execute('''SELECT 1 FROM interval_executions i JOIN executions e ON e.id=i.execution_id
                LEFT JOIN execution_settlements s ON s.execution_id=e.id
                WHERE e.task_id=? AND e.started_at IS NOT NULL AND COALESCE(s.cleanup_confirmed,0)=0''', (object_id,)).fetchone())
        completions = [dict(row) for row in conn.execute('''SELECT id,ended_at FROM executions
            WHERE task_id=? AND status NOT IN ('pending','running') AND ended_at IS NOT NULL
            ORDER BY id DESC LIMIT 1''', (object_id,))]
        if workflows._table(conn, 'remote_runs'):
            completions += [dict(row) for row in conn.execute('''SELECT id,finished_at AS ended_at FROM remote_runs
                WHERE task_id=? AND status NOT IN ('queued','preparing','running','stopping','uncertain')
                AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1''', (object_id,))]
        if workflows._table(conn, 'execution_settlements'):
            receipt = conn.execute('''SELECT MAX(s.updated_at) AS ended_at FROM execution_settlements s
                JOIN executions e ON e.id=s.execution_id WHERE e.task_id=? AND s.cleanup_confirmed=1''',
                (object_id,)).fetchone()
            if receipt and receipt['ended_at']:
                completions.append({'receipt': receipt['ended_at'], 'ended_at': receipt['ended_at']})
        if workflows._table(conn, 'maintenance_jobs'):
            job = conn.execute('SELECT MAX(ended_at) AS ended_at FROM maintenance_jobs WHERE task_id=?',
                               (object_id,)).fetchone()
            if job and job['ended_at']:
                completions.append({'maintenance': job['ended_at'], 'ended_at': job['ended_at']})
    else:
        busy = bool(conn.execute("SELECT 1 FROM workflow_runs WHERE workflow_id=? AND state IN ('waiting','running','stopping')",
                                 (object_id,)).fetchone())
        completions = [dict(row) for row in conn.execute('''SELECT id,ended_at FROM workflow_runs
            WHERE workflow_id=? AND state NOT IN ('waiting','running','stopping') AND ended_at IS NOT NULL
            ORDER BY id DESC LIMIT 1''', (object_id,))]
    key = json.dumps(completions, sort_keys=True)
    state = conn.execute('SELECT * FROM interval_schedule_state WHERE object_type=? AND object_id=?',
                         (object_type, object_id)).fetchone()
    if state is None:
        conn.execute('INSERT INTO interval_schedule_state VALUES(?,?,?,?)', (object_type, object_id, key, int(busy)))
    next_run = item['next_run_at']
    if busy:
        next_run = None
        conn.execute('UPDATE interval_schedule_state SET waiting=1 WHERE object_type=? AND object_id=?',
                     (object_type, object_id))
    elif state is not None and (state['waiting'] or state['completion_key'] != key):
        ended_at = max((row['ended_at'] for row in completions), default=now)
        next_run = compute_next_run('interval', json.loads(item['trigger_config']), ended_at)
        conn.execute('UPDATE interval_schedule_state SET completion_key=?,waiting=0 WHERE object_type=? AND object_id=?',
                     (key, object_type, object_id))
    elif next_run is None:
        next_run = compute_next_run('interval', json.loads(item['trigger_config']), now)
    conn.execute(f'UPDATE {table} SET next_run_at=? WHERE id=?', (next_run, object_id))
    return next_run


def scan_tasks(now=None):
    now = now or utc_now()
    from .host_dispatch import dispatch_scheduled_in_transaction
    # Each acceptance, execution snapshot and cleared countdown commit together.
    with transaction() as conn:
        ensure_schema(conn)
        ids = [row['id'] for row in conn.execute("SELECT id FROM tasks WHERE trigger_type='interval' AND enabled=1 AND archived=0 AND (target_host_id IS NOT NULL OR first_environment_ready=1)")]
    for task_id in ids:
        try:
            with transaction() as conn:
                task = conn.execute("SELECT * FROM tasks WHERE id=? AND trigger_type='interval' AND enabled=1 AND archived=0 AND (target_host_id IS NOT NULL OR first_environment_ready=1)", (task_id,)).fetchone()
                if task is None:
                    continue
                point = refresh(conn, 'task', task, now)
                if point is None or point > now:
                    continue
                conn.execute('UPDATE tasks SET last_triggered_at=? WHERE id=?', (point, task_id))
                dispatch_scheduled_in_transaction(conn, task_id)
                conn.execute('UPDATE tasks SET next_run_at=NULL WHERE id=?', (task_id,))
                conn.execute("UPDATE interval_schedule_state SET waiting=1 WHERE object_type='task' AND object_id=?", (task_id,))
        except Exception:
            logger.exception('任务 %s 间隔运行未能入队，保留原计划等待重试', task_id)
