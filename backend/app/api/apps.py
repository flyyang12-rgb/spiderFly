"""脚本上传与环境管理接口。"""

from __future__ import annotations

import asyncio
import sqlite3
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from typing import Annotated
from .. import security
from ..database import fetch_all, transaction
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


@router.post('/api/apps/analyze-dependencies')
async def analyze_dependencies(
    script: UploadFile = File(...), requirements_text: str = Form(default=''),
    dependency_decisions: str = Form(default='[]', max_length=60000),
    target_host_id: int | None = Form(default=None), task_id: int | None = Form(default=None),
    user: dict = Depends(admin_user),
):
    import json
    from ..dependency_analysis import analyze, target_context
    from ..environments import validate_python_upload
    from ..services.task_queries import validate_target_host
    try:
        filename, source = validate_python_upload(script.filename or 'main.py', await script.read(MAX_SCRIPT_BYTES + 1))
        with transaction() as conn:
            if task_id is not None:
                task = conn.execute('SELECT target_host_id FROM tasks WHERE id=? AND archived=0', (task_id,)).fetchone()
                if not task: raise HTTPException(404, '任务不存在')
                target_host_id = task['target_host_id']
            validate_target_host(conn, target_host_id)
        context = await asyncio.to_thread(target_context, target_host_id)
        return await asyncio.to_thread(analyze, source, filename, requirements_text,
                                       json.loads(dependency_decisions), context)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/api/creation-requests/{kind}/{request_id}')
def creation_result(kind: str, request_id: str, user: dict = Depends(admin_user)):
    from ..services import creation_requests, workflows
    if kind not in {'task', 'workflow'}:
        raise HTTPException(400, '创建对象类型无效')
    with transaction() as conn:
        receipt = creation_requests.find(conn, user['id'], kind, request_id)
        if not receipt:
            raise HTTPException(404, '尚未查到创建结果，可使用原请求重试')
        if kind == 'workflow':
            item = workflows.get_definition(conn, receipt['object_id'])
            if item['archived']:
                raise HTTPException(409, '该请求对应的流程已归档，不能重复创建')
        else:
            try:
                item = _task_or_404(receipt['object_id'], public=True)
            except HTTPException as exc:
                if exc.status_code == 404:
                    raise HTTPException(409, '该请求对应的任务已删除，不能重复创建') from exc
                raise
    return {'kind': kind, 'id': receipt['object_id'], 'object': item}


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


@router.get('/api/apps/{app_id}/dependency-details')
def dependency_details(app_id: int, user: dict = Depends(admin_user)):
    import json
    from ..database import fetch_one
    if not fetch_one('SELECT id FROM rpa_apps WHERE id=? AND archived=0', (app_id,)):
        raise HTTPException(404, '任务程序不存在')
    versions = fetch_all('''SELECT v.id,v.sequence,d.analysis FROM tasks t JOIN task_code_versions v ON v.task_id=t.id
        LEFT JOIN dependency_analyses d ON d.version_id=v.id WHERE t.app_id=? ORDER BY v.sequence DESC''', (app_id,))
    records = fetch_all('''SELECT e.revision,e.version_id,v.sequence AS version_sequence,e.requirements_hash,e.target,e.status,e.evidence,e.completed_at
        FROM environment_records e LEFT JOIN task_code_versions v ON v.id=e.version_id
        WHERE e.app_id=? ORDER BY e.id DESC LIMIT 10''', (app_id,))
    for record in records:
        facts = json.loads(record.pop('evidence'))
        for key in ('executable','prefix'): facts.pop(key, None)
        record['evidence'] = facts
    remote_records = fetch_all('''SELECT r.id AS run_id,r.host_id,h.name AS host_name,r.code_version_id AS version_id,
        r.version_sequence,r.package_hash,e.report_json,e.received_at FROM remote_environment_records e
        JOIN remote_runs r ON r.id=e.run_id JOIN agent_hosts h ON h.id=r.host_id
        JOIN tasks t ON t.id=r.task_id WHERE t.app_id=? ORDER BY r.id DESC LIMIT 10''', (app_id,)) if fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_environment_records'") else []
    for record in remote_records:
        record['environment'] = json.loads(record.pop('report_json'))
    return {'versions':[{'id':v['id'],'sequence':v['sequence'],
                          'analysis':json.loads(v['analysis']) if v['analysis'] else None} for v in versions],
            'environments':records, 'remote_environments':remote_records}


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
    target_host_id: Annotated[int | None, Form()] = None,
    enabled: Annotated[bool, Form()] = True,
    notify_on_success: Annotated[bool, Form()] = True,
    notify_on_failure: Annotated[bool, Form()] = True,
    failure_screenshot: Annotated[bool, Form()] = False,
    request_id: Annotated[str | None, Form(min_length=16, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')] = None,
    dependency_decisions: Annotated[str, Form(max_length=60000)] = '[]',
    user: dict = Depends(admin_user),
) -> dict:
    try:
        clean_description, clean_trigger_type, encoded_config, next_run_at = (
            _create_task_schedule_values(
                description, trigger_type, trigger_config, enabled
            )
        )
        if clean_trigger_type == "cron" and target_host_id is not None:
            raise ValueError("Cron 首版仅支持主控本机，请选择主控本机")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    content = await script.read(MAX_SCRIPT_BYTES + 1)
    template_content = (
        await template.read(MAX_TEMPLATE_BYTES + 1) if template is not None else None
    )
    try:
        import json
        decisions = json.loads(dependency_decisions)
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
            target_host_id=target_host_id,
            enabled=enabled,
            notify_on_success=notify_on_success,
            notify_on_failure=notify_on_failure,
            failure_screenshot=failure_screenshot,
            request_id=request_id,
            dependency_decisions=decisions,
            on_created=lambda conn, task_id: security.write_audit(
                request, user, 'create_task', target_type='task', target_id=task_id,
                summary=f'创建任务 {name} 并准备独立 Python 环境', conn=conn),
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="这个任务名称已经存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    task_id = int(created["task_id"])
    result = _public_app(created, include_private=True)
    if created.get("version_sequence") is not None:
        result["version_sequence"] = int(created["version_sequence"])
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
