"""持久队列的入队、领取与串行执行。"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from fastapi import HTTPException
from .. import maintenance, task_versions
from ..config import HOST_CHECK_INTERVAL_SECONDS, WORK_DIR
from ..database import TASK_EXECUTION_STATUS_SQL, execute, fetch_one, transaction, utc_now
from ..environments import managed_runtime_paths
from ..host_runtime import HostRuntimeError, check_host_busy, clear_work_directory
from ..runner import run_execution


logger = logging.getLogger(__name__)


def _enqueue_task_sync(
    task_id: int, source: str = "manual", requested_by: int | None = None
) -> int:
    now = utc_now()
    try:
        with transaction() as conn:
            task = conn.execute(
                """
                SELECT
                    t.id, t.enabled, t.archived, t.app_id, t.target_host_id,
                    a.id AS current_app_id, a.name AS app_name,
                    a.script_path, a.env_path, a.environment_status,
                    a.archived AS app_archived
                FROM tasks t
                LEFT JOIN rpa_apps a ON a.id = t.app_id
                WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
            if not task or task["archived"]:
                raise HTTPException(status_code=404, detail="任务不存在")
            if not task["enabled"]:
                raise HTTPException(status_code=409, detail="任务已停用")
            if source == "manual" and task["target_host_id"] is not None:
                raise HTTPException(status_code=409, detail="任务已绑定远程电脑，不能在主控本机运行；请刷新后重试")
            if not task["current_app_id"] or task["app_archived"]:
                raise HTTPException(
                    status_code=409, detail="自动化程序不存在或已经移除"
                )
            app_item = dict(task)
            try:
                script, python_path = managed_runtime_paths(app_item)
            except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                if task["environment_status"] != "ready":
                    detail = "Python 环境尚未准备好"
                else:
                    detail = str(exc)
                raise HTTPException(status_code=409, detail=detail) from exc
            running = conn.execute(
                """
                SELECT id FROM executions
                WHERE task_id = ? AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if running and source != "schedule":
                raise HTTPException(status_code=409, detail="任务已经在排队或运行")
            cursor = conn.execute(
                """
                INSERT INTO executions (
                    task_id, status, trigger_source, requested_by,
                    script_path_snapshot, python_path_snapshot, created_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    source,
                    requested_by,
                    str(script),
                    str(python_path),
                    now,
                ),
            )
            conn.execute('UPDATE executions SET maintenance_snapshot=? WHERE id=?',
                         (maintenance.capture_snapshot(conn, task_id), cursor.lastrowid))
            conn.execute(
                f"""
                UPDATE tasks
                SET last_status = {TASK_EXECUTION_STATUS_SQL}, app_name = ?, script_path = ?,
                    python_path = ?, updated_at = ?
                WHERE id = ? AND archived = 0 AND enabled = 1
                """,
                ("pending", app_item["app_name"], str(script), str(python_path), now, task_id),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务已经在排队或运行") from exc


async def _enqueue_task(
    task_id: int, source: str = "manual", requested_by: int | None = None
) -> int:
    return await asyncio.to_thread(_enqueue_task_sync, task_id, source, requested_by)


def _next_pending_execution_id() -> int | None:
    item = fetch_one(
        "SELECT id FROM executions WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
    )
    return int(item["id"]) if item else None


def _set_execution_waiting(execution_id: int, message: str) -> None:
    execute(
        """
        UPDATE executions
        SET error_message = ?
        WHERE id = ? AND status = 'pending' AND error_message != ?
        """,
        (message[:1000], execution_id, message[:1000]),
    )


def _host_waiting_reason() -> str:
    busy = check_host_busy()
    if busy.busy:
        return f"等待宿主机空闲：{busy.message}"
    try:
        clear_work_directory(WORK_DIR)
    except HostRuntimeError as exc:
        return f"等待公共工作文件夹可用：{exc}"
    return ""


def _claim_next_execution() -> int | None:
    with transaction() as conn:
        row = conn.execute(
            "SELECT id, task_id FROM executions WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        now = utc_now()
        cursor = conn.execute(
            """
            UPDATE executions
            SET status = 'running', started_at = NULL, error_message = ''
            WHERE id = ? AND status = 'pending'
            """,
            (row["id"],),
        )
        if cursor.rowcount != 1:
            return None
        conn.execute(
            "UPDATE tasks SET last_status = 'running', updated_at = ? WHERE id = ?",
            (now, row["task_id"]),
        )
        return int(row["id"])


async def _queue_worker_loop() -> None:
    while True:
        # Finish a ready repair at the serial boundary before starting queued code.
        try:
            await asyncio.to_thread(maintenance.reconcile_reruns)
            await asyncio.to_thread(task_versions.notify_next)
            from . import host_dispatch
            await asyncio.to_thread(host_dispatch.notify_next)
            if await maintenance.run_next_trial():
                continue
            if await task_versions.run_next():
                continue
            from ..collection import run_next_trial as run_collection_trial
            if await run_collection_trial():
                continue
            from ..dp_probe import run_next as run_dp_probe
            if await run_dp_probe():
                continue
        except asyncio.CancelledError: raise
        except Exception:
            logger.exception('读取自动维护试跑队列失败')
        pending_id = await asyncio.to_thread(_next_pending_execution_id)
        if pending_id is None:
            await asyncio.sleep(0.5)
            continue
        waiting_reason = await asyncio.to_thread(_host_waiting_reason)
        if waiting_reason:
            await asyncio.to_thread(_set_execution_waiting, pending_id, waiting_reason)
            await asyncio.sleep(HOST_CHECK_INTERVAL_SECONDS)
            continue
        execution_id = await asyncio.to_thread(_claim_next_execution)
        if execution_id is None:
            await asyncio.sleep(0.5)
            continue
        try:
            await run_execution(execution_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("执行记录 %s 发生未处理异常", execution_id)
            try:
                await asyncio.to_thread(
                    _mark_execution_worker_failure, execution_id, str(exc)
                )
            except Exception:
                logger.exception("执行记录 %s 的兜底失败状态写入失败", execution_id)
            await asyncio.sleep(0.1)

        try:
            await asyncio.to_thread(maintenance.record_failure, execution_id)
        except Exception:
            logger.exception('执行记录 %s 的自动维护入队失败', execution_id)


def _mark_execution_worker_failure(execution_id: int, message: str) -> None:
    """Keep one unexpected execution failure from stopping the serial queue."""
    now = utc_now()
    with transaction() as conn:
        item = conn.execute(
            "SELECT task_id, status FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        if not item or item["status"] not in {"pending", "running"}:
            return
        summary = (message.strip() or "SpiderFly 执行器发生内部错误")[:5000]
        conn.execute(
            """
            UPDATE executions
            SET status = 'failed', ended_at = ?, error_message = ?
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (now, summary, execution_id),
        )
        conn.execute(
            f"UPDATE tasks SET last_status = {TASK_EXECUTION_STATUS_SQL}, last_run_at = ?, updated_at = ? WHERE id = ?",
            ("failed", now, now, item["task_id"]),
        )
