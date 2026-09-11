"""脚本上传与环境管理接口。"""

from __future__ import annotations

import asyncio
import sqlite3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from typing import Annotated
from .. import security
from ..database import fetch_all
from ..environments import (
    MAX_SCRIPT_BYTES,
    MAX_TEMPLATE_BYTES,
    create_managed_task_bundle,
    delete_managed_app,
    request_rebuild,
)
from ..security import admin_user, ready_user
from ..services.task_queries import _create_task_schedule_values, _public_app, _task_or_404


router = APIRouter()


@router.get("/api/apps")
def list_apps(user: dict = Depends(ready_user)) -> list[dict]:
    rows = fetch_all(
        """
        SELECT
            a.*,
            creator.display_name AS created_by_name,
            (
                SELECT COUNT(*) FROM tasks active_task
                WHERE active_task.app_id = a.id AND active_task.archived = 0
            ) AS active_task_count,
            (
                SELECT COUNT(*) FROM tasks old_task
                WHERE old_task.app_id = a.id AND old_task.archived = 1
            ) AS archived_task_count
        FROM rpa_apps a
        LEFT JOIN users creator ON creator.id = a.created_by
        WHERE a.archived = 0
        ORDER BY a.id DESC
        """
    )
    return [
        _public_app(item, include_private=user["role"] in {"super_admin", "admin"}) for item in rows
    ]


@router.post("/api/apps", status_code=201)
async def create_app(
    request: Request,
    name: str = Form(...),
    requirements_text: str = Form(default=""),
    script: UploadFile = File(...),
    template: UploadFile | None = File(default=None),
    description: Annotated[str, Form()] = "",
    trigger_type: Annotated[str, Form()] = "manual",
    trigger_config: Annotated[str, Form()] = "{}",
    enabled: Annotated[bool, Form()] = True,
    notify_on_success: Annotated[bool, Form()] = True,
    notify_on_failure: Annotated[bool, Form()] = True,
    failure_screenshot: Annotated[bool, Form()] = False,
    user: dict = Depends(admin_user),
) -> dict:
    try:
        clean_description, clean_trigger_type, encoded_config, next_run_at = (
            _create_task_schedule_values(
                description, trigger_type, trigger_config, enabled
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = await script.read(MAX_SCRIPT_BYTES + 1)
    template_content = (
        await template.read(MAX_TEMPLATE_BYTES + 1) if template is not None else None
    )
    try:
        created = await asyncio.to_thread(
            create_managed_task_bundle,
            name,
            script.filename or "main.py",
            content,
            requirements_text,
            int(user["id"]),
            template.filename or "" if template is not None else "",
            template_content,
            description=clean_description,
            trigger_type=clean_trigger_type,
            trigger_config=encoded_config,
            next_run_at=next_run_at,
            enabled=enabled,
            notify_on_success=notify_on_success,
            notify_on_failure=notify_on_failure,
            failure_screenshot=failure_screenshot,
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="这个任务名称已经存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    security.write_audit(
        request,
        user,
        "create_task",
        target_type="task",
        target_id=created["task_id"],
        summary=f"创建任务 {created['name']} 并准备独立 Python 环境",
    )
    task_id = int(created["task_id"])
    result = _public_app(created, include_private=True)
    result["task"] = _task_or_404(task_id, public=True)
    return result


@router.post("/api/apps/{app_id}/rebuild")
def rebuild_app(
    app_id: int,
    request: Request,
    user: dict = Depends(admin_user),
) -> dict:
    try:
        updated = request_rebuild(app_id, int(user["id"]))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    security.write_audit(
        request,
        user,
        "rebuild_environment",
        target_type="app",
        target_id=app_id,
        summary=f"重建 {updated['name']} 的 Python 环境",
    )
    return _public_app(updated, include_private=True)


@router.delete("/api/apps/{app_id}")
def delete_app(
    app_id: int,
    request: Request,
    user: dict = Depends(admin_user),
) -> dict:
    try:
        result = delete_managed_app(app_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    security.write_audit(
        request,
        user,
        "delete_app",
        target_type="app",
        target_id=app_id,
        summary=f"彻底删除未绑定程序 {result['name']}",
    )
    return result
