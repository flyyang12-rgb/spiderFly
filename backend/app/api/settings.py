"""系统状态与健康查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from .. import ai_agent
from ..config import DEFAULT_TASK_TIMEOUT_SECONDS, MANAGED_BROWSER_PORT, WORK_DIR
from ..feishu import FeishuSettings
from ..security import ready_user
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
    value = FeishuSettings.from_env()
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
        "feishu_configured": value.configured,
        "receiver_id_type": "webhook" if value.webhook_url else value.receiver_id_type,
        "receiver_masked": "群机器人" if value.webhook_url else (("***" + value.receiver_id[-4:]) if value.receiver_id else ""),
        "notification_policy": "one-final-message",
    }
