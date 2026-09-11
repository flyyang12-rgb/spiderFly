"""任务设置、计划与手动运行接口。"""

from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Request
from .. import security
from ..database import TASK_EXECUTION_STATUS_SQL, fetch_all, fetch_one, transaction, utc_now
from ..environments import app_runtime, delete_task_bundle
from ..scheduling import decode_trigger_config
from ..schemas import RunResponse, TaskPatch, TaskPayload
from ..security import admin_user, ready_user
from ..services.execution_queue import _enqueue_task
from ..services.task_queries import TASK_SELECT, _public_task, _schedule_values, _task_or_404


router = APIRouter()


@router.get("/api/tasks")
def list_tasks(user: dict = Depends(ready_user)) -> list[dict]:
    del user
    return [
        _public_task(task)
        for task in fetch_all(f"{TASK_SELECT} WHERE t.archived = 0 ORDER BY t.id DESC")
    ]


@router.post("/api/tasks", status_code=201)
def create_task(
    payload: TaskPayload,
    request: Request,
    user: dict = Depends(ready_user),
) -> dict:
    try:
        app_item, script, python_path = app_runtime(payload.app_id)
        trigger_config, next_run_at = _schedule_values(
            payload.trigger_type, payload.trigger_config, payload.enabled
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    now = utc_now()
    try:
        with transaction() as conn:
            current_app = conn.execute(
                "SELECT id FROM rpa_apps WHERE id = ? AND archived = 0",
                (payload.app_id,),
            ).fetchone()
            if not current_app:
                raise HTTPException(
                    status_code=409,
                    detail="这个自动化程序已经被移除，请刷新后重新选择",
                )
            bound_task = conn.execute(
                "SELECT name FROM tasks WHERE app_id = ? AND archived = 0 LIMIT 1",
                (payload.app_id,),
            ).fetchone()
            if bound_task:
                raise HTTPException(
                    status_code=409,
                    detail="这个程序已经绑定任务，请上传一个新的程序",
                )
            cursor = conn.execute(
                """
                INSERT INTO tasks (
                    name, description, app_id, app_name, script_path, python_path,
                    enabled, trigger_type, trigger_config, next_run_at,
                    timeout_seconds, notify_on_success, notify_on_failure, failure_screenshot,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.name,
                    payload.description,
                    payload.app_id,
                    app_item["name"],
                    str(script),
                    str(python_path),
                    int(payload.enabled),
                    payload.trigger_type,
                    trigger_config,
                    next_run_at,
                    payload.timeout_seconds,
                    int(payload.notify_on_success),
                    int(payload.notify_on_failure),
                    int(payload.failure_screenshot),
                    user["id"],
                    user["id"],
                    now,
                    now,
                ),
            )
            task_id = int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        detail = (
            "这个程序已经绑定任务，请上传一个新的程序"
            if "tasks.app_id" in str(exc)
            else "任务名称已存在"
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    security.write_audit(
        request,
        user,
        "create_task",
        target_type="task",
        target_id=task_id,
        summary=f"创建任务 {payload.name}",
    )
    return _task_or_404(task_id, public=True)


@router.patch("/api/tasks/{task_id}")
def update_task(
    task_id: int,
    payload: TaskPatch,
    request: Request,
    user: dict = Depends(ready_user),
) -> dict:
    current = _task_or_404(task_id)
    values = payload.model_dump(exclude_unset=True)
    expected_version = int(values.pop("version", current["version"]))
    if not values:
        return _public_task(current)

    active = fetch_one(
        "SELECT id, status FROM executions WHERE task_id = ? AND status IN ('pending', 'running')",
        (task_id,),
    )
    if active and any(key != "enabled" for key in values):
        raise HTTPException(status_code=409, detail="排队或运行中的任务只能停用，不能修改配置")

    final_app_id = int(values.get("app_id", current["app_id"]))
    if final_app_id != int(current["app_id"] or 0):
        raise HTTPException(
            status_code=409,
            detail="任务不能更换程序；请删除后上传新程序并重新创建",
        )
    try:
        if values == {"enabled": False}:
            # Stopping a schedule must remain possible when its environment is broken.
            app_item = {"name": current["app_name"]}
            script, python_path = current["script_path"], current["python_path"]
        else:
            app_item, script, python_path = app_runtime(final_app_id)
        final_enabled = bool(values.get("enabled", current["enabled"]))
        final_trigger_type = values.get("trigger_type", current["trigger_type"])
        final_trigger_config = values.get(
            "trigger_config", decode_trigger_config(current.get("trigger_config"))
        )
        encoded_config, next_run_at = _schedule_values(
            final_trigger_type, final_trigger_config, final_enabled
        )
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    values.update(
        {
            "app_id": final_app_id,
            "app_name": app_item["name"],
            "script_path": str(script),
            "python_path": str(python_path),
            "trigger_config": encoded_config,
            "next_run_at": next_run_at,
            "updated_by": user["id"],
        }
    )
    columns: list[str] = []
    params: list[object] = []
    for key, value in values.items():
        columns.append(f"{key} = ?")
        params.append(int(value) if isinstance(value, bool) else value)
    columns.extend(["version = version + 1", "updated_at = ?"])
    params.extend([utc_now(), task_id, expected_version])
    try:
        with transaction() as conn:
            current_app = conn.execute(
                "SELECT id FROM rpa_apps WHERE id = ? AND archived = 0",
                (final_app_id,),
            ).fetchone()
            if not current_app:
                raise HTTPException(
                    status_code=409,
                    detail="这个自动化程序已经被移除，请刷新后重新选择",
                )
            cursor = conn.execute(
                f"UPDATE tasks SET {', '.join(columns)} WHERE id = ? AND version = ? AND archived = 0",
                tuple(params),
            )
            if cursor.rowcount != 1:
                raise HTTPException(status_code=409, detail="任务已被其他伙伴修改，请刷新后重试")
            if values.get("enabled") == 0:
                now = utc_now()
                cancelled = conn.execute(
                    """
                    UPDATE executions
                    SET status = 'cancelled', ended_at = ?, error_message = '任务停用，已取消排队'
                    WHERE task_id = ? AND status = 'pending'
                    """,
                    (now, task_id),
                )
                if cancelled.rowcount:
                    conn.execute(
                        f"UPDATE tasks SET last_status = {TASK_EXECUTION_STATUS_SQL} WHERE id = ?",
                        ("cancelled", task_id),
                    )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务名称已存在") from exc

    security.write_audit(
        request,
        user,
        "update_task",
        target_type="task",
        target_id=task_id,
        summary=f"更新任务 {values.get('name', current['name'])}",
    )
    return _task_or_404(task_id, public=True)


@router.delete("/api/tasks/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    request: Request,
    user: dict = Depends(admin_user),
) -> None:
    try:
        result = delete_task_bundle(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    security.write_audit(
        request,
        user,
        "delete_task",
        target_type="task",
        target_id=task_id,
        summary=f"彻底删除任务 {result['name']}、程序 {result['app_name']} 和运行历史",
    )


@router.post("/api/tasks/{task_id}/run", response_model=RunResponse)
async def run_task(
    task_id: int,
    request: Request,
    user: dict = Depends(ready_user),
) -> RunResponse:
    execution_id = await _enqueue_task(task_id, "manual", int(user["id"]))
    task = _task_or_404(task_id)
    security.write_audit(
        request,
        user,
        "run_task",
        target_type="task",
        target_id=task_id,
        summary=f"手动运行 {task['name']}，执行记录 #{execution_id}",
    )
    return RunResponse(
        execution_id=execution_id,
        status="pending",
        message="任务已进入串行队列",
    )
