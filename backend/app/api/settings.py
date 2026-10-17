"""系统状态与健康查询接口。"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from .. import ai_agent, feishu_settings
from ..config import DEFAULT_TASK_TIMEOUT_SECONDS, MANAGED_BROWSER_PORT, WORK_DIR
from ..security import admin_user, ready_user, super_admin_user, write_audit
from ..services import runtime


router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "shared-central-python",
        "scheduler": "running"
        if runtime._scheduler_task and not runtime._scheduler_task.done()
        else "stopped",
        "queue_worker": "running"
        if runtime._queue_worker_task and not runtime._queue_worker_task.done()
        else "stopped",
        "environment_worker": "running"
        if runtime._environment_worker_task and not runtime._environment_worker_task.done()
        else "stopped",
        "ai_worker": "running" if ai_agent.is_running() else "stopped",
    }


@router.get("/api/settings")
def settings(user: dict = Depends(ready_user)) -> dict:
    del user
    value = feishu_settings.public_settings()
    return {
        "mode": "shared-central-python",
        "scheduler": "running"
        if runtime._scheduler_task and not runtime._scheduler_task.done()
        else "stopped",
        "queue_worker": "running"
        if runtime._queue_worker_task and not runtime._queue_worker_task.done()
        else "stopped",
        "scheduler_timezone": "Asia/Shanghai",
        "concurrency": 1,
        "collision_policy": "scheduled-runs-queue-manual-deduplicated",
        "task_timeout_seconds": DEFAULT_TASK_TIMEOUT_SECONDS,
        "work_directory_name": WORK_DIR.name,
        "host_preflight": "excel-and-managed-browser-port",
        "managed_browser_port": MANAGED_BROWSER_PORT,
        "feishu_configured": value["configured"],
        "receiver_id_type": "webhook" if value["mode"] == "webhook" else value["receiver_id_type"],
        "receiver_masked": "群机器人" if value["mode"] == "webhook" else "应用收件人" if value["mode"] == "application" else "",
        "notification_policy": "one-final-message",
    }


_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


@router.get("/api/settings/feishu")
def get_feishu_settings(response: Response, user: dict = Depends(admin_user)) -> dict:
    response.headers.update(_PRIVATE_HEADERS)
    try:
        return feishu_settings.public_settings(include_details=user["role"] == "super_admin")
    except Exception:
        raise HTTPException(400, "飞书配置无法读取，请检查配置目录", headers=_PRIVATE_HEADERS) from None


async def _feishu_payload(request: Request) -> dict:
    body = await request.body()
    if len(body) > 4000:
        raise HTTPException(400, "飞书配置内容过长", headers=_PRIVATE_HEADERS)
    try:
        value = json.loads(body)
    except (ValueError, TypeError):
        raise HTTPException(400, "飞书配置格式无效", headers=_PRIVATE_HEADERS) from None
    if not isinstance(value, dict):
        raise HTTPException(400, "飞书配置格式无效", headers=_PRIVATE_HEADERS)
    return value


async def _mutate_feishu(request: Request, response: Response, user: dict, *, clear: bool) -> dict:
    response.headers.update(_PRIVATE_HEADERS)
    payload = await _feishu_payload(request)
    try:
        operation = feishu_settings.clear_settings if clear else feishu_settings.save_settings
        result = await asyncio.to_thread(operation, payload)
    except feishu_settings.ConfigurationConflict as error:
        raise HTTPException(409, str(error), headers=_PRIVATE_HEADERS) from None
    except ValueError as error:
        raise HTTPException(400, str(error), headers=_PRIVATE_HEADERS) from None
    except Exception:
        raise HTTPException(400, "飞书配置更新失败，请重新加载后重试", headers=_PRIVATE_HEADERS) from None
    write_audit(request, user, "feishu_settings", target_type="settings",
                summary="清除飞书本机配置" if clear else "更新飞书通知配置")
    return result


@router.put("/api/settings/feishu")
async def update_feishu_settings(request: Request, response: Response, user: dict = Depends(super_admin_user)) -> dict:
    return await _mutate_feishu(request, response, user, clear=False)


@router.delete("/api/settings/feishu")
async def delete_feishu_settings(request: Request, response: Response, user: dict = Depends(super_admin_user)) -> dict:
    return await _mutate_feishu(request, response, user, clear=True)
