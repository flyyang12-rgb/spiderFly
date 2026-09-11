"""后台任务的启动、关闭与环境构建循环。"""

from __future__ import annotations

import asyncio
import logging
from .. import ai_agent, maintenance
from ..database import init_db
from ..environments import (
    build_environment,
    cleanup_legacy_task_program_model,
    pending_environment_ids,
)
from ..instance_lock import InstanceLock, acquire_instance_lock
from ..scheduling import reconcile_schedules, scheduler_loop
from ..security import ensure_bootstrap_admin
from ..services.execution_queue import _enqueue_task, _queue_worker_loop


logger = logging.getLogger(__name__)


_scheduler_task: asyncio.Task | None = None


_queue_worker_task: asyncio.Task | None = None


_environment_worker_task: asyncio.Task | None = None


_instance_lock: InstanceLock | None = None


async def startup() -> None:
    global _scheduler_task, _queue_worker_task, _environment_worker_task, _instance_lock
    _instance_lock = acquire_instance_lock()
    try:
        init_db()
        await asyncio.to_thread(cleanup_legacy_task_program_model)
        bootstrap_file = ensure_bootstrap_admin()
        if bootstrap_file:
            print(f"[SpiderFly] 首次登录信息：{bootstrap_file}")
        reconcile_schedules()
        maintenance.init_tables()
        ai_agent.start()
        _scheduler_task = asyncio.create_task(scheduler_loop(_enqueue_task))
        _queue_worker_task = asyncio.create_task(_queue_worker_loop())
        _environment_worker_task = asyncio.create_task(_environment_worker_loop())
    except BaseException:
        await _stop_background_tasks()
        _instance_lock.close()
        _instance_lock = None
        raise


async def _stop_background_tasks() -> None:
    global _scheduler_task, _queue_worker_task, _environment_worker_task
    await ai_agent.stop()
    tasks = [
        item
        for item in (_scheduler_task, _environment_worker_task, _queue_worker_task)
        if item
    ]
    for item in tasks:
        item.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _scheduler_task = None
    _environment_worker_task = None
    _queue_worker_task = None


async def shutdown() -> None:
    global _instance_lock
    try:
        await _stop_background_tasks()
    finally:
        if _instance_lock:
            _instance_lock.close()
            _instance_lock = None


async def _environment_worker_loop() -> None:
    while True:
        try:
            pending = await asyncio.to_thread(pending_environment_ids)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("读取待构建 Python 环境失败")
            await asyncio.sleep(1)
            continue
        if not pending:
            await asyncio.sleep(1)
            continue
        for app_id in pending:
            try:
                await build_environment(app_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("应用 %s 的环境构建发生未处理异常", app_id)
