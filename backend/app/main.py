from __future__ import annotations

import asyncio
import sqlite3
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import execute, fetch_all, fetch_one, init_db, utc_now
from .config import load_env_file
from .feishu import FeishuSettings
from .runner import run_execution, validate_script
from .scheduling import (
    compute_next_run,
    decode_trigger_config,
    encode_trigger_config,
    normalize_trigger,
    reconcile_schedules,
    scheduler_loop,
)
from .schemas import RunResponse, TaskPatch, TaskPayload


load_env_file()
app = FastAPI(title="SpiderFly", version="0.1.0")
_background_runs: set[asyncio.Task] = set()
_scheduler_task: asyncio.Task | None = None
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    global _scheduler_task
    init_db()
    reconcile_schedules()
    _scheduler_task = asyncio.create_task(scheduler_loop(_enqueue_task))


@app.on_event("shutdown")
async def shutdown() -> None:
    if _scheduler_task:
        _scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await _scheduler_task


def _task_or_404(task_id: int, *, public: bool = False) -> dict:
    task = fetch_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _public_task(task) if public else task


def _public_task(task: dict) -> dict:
    item = dict(task)
    item["trigger_config"] = decode_trigger_config(item.get("trigger_config"))
    return item


def _schedule_values(trigger_type: str, trigger_config: dict, enabled: bool) -> tuple[str, str | None]:
    normalized = normalize_trigger(trigger_type, trigger_config)
    next_run = compute_next_run(trigger_type, normalized) if enabled else None
    if enabled and trigger_type == "once" and next_run is None:
        raise ValueError("单次执行时间必须晚于当前时间")
    return encode_trigger_config(normalized), next_run


async def _enqueue_task(task_id: int, source: str = "manual") -> int:
    task = _task_or_404(task_id)
    if not task["enabled"]:
        raise HTTPException(status_code=409, detail="任务已停用")
    running = fetch_one(
        "SELECT id FROM executions WHERE task_id = ? AND status IN ('pending', 'running') LIMIT 1",
        (task_id,),
    )
    if running:
        raise HTTPException(status_code=409, detail="任务正在运行")
    try:
        validate_script(task["script_path"], task["python_path"])
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    execution_id = execute(
        """
        INSERT INTO executions (task_id, status, trigger_source, created_at)
        VALUES (?, 'pending', ?, ?)
        """,
        (task_id, source, utc_now()),
    )
    run = asyncio.create_task(run_execution(execution_id, task_id))
    _background_runs.add(run)
    run.add_done_callback(_background_runs.discard)
    return execution_id


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "local-direct",
        "scheduler": "running" if _scheduler_task and not _scheduler_task.done() else "stopped",
    }


@app.get("/api/overview")
def overview() -> dict:
    counts = fetch_one(
        """
        SELECT
            COUNT(*) AS total_tasks,
            SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_tasks,
            SUM(CASE WHEN last_status = 'running' THEN 1 ELSE 0 END) AS running_tasks
        FROM tasks
        """
    ) or {}
    today = fetch_one(
        """
        SELECT
            COUNT(*) AS total_runs,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_runs,
            SUM(CASE WHEN status IN ('failed', 'timeout') THEN 1 ELSE 0 END) AS failed_runs
        FROM executions
        WHERE date(created_at) = date('now')
        """
    ) or {}
    return {**counts, **today}


@app.get("/api/settings")
def settings() -> dict:
    value = FeishuSettings.from_env()
    return {
        "mode": "local-direct",
        "scheduler": "running" if _scheduler_task and not _scheduler_task.done() else "stopped",
        "scheduler_timezone": "Asia/Shanghai",
        "collision_policy": "skip-overlapping-run",
        "feishu_configured": value.configured,
        "receiver_id_type": value.receiver_id_type,
        "receiver_masked": ("***" + value.receiver_id[-4:]) if value.receiver_id else "",
        "notification_policy": "one-final-message",
    }


@app.get("/api/tasks")
def list_tasks() -> list[dict]:
    return [_public_task(task) for task in fetch_all("SELECT * FROM tasks ORDER BY id DESC")]


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskPayload) -> dict:
    try:
        validate_script(payload.script_path, payload.python_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = utc_now()
    app_name = payload.app_name or Path(payload.script_path).stem
    try:
        trigger_config, next_run_at = _schedule_values(
            payload.trigger_type, payload.trigger_config, payload.enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        task_id = execute(
            """
            INSERT INTO tasks (
                name, description, app_name, script_path, python_path, enabled,
                trigger_type, trigger_config, next_run_at,
                timeout_seconds, notify_on_success, notify_on_failure,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                payload.description,
                app_name,
                payload.script_path,
                payload.python_path,
                int(payload.enabled),
                payload.trigger_type,
                trigger_config,
                next_run_at,
                payload.timeout_seconds,
                int(payload.notify_on_success),
                int(payload.notify_on_failure),
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务名称已存在") from exc
    return _task_or_404(task_id, public=True)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskPatch) -> dict:
    current = _task_or_404(task_id)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        return _public_task(current)
    final_script = values.get("script_path", current["script_path"])
    final_python = values.get("python_path", current["python_path"])
    try:
        validate_script(final_script, final_python)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    final_enabled = bool(values.get("enabled", current["enabled"]))
    final_trigger_type = values.get("trigger_type", current["trigger_type"])
    final_trigger_config = values.get(
        "trigger_config", decode_trigger_config(current.get("trigger_config"))
    )
    try:
        encoded_config, next_run_at = _schedule_values(
            final_trigger_type, final_trigger_config, final_enabled
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    values["trigger_config"] = encoded_config
    values["next_run_at"] = next_run_at
    if "script_path" in values and "app_name" not in values:
        values["app_name"] = Path(final_script).stem

    columns = []
    params = []
    for key, value in values.items():
        columns.append(f"{key} = ?")
        params.append(int(value) if isinstance(value, bool) else value)
    columns.append("updated_at = ?")
    params.extend([utc_now(), task_id])
    try:
        execute(f"UPDATE tasks SET {', '.join(columns)} WHERE id = ?", tuple(params))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务名称已存在") from exc
    return _task_or_404(task_id, public=True)


@app.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(task_id: int) -> None:
    task = _task_or_404(task_id)
    if task["last_status"] == "running":
        raise HTTPException(status_code=409, detail="运行中的任务不能删除")
    execute("DELETE FROM tasks WHERE id = ?", (task_id,))


@app.post("/api/tasks/{task_id}/run", response_model=RunResponse)
async def run_task(task_id: int) -> RunResponse:
    execution_id = await _enqueue_task(task_id, "manual")
    return RunResponse(execution_id=execution_id, status="pending", message="任务已开始运行")


@app.get("/api/executions")
def list_executions(
    task_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    where = "WHERE e.task_id = ?" if task_id is not None else ""
    params = (task_id, limit) if task_id is not None else (limit,)
    return fetch_all(
        f"""
        SELECT e.*, t.name AS task_name, t.script_path
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        {where}
        ORDER BY e.id DESC
        LIMIT ?
        """,
        params,
    )


@app.get("/api/executions/{execution_id}")
def get_execution(execution_id: int) -> dict:
    item = fetch_one(
        """
        SELECT e.*, t.name AS task_name, t.script_path
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        WHERE e.id = ?
        """,
        (execution_id,),
    )
    if not item:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    return item


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
