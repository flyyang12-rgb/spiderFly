from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    MANAGED_BROWSER_PORT,
    RPA_APPS_DIR,
    RPA_ENVS_DIR,
    WORK_DIR,
)
from .database import append_execution_output, execute, fetch_one, utc_now
from .execution_results import (
    ExecutionWorkspace,
    ResolvedOutcome,
    create_execution_workspace,
    resolve_execution_outcome,
)
from .feishu import FeishuNotifier
from .host_runtime import cleanup_after_run, prepare_work_directory


FINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}
PROCESS_TERMINATION_SECONDS = 8
STREAM_DRAIN_SECONDS = 5
logger = logging.getLogger(__name__)


def validate_script(script_path: str, python_path: str) -> tuple[Path, str]:
    script = Path(script_path).expanduser().resolve()
    interpreter = Path(python_path).expanduser().resolve()
    try:
        script.relative_to(RPA_APPS_DIR.resolve())
        interpreter.relative_to(RPA_ENVS_DIR.resolve())
    except ValueError as exc:
        raise ValueError("脚本或 Python 环境不在 SpiderFly 管理目录内") from exc
    if script.suffix.lower() != ".py":
        raise ValueError("当前版本只支持 .py 文件")
    if not script.is_file():
        raise FileNotFoundError(f"脚本不存在：{script}")
    if not interpreter.is_file():
        raise FileNotFoundError(f"Python 环境不存在：{interpreter}")
    return script, str(interpreter)


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


def _notification_summary(outcome: ResolvedOutcome) -> str:
    """Never hide a process/runtime failure behind a script-authored message."""
    return outcome.error_message or outcome.result_message


async def _send_notification(
    execution_id: int,
    task: dict[str, Any],
    status: str,
    duration_ms: int,
    error_summary: str,
    result_code: str = "",
    manual_action_url: str = "",
    manual_code: str = "",
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

    try:
        await asyncio.to_thread(
            notifier.send_final_result,
            task_name=task["name"],
            status=status,
            duration_ms=duration_ms,
            error_summary=error_summary,
            result_code=result_code,
            manual_action_url=manual_action_url,
            manual_code=manual_code,
            image_bytes=None,
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


def _runtime_environment(
    workspace: ExecutionWorkspace | None = None,
    execution_id: int | None = None,
    work_dir: Path | None = None,
    template_file: Path | None = None,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("SPIDERFLY_", "FEISHU_"))
    }
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONUTF8"] = "1"
    for variable in ("NO_PROXY", "no_proxy"):
        entries = [
            item.strip()
            for item in environment.get(variable, "").split(",")
            if item.strip()
        ]
        lowered = {item.casefold() for item in entries}
        entries.extend(
            item
            for item in ("127.0.0.1", "localhost")
            if item.casefold() not in lowered
        )
        environment[variable] = ",".join(entries)
    environment["SPIDERFLY_BROWSER_PORT"] = str(MANAGED_BROWSER_PORT)
    if workspace is not None and execution_id is not None:
        environment.update(workspace.environment(execution_id))
    if work_dir is not None:
        environment["SPIDERFLY_WORK_DIR"] = str(work_dir)
        environment["SPIDERFLY_BROWSER_PROFILE_DIR"] = str(
            Path(work_dir) / f".spiderfly-browser-{MANAGED_BROWSER_PORT}"
        )
    if template_file is not None:
        environment["SPIDERFLY_TEMPLATE_FILE"] = str(template_file)
    return environment


async def _cleanup_public_work_directory(execution_id: int) -> str:
    try:
        await asyncio.to_thread(cleanup_after_run, WORK_DIR)
        return ""
    except Exception as exc:
        message = f"公共工作文件夹清理失败：{exc}"
        await asyncio.to_thread(
            append_execution_output, execution_id, "stderr", f"{message}\n"
        )
        return message


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer: asyncio.subprocess.Process | None = None
        try:
            killer = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                ),
                timeout=3,
            )
            await asyncio.wait_for(killer.wait(), timeout=PROCESS_TERMINATION_SECONDS)
        except (OSError, asyncio.TimeoutError):
            if killer and killer.returncode is None:
                try:
                    killer.kill()
                except (OSError, ProcessLookupError):
                    pass
    else:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
    try:
        await asyncio.wait_for(process.wait(), timeout=PROCESS_TERMINATION_SECONDS)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            logger.error("任务进程 PID %s 在终止预算内未确认退出", process.pid)


async def _finish_stream_tasks(*tasks: asyncio.Task | None) -> None:
    active = [task for task in tasks if task is not None]
    if not active:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*active, return_exceptions=True), timeout=STREAM_DRAIN_SECONDS
        )
    except asyncio.TimeoutError:
        for task in active:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*active, return_exceptions=True), timeout=2
            )
        except asyncio.TimeoutError:
            logger.error("任务输出管道未在收尾预算内关闭")


async def _finalize(
    execution_id: int,
    task_id: int,
    outcome: ResolvedOutcome,
    duration_ms: int,
    exit_code: int | None,
) -> None:
    ended_at = utc_now()
    await asyncio.to_thread(
        execute,
        """
        UPDATE executions
        SET status = ?, ended_at = ?, duration_ms = ?, exit_code = ?, error_message = ?,
            result_source = ?, business_outcome = ?, result_code = ?, result_message = ?,
            retryable = ?, manual_action_url = ?, manual_code = ?
        WHERE id = ?
        """,
        (
            outcome.status,
            ended_at,
            duration_ms,
            exit_code,
            outcome.error_message[:5000],
            outcome.result_source,
            outcome.business_outcome,
            outcome.result_code,
            outcome.result_message[:1000],
            int(outcome.retryable) if outcome.retryable is not None else None,
            outcome.manual_action_url[:2000],
            outcome.manual_code[:200],
            execution_id,
        ),
    )
    await asyncio.to_thread(
        execute,
        "UPDATE tasks SET last_status = ?, last_run_at = ?, updated_at = ? WHERE id = ?",
        (outcome.status, ended_at, ended_at, task_id),
    )


async def run_execution(execution_id: int) -> None:
    task = await asyncio.to_thread(
        fetch_one,
        """
        SELECT t.*, e.script_path_snapshot, e.python_path_snapshot,
               a.template_filename, a.template_path
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        LEFT JOIN rpa_apps a ON a.id = t.app_id
        WHERE e.id = ?
        """,
        (execution_id,),
    )
    if not task:
        return
    task_id = int(task["id"])
    started = time.monotonic()

    status = "failed"
    exit_code: int | None = None
    error_message = ""
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    workspace: ExecutionWorkspace | None = None
    public_work_dir: Path | None = None
    staged_template: Path | None = None
    try:
        workspace = await asyncio.to_thread(create_execution_workspace, execution_id)
        script, interpreter = validate_script(
            task["script_path_snapshot"], task["python_path_snapshot"]
        )
        public_work_dir, staged_template = await asyncio.to_thread(
            prepare_work_directory,
            WORK_DIR,
            template_path=task.get("template_path") or None,
            template_name=task.get("template_filename") or None,
        )
        started = time.monotonic()
        started_at = utc_now()
        await asyncio.to_thread(
            execute,
            "UPDATE executions SET status = 'running', started_at = ? WHERE id = ?",
            (started_at, execution_id),
        )
        await asyncio.to_thread(
            execute,
            """
            UPDATE tasks
            SET last_status = 'running', last_run_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (started_at, started_at, task_id),
        )
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = await asyncio.create_subprocess_exec(
            interpreter,
            "-u",
            str(script),
            cwd=str(script.parent),
            env=_runtime_environment(
                workspace,
                execution_id,
                public_work_dir,
                staged_template,
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )
        stdout_task = asyncio.create_task(_consume_stream(process.stdout, execution_id, "stdout"))
        stderr_task = asyncio.create_task(_consume_stream(process.stderr, execution_id, "stderr"))
        timeout = DEFAULT_TASK_TIMEOUT_SECONDS
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate_process(process)
            status = "timeout"
            error_message = f"运行超过任务设置的 {timeout} 秒，已终止"
        await _finish_stream_tasks(stdout_task, stderr_task)
        exit_code = process.returncode
        if status != "timeout":
            status = "success" if exit_code == 0 else "failed"
            if status == "failed":
                record = await asyncio.to_thread(
                    fetch_one,
                    "SELECT stdout, stderr FROM executions WHERE id = ?",
                    (execution_id,),
                )
                error_message = (
                    (record or {}).get("stderr")
                    or (record or {}).get("stdout")
                    or f"退出码 {exit_code}"
                ).strip()
    except asyncio.CancelledError:
        if process:
            await _terminate_process(process)
        await _finish_stream_tasks(stdout_task, stderr_task)
        duration_ms = int((time.monotonic() - started) * 1000)
        cancelled_outcome = ResolvedOutcome(
            status="cancelled",
            error_message="SpiderFly 服务停止，运行已取消",
        )
        cleanup_error = await _cleanup_public_work_directory(execution_id)
        if cleanup_error:
            cancelled_outcome = ResolvedOutcome(
                status="cancelled",
                error_message=f"{cancelled_outcome.error_message}；{cleanup_error}",
            )
        await _finalize(
            execution_id,
            task_id,
            cancelled_outcome,
            duration_ms,
            process.returncode if process else None,
        )
        raise
    except Exception as exc:
        if process and process.returncode is None:
            await _terminate_process(process)
        await _finish_stream_tasks(stdout_task, stderr_task)
        if process:
            exit_code = process.returncode
        error_message = str(exc)
        await asyncio.to_thread(append_execution_output, execution_id, "stderr", f"{exc}\n")

    cleanup_error = await _cleanup_public_work_directory(execution_id)
    if cleanup_error:
        if status == "success":
            status = "failed"
        error_message = "；".join(
            item for item in (error_message.strip(), cleanup_error) if item
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    outcome = await asyncio.to_thread(
        resolve_execution_outcome,
        process_status=status,
        exit_code=exit_code,
        legacy_error=error_message,
        result_file=workspace.result_file if workspace else None,
    )
    if outcome.result_code == "RESULT_INVALID":
        await asyncio.to_thread(
            append_execution_output,
            execution_id,
            "stderr",
            f"{outcome.result_message}\n",
        )
    await _finalize(execution_id, task_id, outcome, duration_ms, exit_code)
    await _send_notification(
        execution_id,
        task,
        outcome.status,
        duration_ms,
        _notification_summary(outcome),
        outcome.result_code,
        outcome.manual_action_url,
        outcome.manual_code,
    )
