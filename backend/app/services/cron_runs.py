"""Durable single-task Cron rounds, including the existing repair lifecycle.

Connection-taking helpers never commit: the queue and trigger ledger share one
transaction. Terminal execution status alone is not proof that the host is free.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Callable, Iterator
from uuid import uuid4

from fastapi import HTTPException

from ..cron_schedule import cron_times, previous_cron_time
from ..database import transaction, utc_now

ACTIVE_STATES = ("pending", "active")
EXECUTION_TERMINAL = {"success", "failed", "timeout", "cancelled"}
MAINTENANCE_ACTIVE = {"pending", "generating", "ready", "testing"}
REMOTE_ACTIVE = ("queued", "preparing", "running", "stopping", "uncertain")


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def has_schema(conn: sqlite3.Connection) -> bool:
    return _table(conn, "cron_rounds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    # execute, rather than executescript, preserves an enclosing BEGIN IMMEDIATE.
    conn.execute("""CREATE TABLE IF NOT EXISTS cron_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
        task_name TEXT NOT NULL, source TEXT NOT NULL,
        request_key TEXT, request_intent TEXT NOT NULL DEFAULT '{}', requested_by INTEGER,
        scheduled_for TEXT, config TEXT NOT NULL, schedule_revision INTEGER NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('pending','active','settled','error','skipped')),
        execution_id INTEGER, maintenance_job_id INTEGER, rerun_execution_id INTEGER,
        maintenance_decision TEXT NOT NULL DEFAULT 'pending'
            CHECK(maintenance_decision IN ('pending','none','entered')),
        maintenance_reason TEXT NOT NULL DEFAULT '',
        maintenance_reason_code TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '', coalesced_from TEXT, coalesced_to TEXT,
        created_at TEXT NOT NULL, enqueued_at TEXT, ended_at TEXT,
        execution_status TEXT, rerun_status TEXT, final_status TEXT,
        execution_result TEXT, rerun_result TEXT,
        result_summary TEXT NOT NULL DEFAULT ''
    )""")
    columns = {item["name"] for item in conn.execute("PRAGMA table_info(cron_rounds)")}
    # A previously created ledger must remain usable after an application update.
    for name, definition in (
        ("enqueued_at", "TEXT"),
        ("execution_status", "TEXT"), ("rerun_status", "TEXT"), ("final_status", "TEXT"),
        ("execution_result", "TEXT"), ("rerun_result", "TEXT"),
        ("result_summary", "TEXT NOT NULL DEFAULT ''"),
        ("maintenance_reason", "TEXT NOT NULL DEFAULT ''"),
        ("maintenance_reason_code", "TEXT NOT NULL DEFAULT ''"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE cron_rounds ADD COLUMN {name} {definition}")
    for statement in (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cron_round_request ON cron_rounds(request_key) WHERE request_key IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cron_round_slot ON cron_rounds(task_id,scheduled_for) WHERE scheduled_for IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cron_round_active ON cron_rounds(task_id) WHERE state IN ('pending','active')",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_cron_round_execution ON cron_rounds(execution_id) WHERE execution_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS ix_cron_round_history ON cron_rounds(task_id,id DESC)",
    ):
        conn.execute(statement)
    # Only recover facts that are still present. Missing historical execution
    # rows are unknown, rather than an invented cancellation or success.
    if _table(conn, "executions"):
        for item in conn.execute("SELECT * FROM cron_rounds WHERE state='settled' AND final_status IS NULL").fetchall():
            row = dict(item)
            _store_result(conn, row, row["reason"])


@contextmanager
def _connection(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if conn is not None:
        yield conn
    else:
        with transaction() as opened:
            yield opened


def _time(value: str | datetime | None = None) -> datetime:
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    result = result or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("Cron 轮次时间必须带时区")
    return result.astimezone(timezone.utc)


def _iso(value: str | datetime | None = None) -> str:
    return _time(value).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _config(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("trigger_config") or "{}"
    trigger = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(trigger, dict):
        raise ValueError("Cron 配置必须为对象")
    return {"trigger_config": trigger, "task_version": int(task.get("version") or 1),
            "target_host_id": task.get("target_host_id"), "task_name": task["name"]}


def _get(conn: sqlite3.Connection, round_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM cron_rounds WHERE id=?", (round_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Cron 轮次不存在")
    return dict(row)


def active_round(conn: sqlite3.Connection, task_id: int) -> dict[str, Any] | None:
    if not has_schema(conn):
        return None
    row = conn.execute("SELECT * FROM cron_rounds WHERE task_id=? AND state IN ('pending','active') LIMIT 1", (task_id,)).fetchone()
    return dict(row) if row else None


def _busy_reason(conn: sqlite3.Connection, task_id: int, ignore_round: int | None = None) -> str:
    round_item = active_round(conn, task_id)
    if round_item and round_item["id"] != ignore_round:
        if round_item["maintenance_decision"] in {"pending", "entered"} and round_item["execution_id"]:
            return "上一轮未结束（结果收尾或自动维护尚未确认）"
        return "上一轮未结束，已跳过"
    if _table(conn, "executions") and conn.execute(
        "SELECT 1 FROM executions WHERE task_id=? AND status IN ('pending','running') LIMIT 1", (task_id,)
    ).fetchone():
        return "同任务已有本机排队或运行，等待结束"
    if _table(conn, "execution_settlements") and conn.execute(
        """SELECT 1 FROM executions e JOIN execution_settlements s ON s.execution_id=e.id
        WHERE e.task_id=? AND s.cleanup_confirmed=0 LIMIT 1""", (task_id,)
    ).fetchone():
        return "同任务进程或资源收尾尚未确认"
    if _table(conn, "remote_runs") and conn.execute(
        "SELECT 1 FROM remote_runs WHERE task_id=? AND status IN (?,?,?,?,?) LIMIT 1", (task_id, *REMOTE_ACTIVE)
    ).fetchone():
        return "同任务已有远程活动运行，等待结束"
    if _table(conn, "maintenance_jobs") and conn.execute(
        """SELECT 1 FROM maintenance_jobs WHERE task_id=? AND
        (status IN ('pending','generating','ready','testing') OR
        (status='activated' AND rerun_execution_id IS NOT NULL AND ended_at IS NULL)) LIMIT 1""", (task_id,)
    ).fetchone():
        return "同任务自动维护或修复重跑尚未结束"
    return ""


def _local_enabled(task: dict[str, Any] | None) -> None:
    if not task or task.get("archived"):
        raise HTTPException(404, "任务不存在")
    if not task.get("enabled"):
        raise HTTPException(409, "任务已停用")
    if task.get("target_host_id") is not None:
        raise HTTPException(409, "Cron 首版仅支持主控本机，任务已绑定远程电脑")


def _manual_identity(task_id: int, requested_by: int | None, request_id: str,
                     intent: Any = None) -> tuple[str, str]:
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise HTTPException(400, "请求标识必须为 1 到 200 字符")
    return _json([requested_by, task_id, "manual", request_id]), _json({
        "operation": "manual", "task_id": task_id, "requested_by": requested_by,
        "intent": {} if intent is None else intent,
    })


def find_manual(conn: sqlite3.Connection, task_id: int, requested_by: int | None,
                request_id: str | None, intent: Any = None) -> dict[str, Any] | None:
    if request_id is None or not has_schema(conn):
        return None
    key, encoded = _manual_identity(task_id, requested_by, request_id, intent)
    row = conn.execute("SELECT * FROM cron_rounds WHERE request_key=?", (key,)).fetchone()
    if not row:
        return None
    if row["request_intent"] != encoded:
        raise HTTPException(409, "请求标识已用于不同的运行意图")
    return dict(row)


def register_manual(conn: sqlite3.Connection, task: Any, requested_by: int | None,
                    request_id: str | None, intent: Any = None) -> dict[str, Any]:
    task_id = int(task["id"])
    old = find_manual(conn, task_id, requested_by, request_id, intent)
    if old:
        return old
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    current = dict(row) if row else None
    _local_enabled(current)
    if current["trigger_type"] != "cron":
        raise HTTPException(409, "任务未配置 Cron")
    reason = _busy_reason(conn, task_id)
    if reason:
        raise HTTPException(409, reason)
    key, encoded = _manual_identity(task_id, requested_by, request_id or uuid4().hex, intent)
    config = _config(current)
    cursor = conn.execute("""INSERT INTO cron_rounds
        (task_id,task_name,source,request_key,request_intent,requested_by,config,schedule_revision,state,created_at)
        VALUES (?,?,'manual',?,?,?,?,?,'pending',?)""",
        (task_id, current["name"], key, encoded, requested_by, _json(config), current["version"], utc_now()))
    return _get(conn, int(cursor.lastrowid))


def register_due(task_id: int, now: str | datetime | None = None,
                 expected_next_run: str | None = None, expected_version: int | None = None,
                 *, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    with _connection(conn) as opened:
        if not has_schema(opened):
            return None
        row = opened.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        task = dict(row)
        at = _time(now)
        if (task.get("archived") or not task.get("enabled") or task["trigger_type"] != "cron"
            or not task.get('first_environment_ready', 1)
            or (expected_version is not None and task["version"] != expected_version)
            or (expected_next_run is not None and task["next_run_at"] != expected_next_run)
            or not task.get("next_run_at") or _time(task["next_run_at"]) > at):
            return None
        config = _config(task)
        expression = config["trigger_config"]["expression"]
        first = _time(task["next_run_at"])
        scheduled = max(first, previous_cron_time(expression, at))
        slot, timestamp = _iso(scheduled), _iso(at)
        old = opened.execute("SELECT * FROM cron_rounds WHERE task_id=? AND scheduled_for=?", (task_id, slot)).fetchone()
        reason = _busy_reason(opened, task_id)
        state = "skipped" if reason else "pending"
        if task.get("target_host_id") is not None:
            state, reason = "error", "Cron 首版仅支持主控本机，任务已绑定远程电脑"
        coalesced = first < scheduled
        if not old:
            cursor = opened.execute("""INSERT INTO cron_rounds
                (task_id,task_name,source,scheduled_for,config,schedule_revision,state,
                 maintenance_decision,reason,coalesced_from,coalesced_to,created_at,ended_at)
                VALUES (?,?,'schedule',?,?,?,?,?,?,?,?,?,?)""",
                (task_id, task["name"], slot, _json(config), task["version"], state,
                 "pending" if state == "pending" else "none", reason,
                 _iso(first) if coalesced else None, slot if coalesced else None,
                 timestamp, timestamp if state != "pending" else None))
            result = _get(opened, int(cursor.lastrowid))
        else:
            result = dict(old)
        next_run = _iso(cron_times(expression, at, 1)[0])
        opened.execute("UPDATE tasks SET next_run_at=?,last_triggered_at=?,updated_at=? WHERE id=?",
                       (next_run, slot, timestamp, task_id))
        return result


def attach_execution(conn: sqlite3.Connection, round_id: int, execution_id: int) -> dict[str, Any]:
    row = _get(conn, round_id)
    if row["execution_id"] is not None:
        if row["execution_id"] != execution_id:
            raise HTTPException(409, "该轮次已关联另一执行")
        return row
    if row["state"] != "pending":
        raise HTTPException(409, "该轮次不能入队")
    execution = conn.execute("SELECT task_id FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not execution or execution["task_id"] != row["task_id"]:
        raise HTTPException(409, "执行与 Cron 轮次任务不匹配")
    conn.execute("UPDATE cron_rounds SET state='active',execution_id=?,enqueued_at=?,reason='' WHERE id=?",
                 (execution_id, utc_now(), round_id))
    return _get(conn, round_id)


def dispatch_pending(enqueue_in_transaction: Callable[..., int], *,
                     conn: sqlite3.Connection | None = None) -> int:
    with _connection(conn) as opened:
        if not has_schema(opened):
            return 0
        count = 0
        for item in opened.execute("SELECT * FROM cron_rounds WHERE state='pending' ORDER BY id").fetchall():
            row = dict(item)
            task = opened.execute("SELECT * FROM tasks WHERE id=?", (row["task_id"],)).fetchone()
            try:
                _local_enabled(dict(task) if task else None)
            except HTTPException as error:
                opened.execute("UPDATE cron_rounds SET state='error',maintenance_decision='none',reason=?,ended_at=? WHERE id=?",
                               (str(error.detail), utc_now(), row["id"]))
                continue
            reason = _busy_reason(opened, row["task_id"], row["id"])
            if reason:
                opened.execute("UPDATE cron_rounds SET reason=? WHERE id=?", (reason, row["id"]))
                continue
            # Roll back partial enqueue writes, retaining the accepted pending round.
            opened.execute("SAVEPOINT cron_dispatch")
            try:
                execution_id = enqueue_in_transaction(row["task_id"], row["source"], row["requested_by"], opened, row["id"])
                attach_execution(opened, row["id"], execution_id)
            except Exception as error:
                opened.execute("ROLLBACK TO cron_dispatch")
                opened.execute("RELEASE cron_dispatch")
                permanent = isinstance(error, (ValueError, FileNotFoundError)) or (
                    isinstance(error, HTTPException) and error.status_code < 500)
                detail = str(error.detail) if isinstance(error, HTTPException) else str(error)
                opened.execute("UPDATE cron_rounds SET state=?,reason=?,ended_at=?,maintenance_decision=? WHERE id=?",
                    ("error" if permanent else "pending", detail[:2000] or type(error).__name__,
                     utc_now() if permanent else None, "none" if permanent else "pending", row["id"]))
                continue
            opened.execute("RELEASE cron_dispatch")
            count += 1
        return count


def decide_maintenance(execution_id: int, *, conn: sqlite3.Connection | None = None,
                       reason: str | None = None, reason_code: str | None = None) -> None:
    """Call after record_failure (even on failure) to persist its observed decision.

On startup a pending decision can be reconciled by inspecting already-created
jobs; this helper never invokes the model or creates a repair itself.
"""
    with _connection(conn) as opened:
        if not has_schema(opened):
            return
        record = opened.execute("SELECT * FROM cron_rounds WHERE execution_id=? AND state='active'",
                                (execution_id,)).fetchone()
        if not record:
            return
        job = opened.execute("SELECT * FROM maintenance_jobs WHERE execution_id=?", (execution_id,)).fetchone() if _table(opened, "maintenance_jobs") else None
        # Reconciliation must retain the original observation, even if policy or
        # history later changes. A missing observed job is not a new 'none'.
        if not job and record['maintenance_decision'] == 'entered':
            return
        if not job and record['maintenance_decision'] == 'none' and record['maintenance_reason']:
            return
        if not job and not reason and record['maintenance_decision'] == 'pending':
            execution = opened.execute('SELECT status FROM executions WHERE id=?', (execution_id,)).fetchone()
            if not execution or execution['status'] not in {'success', 'cancelled'}:
                # record_failure did not commit a decision. An exception is not
                # proof that policy rejected repair; keep ownership until recovery.
                return
        if not reason:
            reason = record['maintenance_reason'] or (
                '已登记自动维护，修复及重跑结果单独记录' if job else '未登记自动维护，未取得具体决定原因')
        if not reason_code:
            reason_code = record['maintenance_reason_code'] or ('queued' if job else 'unrecorded_reason')
        opened.execute("""UPDATE cron_rounds SET maintenance_decision=?,maintenance_job_id=?,rerun_execution_id=?,
            maintenance_reason=?,maintenance_reason_code=?
            WHERE execution_id=? AND state='active'""",
            ("entered" if job else "none", job["id"] if job else None,
             job["rerun_execution_id"] if job else None, reason[:2000], reason_code[:80], execution_id))


def _cleanup_confirmed(conn: sqlite3.Connection, execution_id: int) -> bool:
    from ..native_process import cleanup_confirmed
    return cleanup_confirmed(conn, execution_id)


def _execution_result(conn: sqlite3.Connection, execution_id: int | None) -> dict[str, Any] | None:
    if execution_id is None or not _table(conn, "executions"):
        return None
    item = conn.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
    if not item or item["status"] not in EXECUTION_TERMINAL:
        return None
    source = dict(item)
    result = {"id": execution_id, "status": source["status"]}
    for name in ("result_source", "business_outcome", "result_code", "exit_code", "ended_at", "duration_ms"):
        if name in source:
            result[name] = source[name]
    # Retain the existing result/error summary, never the full output, source,
    # repair snapshot, environment or traceback buffers.
    for name in ("result_message", "error_message"):
        if name in source:
            result[name] = str(source[name] or "")[:1000]
    return result


def _store_result(conn: sqlite3.Connection, row: dict[str, Any], reason: str) -> None:
    original = _execution_result(conn, row["execution_id"]) if not row.get("execution_status") else None
    rerun = _execution_result(conn, row["rerun_execution_id"]) if not row.get("rerun_status") else None
    original_status = row.get("execution_status") or (original["status"] if original else None)
    rerun_status = row.get("rerun_status") or (rerun["status"] if rerun else None)
    final_status = row.get("final_status") or (rerun_status if row["rerun_execution_id"] is not None else original_status)
    summary = row.get("result_summary") or reason or "历史执行资料已删除，最终状态未核实"
    conn.execute("""UPDATE cron_rounds SET execution_status=?,rerun_status=?,final_status=?,
        execution_result=?,rerun_result=?,result_summary=? WHERE id=?""",
        (original_status, rerun_status, final_status,
         row.get("execution_result") or (_json(original) if original else None),
         row.get("rerun_result") or (_json(rerun) if rerun else None), summary, row["id"]))


def _finish(conn: sqlite3.Connection, row: dict[str, Any], at: str, reason: str) -> None:
    # Account for every missed interval while the round still owns the task.
    # A delayed maintenance callback must not release, then replay its last slot.
    register_due(row["task_id"], at, conn=conn)
    # Re-read the ledger so a newly observed repair execution is also frozen.
    # These facts survive task, execution and maintenance history deletion.
    _store_result(conn, _get(conn, row["id"]), reason)
    conn.execute("UPDATE cron_rounds SET state='settled',reason=?,ended_at=? WHERE id=? AND state='active'",
                 (reason, at, row["id"]))


def reconcile(conn: sqlite3.Connection | None = None, now: str | datetime | None = None,
              *, recover: bool = False, recover_decisions: bool = False) -> int:
    """Close only confirmed rounds; startup may settle interrupted decisions.

recover_decisions is only for the service startup boundary, after maintenance
initialization: inspecting persisted jobs must not rerun an interrupted model.
"""
    with _connection(conn) as opened:
        if not has_schema(opened):
            return 0
        at, closed = _iso(now), 0
        for item in opened.execute("SELECT * FROM cron_rounds WHERE state='active' ORDER BY id").fetchall():
            row = dict(item)
            execution = opened.execute("SELECT * FROM executions WHERE id=?", (row["execution_id"],)).fetchone()
            if not execution:
                opened.execute("UPDATE cron_rounds SET reason='关联执行记录缺失，收尾未确认' WHERE id=?", (row["id"],))
                continue
            if execution["status"] not in EXECUTION_TERMINAL or not _cleanup_confirmed(opened, execution["id"]):
                continue
            if row["maintenance_decision"] == "pending":
                if execution["status"] in {"success", "cancelled"} or recover or recover_decisions:
                    reason, code = (
                        ('执行成功，无需自动维护', 'execution_succeeded') if execution['status'] == 'success' else
                        ('执行已取消，不进入自动维护', 'execution_cancelled') if execution['status'] == 'cancelled' else
                        ('维护决定中断，恢复只核对原关联，未自动重发模型调用', 'decision_interrupted'))
                    if _table(opened, 'maintenance_jobs') and opened.execute(
                        'SELECT 1 FROM maintenance_jobs WHERE execution_id=?', (execution['id'],)
                    ).fetchone():
                        reason, code = '已登记自动维护，恢复继续核对原维护及重跑关联', 'queued'
                    decide_maintenance(execution["id"], conn=opened, reason=reason, reason_code=code)
                    row = _get(opened, row["id"])
                    if execution["status"] in {"failed", "timeout"} and row["maintenance_decision"] == "none":
                        row["reason"] = "恢复中断的维护决定，未自动重发模型调用"
                else:
                    continue
            if row["maintenance_decision"] == "none":
                _finish(opened, row, at, (row["reason"] + "；" if row["reason"] else "") + "执行已完成并确认收尾：" + execution["status"])
                closed += 1
                continue
            job = opened.execute("SELECT * FROM maintenance_jobs WHERE id=?", (row["maintenance_job_id"],)).fetchone() if _table(opened, "maintenance_jobs") else None
            if not job:
                opened.execute("UPDATE cron_rounds SET reason='关联维护记录缺失，轮次结束未确认' WHERE id=?", (row["id"],))
                continue
            rerun_id = job["rerun_execution_id"]
            if rerun_id != row["rerun_execution_id"]:
                opened.execute("UPDATE cron_rounds SET rerun_execution_id=? WHERE id=?", (rerun_id, row["id"]))
            if job["status"] in MAINTENANCE_ACTIVE:
                continue
            if rerun_id is not None:
                rerun = opened.execute("SELECT status FROM executions WHERE id=?", (rerun_id,)).fetchone()
                if not rerun or rerun["status"] not in EXECUTION_TERMINAL or not _cleanup_confirmed(opened, rerun_id):
                    continue
            elif not job["ended_at"]:
                continue
            _finish(opened, row, at, "自动维护链路已结束并确认收尾：" + job["status"])
            closed += 1
        return closed
