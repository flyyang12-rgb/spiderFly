from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .database import append_execution_output, execute, fetch_one, utc_now
from .feishu import FeishuNotifier, capture_active_window_jpeg


FINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}


def validate_script(script_path: str, python_path: str = "") -> tuple[Path, str]:
    script = Path(script_path).expanduser().resolve()
    if script.suffix.lower() != ".py":
        raise ValueError("当前版本只支持 .py 文件")
    if not script.exists() or not script.is_file():
        raise FileNotFoundError(f"脚本不存在：{script}")

    interpreter = python_path.strip() or sys.executable
    interpreter_path = Path(interpreter).expanduser()
    if interpreter_path.is_absolute() and not interpreter_path.exists():
        raise FileNotFoundError(f"Python解释器不存在：{interpreter_path}")
    return script, str(interpreter_path if interpreter_path.is_absolute() else interpreter)


async def _consume_stream(
    stream: asyncio.StreamReader | None,
    execution_id: int,
    field: str,
) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.readline()
        if not chunk:
            break
        text = chunk.decode("utf-8", errors="replace")
        await asyncio.to_thread(append_execution_output, execution_id, field, text)


def _notification_enabled(task: dict[str, Any], status: str) -> bool:
    if status == "success":
        return bool(task.get("notify_on_success"))
    return bool(task.get("notify_on_failure"))


async def _send_notification(
    execution_id: int,
    task: dict[str, Any],
    status: str,
    duration_ms: int,
    error_summary: str,
) -> None:
    if not _notification_enabled(task, status):
        await asyncio.to_thread(
            execute,
            "UPDATE executions SET notification_status = 'disabled' WHERE id = ?",
            (execution_id,),
        )
        return

    notifier = FeishuNotifier()
    if not notifier.configured:
        await asyncio.to_thread(
            execute,
            """
            UPDATE executions
            SET notification_status = 'skipped', notification_error = '未配置飞书应用或收件人'
            WHERE id = ?
            """,
            (execution_id,),
        )
        return

    image_bytes = await asyncio.to_thread(capture_active_window_jpeg) if status != "success" else None
    try:
        await asyncio.to_thread(
            notifier.send_final_result,
            task_name=task["name"],
            status=status,
            duration_ms=duration_ms,
            error_summary=error_summary,
            image_bytes=image_bytes,
        )
        await asyncio.to_thread(
            execute,
            "UPDATE executions SET notification_status = 'sent', notification_error = '' WHERE id = ?",
            (execution_id,),
        )
    except Exception as exc:
        await asyncio.to_thread(
            execute,
            "UPDATE executions SET notification_status = 'failed', notification_error = ? WHERE id = ?",
            (str(exc)[:1000], execution_id),
        )


async def run_execution(execution_id: int, task_id: int) -> None:
    task = await asyncio.to_thread(fetch_one, "SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        return

    started = time.monotonic()
    await asyncio.to_thread(
        execute,
        "UPDATE executions SET status = 'running', started_at = ? WHERE id = ?",
        (utc_now(), execution_id),
    )
    await asyncio.to_thread(
        execute,
        "UPDATE tasks SET last_status = 'running', last_run_at = ?, updated_at = ? WHERE id = ?",
        (utc_now(), utc_now(), task_id),
    )

    status = "failed"
    exit_code: int | None = None
    error_message = ""
    try:
        script, interpreter = validate_script(task["script_path"], task.get("python_path") or "")
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            interpreter,
            "-u",
            str(script),
            cwd=str(script.parent),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        stdout_task = asyncio.create_task(_consume_stream(process.stdout, execution_id, "stdout"))
        stderr_task = asyncio.create_task(_consume_stream(process.stderr, execution_id, "stderr"))
        timeout = int(task.get("timeout_seconds") or 0)
        try:
            if timeout > 0:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            else:
                await process.wait()
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            status = "timeout"
            error_message = f"运行超过任务设置的 {timeout} 秒，已终止"
        await asyncio.gather(stdout_task, stderr_task)
        exit_code = process.returncode
        if status != "timeout":
            status = "success" if exit_code == 0 else "failed"
            if status == "failed":
                record = await asyncio.to_thread(
                    fetch_one,
                    "SELECT stdout, stderr FROM executions WHERE id = ?",
                    (execution_id,),
                )
                error_message = ((record or {}).get("stderr") or (record or {}).get("stdout") or f"退出码 {exit_code}").strip()
    except Exception as exc:
        error_message = str(exc)
        await asyncio.to_thread(append_execution_output, execution_id, "stderr", f"{exc}\n")

    duration_ms = int((time.monotonic() - started) * 1000)
    ended_at = utc_now()
    await asyncio.to_thread(
        execute,
        """
        UPDATE executions
        SET status = ?, ended_at = ?, duration_ms = ?, exit_code = ?, error_message = ?
        WHERE id = ?
        """,
        (status, ended_at, duration_ms, exit_code, error_message[:5000], execution_id),
    )
    await asyncio.to_thread(
        execute,
        "UPDATE tasks SET last_status = ?, last_run_at = ?, updated_at = ? WHERE id = ?",
        (status, ended_at, ended_at, task_id),
    )
    await _send_notification(execution_id, task, status, duration_ms, error_message)
