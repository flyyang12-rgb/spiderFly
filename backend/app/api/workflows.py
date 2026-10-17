"""本机手动工作流定义、整轮运行和简要历史。"""

from __future__ import annotations

import asyncio
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..database import transaction
from ..schemas import WorkflowPayload, WorkflowPatch, WorkflowRunPayload, WorkflowSchedulePatch
from ..security import admin_user, ready_user, write_audit
from ..services import workflows, workflow_schedules, creation_requests
from ..runner import request_execution_stop
from .executions import _execution_date_boundary


router = APIRouter()


def _call(action, *args, **kwargs):
    try:
        return action(*args, **kwargs)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


def _dispatch_stops(ids: list[int]) -> None:
    # The durable flag is already committed. A racing finish is reconciled by
    # the serial worker; an absent live control cannot authorize another step.
    for execution_id in ids:
        try:
            request_execution_stop(execution_id, "流程停止")
        except ValueError:
            continue


@router.get("/api/workflows/tasks")
def workflow_tasks(user: dict = Depends(ready_user)) -> list[dict]:
    del user
    with transaction() as conn:
        return workflows.list_task_candidates(conn)


@router.get("/api/workflows")
def list_workflows(include_archived: bool = False, user: dict = Depends(ready_user)) -> list[dict]:
    del user
    with transaction() as conn:
        return workflows.list_definitions(conn, include_archived=include_archived)


@router.post("/api/workflows", status_code=201)
def create_workflow(payload: WorkflowPayload, request: Request,
                    user: dict = Depends(admin_user)) -> dict:
    with transaction() as conn:
        intent_hash = creation_requests.fingerprint(payload.model_dump(exclude={'request_id'}))
        receipt = creation_requests.find(conn, user['id'], 'workflow', payload.request_id, intent_hash)
        if receipt:
            saved = workflows.get_definition(conn, receipt['object_id'])
            if saved['archived']:
                raise HTTPException(409, '该请求对应的流程已归档，不能重复创建')
            return saved
        item = _call(workflows.create_definition, conn, payload.name,
                     payload.description, payload.task_ids, user["id"],
                     schedule=payload.schedule.model_dump() if payload.schedule else None)
        write_audit(request, user, "create_workflow", target_type="workflow", conn=conn,
                    target_id=item["id"], summary=f"创建流程 {item['name']}，{len(payload.task_ids)} 个步骤")
        creation_requests.remember(conn, user['id'], 'workflow', payload.request_id, intent_hash, item['id'])
    return item


@router.get("/api/workflows/{workflow_id}")
def get_workflow(workflow_id: int, user: dict = Depends(ready_user)) -> dict:
    del user
    with transaction() as conn:
        return _call(workflows.get_definition, conn, workflow_id)


@router.patch("/api/workflows/{workflow_id}")
def update_workflow(workflow_id: int, payload: WorkflowPatch, request: Request,
                    user: dict = Depends(admin_user)) -> dict:
    with transaction() as conn:
        item = _call(workflows.update_definition, conn, workflow_id, payload.name,
                     payload.description, payload.task_ids, user["id"],
                     expected_revision=payload.revision,
                     schedule=payload.schedule.model_dump() if payload.schedule else None)
        write_audit(request, user, "update_workflow", target_type="workflow", conn=conn,
                target_id=workflow_id, summary=f"更新流程 {item['name']}，版本 {item['revision']}")
    return item


@router.post("/api/workflows/{workflow_id}/archive")
def archive_workflow(workflow_id: int, request: Request,
                     user: dict = Depends(admin_user)) -> dict:
    with transaction() as conn:
        item = _call(workflows.archive_definition, conn, workflow_id, user["id"])
    write_audit(request, user, "archive_workflow", target_type="workflow",
                target_id=workflow_id, summary=f"归档流程 {item['name']}")
    return item


@router.patch('/api/workflows/{workflow_id}/schedule')
def update_workflow_schedule(workflow_id: int, payload: WorkflowSchedulePatch,
                             request: Request, user: dict = Depends(admin_user)):
    with transaction() as conn:
        item = _call(workflow_schedules.update_plan, conn, workflow_id, payload.trigger_type,
            payload.trigger_config, payload.schedule_enabled, payload.revision, user['id'])
        write_audit(request, user, 'update_workflow_schedule', target_type='workflow', conn=conn,
        target_id=workflow_id, summary=f"更新流程 {item['name']} 的统一计划，{'开启' if item['schedule_enabled'] else '关闭'}")
    return item


@router.get('/api/workflows/{workflow_id}/triggers')
def workflow_trigger_history(workflow_id: int, before_id: int | None = Query(default=None, ge=1),
                             user: dict = Depends(ready_user)):
    del user
    with transaction() as conn:
        return workflow_schedules.history(conn, workflow_id, before_id)


@router.post("/api/workflows/{workflow_id}/runs", status_code=202)
async def start_workflow(workflow_id: int, payload: WorkflowRunPayload, request: Request,
                         user: dict = Depends(admin_user)) -> dict:
    item = await asyncio.to_thread(_call, workflows.start_run, workflow_id,
        user["id"], payload.request_id, rerun_of=payload.rerun_of)
    await asyncio.to_thread(write_audit, request, user,
        "rerun_workflow" if payload.rerun_of else "start_workflow",
        target_type="workflow", target_id=workflow_id,
        summary=f"{'整轮重跑' if payload.rerun_of else '启动'}流程 {item['workflow_name']}，运行 #{item['id']}")
    return item


@router.get("/api/workflow-runs")
def list_workflow_runs(workflow_id: int | None = Query(default=None, ge=1),
    status: str = Query(default="", max_length=20), date_from: date | None = None,
    date_to: date | None = None, page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100), user: dict = Depends(ready_user)) -> dict:
    del user
    normalized = status.strip().lower()
    if normalized and normalized not in {"waiting", "running", "stopping", "success", "failed", "cancelled"}:
        raise HTTPException(400, "流程状态筛选值无效")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(400, "开始日期不能晚于结束日期")
    with transaction() as conn:
        return workflows.list_runs(conn, workflow_id=workflow_id, status=normalized,
            date_from=_execution_date_boundary(date_from) if date_from else None,
            date_to=_execution_date_boundary(date_to, next_day=True) if date_to else None,
            page=page, page_size=page_size)


@router.get("/api/workflow-runs/{run_id}")
def get_workflow_run(run_id: int, user: dict = Depends(ready_user)) -> dict:
    del user
    with transaction() as conn:
        return _call(workflows.get_run, conn, run_id)


@router.post("/api/workflow-runs/{run_id}/stop", status_code=202)
async def stop_workflow_run(run_id: int, request: Request,
                            user: dict = Depends(admin_user)) -> dict:
    item = await asyncio.to_thread(_call, workflows.request_stop, run_id, user["id"])
    _dispatch_stops(item.pop("stop_execution_ids", []))
    await asyncio.to_thread(write_audit, request, user, "stop_workflow",
        target_type="workflow_run", target_id=run_id,
        summary=f"请求停止流程 {item['workflow_name']}，运行 #{run_id}")
    return item
