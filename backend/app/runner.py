from __future__ import annotations

import asyncio
import codecs
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    MANAGED_BROWSER_PORT,
    RPA_APPS_DIR,
    RPA_ENVS_DIR,
    WORK_DIR,
)
from .database import TASK_EXECUTION_STATUS_SQL, append_execution_output, execute, fetch_one, utc_now
from .execution_results import (
    ExecutionWorkspace,
    ResolvedOutcome,
    create_execution_workspace,
    resolve_execution_outcome,
)
from .feishu import FeishuNotifier, capture_active_window_jpeg
from .host_runtime import cleanup_after_run, prepare_work_directory


FINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}
PROCESS_TERMINATION_SECONDS = 8
STREAM_DRAIN_SECONDS = 5
logger = logging.getLogger(__name__)


@dataclass
class ExecutionControl:
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    reason: str = ""
    accepting: bool = True


_execution_controls: dict[int, ExecutionControl] = {}


class ForceStopRequested(Exception):
    pass


def request_execution_stop(execution_id: int, actor: str) -> bool:
    """Called on the event loop; the runner owns all process termination and cleanup."""
    control = _execution_controls.get(execution_id)
    if control and control.stop_event.is_set():
        return False
    if not control or not control.accepting:
        raise ValueError("任务已结束或正在收尾，请刷新运行记录")
    control.reason = f"由管理员 {actor} 强制停止"
    control.stop_event.set()
    return True


def execution_stop_requested(execution_id: int) -> bool:
    control = _execution_controls.get(execution_id)
    return bool(control and control.stop_event.is_set())


def _check_stop(control: ExecutionControl) -> None:
    if control.stop_event.is_set():
        raise ForceStopRequested(control.reason)


async def _wait_for_process(process, control: ExecutionControl, timeout: float) -> None:
    process_wait = asyncio.create_task(process.wait())
    stop_wait = asyncio.create_task(control.stop_event.wait())
    try:
        done, _ = await asyncio.wait({process_wait, stop_wait}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        # An accepted stop wins even if the process exits in the same event-loop turn.
        _check_stop(control)
        if process_wait not in done:
            raise asyncio.TimeoutError
    finally:
        control.accepting = False
        for waiter in (process_wait, stop_wait):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(process_wait, stop_wait, return_exceptions=True)


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
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    while True:
        # A script may print a whole JSON response without any newline.
        chunk = await stream.read(16 * 1024)
        if not chunk:
            break
        text = decoder.decode(chunk)
        if text:
            await asyncio.to_thread(append_execution_output, execution_id, field, text)
    tail = decoder.decode(b"", final=True)
    if tail:
        await asyncio.to_thread(append_execution_output, execution_id, field, tail)


def _notification_enabled(task: dict[str, Any], status: str) -> bool:
    if status == "success":
        return bool(task.get("notify_on_success"))
    return bool(task.get("notify_on_failure"))


def _notification_summary(outcome: ResolvedOutcome) -> str:
    """Never hide a process/runtime failure behind a script-authored message."""
    return outcome.error_message or outcome.result_message


async def _capture_failure_image(
    execution_id: int, task: dict[str, Any], workspace: ExecutionWorkspace | None,
) -> tuple[bytes | None, str]:
    if not (task.get("notify_on_failure") and task.get("failure_screenshot")):
        return None, ""
    try:
        image = await asyncio.to_thread(capture_active_window_jpeg)
    except Exception:
        image = None
    note = ""
    if image is None:
        note = "未能获取前台窗口截图，请查看错误日志。"
    elif workspace is not None:
        try:
            destination = workspace.artifacts_dir / f"failure-{uuid.uuid4().hex}.jpg"
            # A new filename never overwrites a script's output or follows its file symlink.
            def save() -> None:
                if workspace.artifacts_dir.is_symlink() or workspace.artifacts_dir.resolve().parent != workspace.root.resolve():
                    raise OSError("产物目录无效")
                with destination.open("xb") as stream:
                    stream.write(image)
            await asyncio.to_thread(save)
        except Exception:
            note = "截图未能保存到本次文件，仍尝试附图通知。"
    if note:
        await asyncio.to_thread(append_execution_output, execution_id, "stderr", f"截图提示：{note}\n")
    return image, note


async def _send_notification(
    execution_id: int,
    task: dict[str, Any],
    status: str,
    duration_ms: int,
    error_summary: str,
    result_code: str = "",
    manual_action_url: str = "",
    manual_code: str = "",
    image_bytes: bytes | None = None,
    screenshot_note: str = "",
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
            SET notification_status = 'skipped', notification_error = '未配置飞书群 Webhook 或应用收件人'
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
            image_bytes=image_bytes,
            screenshot_note=screenshot_note,
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
        if not key.upper().startswith(("SPIDERFLY_", "FEISHU_", "DEEPSEEK_"))
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


async def _stop_process_confirmed(process, execution_id: int) -> None:
    await _terminate_process(process)
    if process.returncode is None:
        await asyncio.to_thread(append_execution_output, execution_id, "stderr", "强制停止尚未确认进程退出，队列继续等待。\n")
        # Never release the serial worker while this process is still alive.
        await process.wait()


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
        f"UPDATE tasks SET last_status = {TASK_EXECUTION_STATUS_SQL}, last_run_at = ?, updated_at = ? WHERE id = ?",
        (outcome.status, ended_at, ended_at, task_id),
    )


async def run_execution(execution_id: int) -> None:
    if execution_id in _execution_controls:
        raise RuntimeError("同一执行记录已由执行器接管")
    control = ExecutionControl()
    _execution_controls[execution_id] = control
    try:
        await _run_execution(execution_id, control)
    finally:
        _execution_controls.pop(execution_id, None)


async def _run_execution(execution_id: int, control: ExecutionControl) -> None:
    task = await asyncio.to_thread(
        fetch_one,
        """
        SELECT t.*, e.script_path_snapshot, e.python_path_snapshot, e.maintenance_snapshot,
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
    if task.get('maintenance_snapshot'):
        import json
        snapshot = json.loads(task['maintenance_snapshot'])
        if snapshot['policy']['runtime'] == 'readonly-v1':
            from .maintenance import run_managed_execution
            await run_managed_execution(execution_id, task, control)
            return
    task_id = int(task["id"])
    started = time.monotonic()

    status = "failed"
    shutdown_requested = False
    exit_code: int | None = None
    error_message = ""
    process: asyncio.subprocess.Process | None = None
    stdout_task: asyncio.Task | None = None
    stderr_task: asyncio.Task | None = None
    workspace: ExecutionWorkspace | None = None
    public_work_dir: Path | None = None
    staged_template: Path | None = None
    failure_image: bytes | None = None
    screenshot_note = ""
    screenshot_attempted = False
    try:
        _check_stop(control)
        workspace = await asyncio.to_thread(create_execution_workspace, execution_id)
        _check_stop(control)
        script, interpreter = validate_script(
            task["script_path_snapshot"], task["python_path_snapshot"]
        )
        public_work_dir, staged_template = await asyncio.to_thread(
            prepare_work_directory,
            WORK_DIR,
            template_path=task.get("template_path") or None,
            template_name=task.get("template_filename") or None,
        )
        _check_stop(control)
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
        _check_stop(control)
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
            await _wait_for_process(process, control, timeout)
        except asyncio.TimeoutError:
            failure_image, screenshot_note = await _capture_failure_image(execution_id, task, workspace)
            screenshot_attempted = True
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
    except ForceStopRequested:
        control.accepting = False
        if process:
            termination = asyncio.create_task(_stop_process_confirmed(process, execution_id))
            try:
                await asyncio.shield(termination)
            except asyncio.CancelledError:
                # Shutdown must not abandon a stop already in progress.
                await termination
                shutdown_requested = True
        await _finish_stream_tasks(stdout_task, stderr_task)
        exit_code = process.returncode if process else None
        status = "cancelled"
        error_message = control.reason
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
        failure_image, screenshot_note = await _capture_failure_image(execution_id, task, workspace)
        screenshot_attempted = True
        if process and process.returncode is None:
            await _terminate_process(process)
        await _finish_stream_tasks(stdout_task, stderr_task)
        if process:
            exit_code = process.returncode
        error_message = str(exc)
        await asyncio.to_thread(append_execution_output, execution_id, "stderr", f"{exc}\n")
    finally:
        control.accepting = False

    # Resolve business failures as well as nonzero exits before cleaning the UI/workspace.
    if status != "cancelled" and not screenshot_attempted:
        before_cleanup = await asyncio.to_thread(
            resolve_execution_outcome, process_status=status, exit_code=exit_code,
            legacy_error=error_message, result_file=workspace.result_file if workspace else None,
        )
        if before_cleanup.status in {"failed", "timeout"}:
            failure_image, screenshot_note = await _capture_failure_image(execution_id, task, workspace)
            screenshot_attempted = True

    cleanup_error = await _cleanup_public_work_directory(execution_id)
    if cleanup_error:
        if status == "success":
            status = "failed"
        error_message = "；".join(
            item for item in (error_message.strip(), cleanup_error) if item
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    if status == "cancelled" and control.stop_event.is_set():
        outcome = ResolvedOutcome(status="cancelled", error_message=error_message,
                                  result_code="FORCE_STOPPED", result_message=control.reason, retryable=False)
    else:
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
    if outcome.status in {"failed", "timeout"} and not screenshot_attempted:
        failure_image, screenshot_note = await _capture_failure_image(execution_id, task, workspace)
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
        image_bytes=failure_image,
        screenshot_note=screenshot_note,
    )

    if shutdown_requested:
        raise asyncio.CancelledError
