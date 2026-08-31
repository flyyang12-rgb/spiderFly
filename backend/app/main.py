from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import execute, fetch_all, fetch_one, init_db, transaction, utc_now
from .config import (
    DEFAULT_TASK_TIMEOUT_SECONDS,
    HOST_CHECK_INTERVAL_SECONDS,
    MANAGED_BROWSER_PORT,
    WORK_DIR,
)
from .environments import (
    MAX_SCRIPT_BYTES,
    MAX_TEMPLATE_BYTES,
    active_environment_revision,
    app_runtime,
    build_environment,
    cleanup_legacy_task_program_model,
    create_managed_task_bundle,
    delete_managed_app,
    delete_task_bundle,
    managed_runtime_paths,
    pending_environment_ids,
    request_rebuild,
    runtime_ready,
)
from .feishu import FeishuSettings
from .host_runtime import HostRuntimeError, check_host_busy, clear_work_directory
from .instance_lock import InstanceLock, acquire_instance_lock
from .runner import run_execution
from .scheduling import (
    compute_next_run,
    decode_trigger_config,
    encode_trigger_config,
    normalize_trigger,
    reconcile_schedules,
    scheduler_loop,
)
from .schemas import (
    ChangePasswordPayload,
    LoginPayload,
    RunResponse,
    TaskPatch,
    TaskPayload,
    UserCreatePayload,
)
from .security import (
    admin_user,
    authenticate_user,
    change_password,
    clear_session,
    create_session,
    create_user,
    current_user,
    ensure_bootstrap_admin,
    list_users,
    public_user,
    ready_user,
    set_session_cookie,
    write_audit,
)


app = FastAPI(
    title="SpiderFly",
    version="0.2.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_scheduler_task: asyncio.Task | None = None
_queue_worker_task: asyncio.Task | None = None
_environment_worker_task: asyncio.Task | None = None
_instance_lock: InstanceLock | None = None
logger = logging.getLogger(__name__)


TASK_SELECT = """
    SELECT
        t.*,
        a.script_filename,
        a.script_path AS app_script_path,
        a.env_path AS app_env_path,
        a.environment_status,
        a.environment_error,
        a.revision AS app_revision,
        creator.display_name AS created_by_name,
        updater.display_name AS updated_by_name
    FROM tasks t
    LEFT JOIN rpa_apps a ON a.id = t.app_id
    LEFT JOIN users creator ON creator.id = t.created_by
    LEFT JOIN users updater ON updater.id = t.updated_by
"""


@app.on_event("startup")
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


@app.on_event("shutdown")
async def shutdown() -> None:
    global _instance_lock
    try:
        await _stop_background_tasks()
    finally:
        if _instance_lock:
            _instance_lock.close()
            _instance_lock = None


def _public_task(task: dict) -> dict:
    item = dict(task)
    item["trigger_config"] = decode_trigger_config(item.get("trigger_config"))
    item["runtime_ready"] = runtime_ready(
        {
            "script_path": item.get("app_script_path") or item.get("script_path") or "",
            "env_path": item.get("app_env_path") or "",
        }
    )
    for key in ("enabled", "notify_on_success", "notify_on_failure", "archived"):
        item[key] = bool(item.get(key))
    for key in ("script_path", "python_path", "app_script_path", "app_env_path"):
        item.pop(key, None)
    return item


def _task_or_404(task_id: int, *, public: bool = False) -> dict:
    task = fetch_one(
        f"{TASK_SELECT} WHERE t.id = ? AND t.archived = 0", (task_id,)
    )
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return _public_task(task) if public else task


def _public_app(item: dict, *, include_private: bool = False) -> dict:
    result = {
        "id": int(item["id"]),
        "name": item["name"],
        "script_filename": item["script_filename"],
        "template_filename": item.get("template_filename") or "",
        "environment_status": item["environment_status"],
        "environment_error": item.get("environment_error") or "",
        "runtime_ready": runtime_ready(item),
        "revision": int(item.get("revision") or 1),
        "requested_revision": int(item.get("revision") or 1),
        "active_revision": active_environment_revision(item),
        "active_task_count": int(item.get("active_task_count") or 0),
        "archived_task_count": int(item.get("archived_task_count") or 0),
        "created_by_name": item.get("created_by_name") or "系统迁移",
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }
    if include_private:
        result["requirements_text"] = item.get("requirements_text") or ""
        result["install_log"] = item.get("install_log") or ""
    return result


def _schedule_values(
    trigger_type: str, trigger_config: dict, enabled: bool
) -> tuple[str, str | None]:
    normalized = normalize_trigger(trigger_type, trigger_config)
    next_run = compute_next_run(trigger_type, normalized) if enabled else None
    if enabled and trigger_type == "once" and next_run is None:
        raise ValueError("单次执行时间必须晚于当前时间")
    return encode_trigger_config(normalized), next_run


def _create_task_schedule_values(
    description: str,
    trigger_type: str,
    trigger_config: str,
    enabled: bool,
) -> tuple[str, str, str, str | None]:
    clean_description = description.strip()
    if len(clean_description) > 500:
        raise ValueError("任务说明不能超过 500 个字符")
    trigger_type = (trigger_type or "manual").strip().lower()
    if trigger_type not in {"manual", "daily", "weekly"}:
        raise ValueError("不支持的触发方式")
    try:
        config = json.loads((trigger_config or "{}").strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("运行时间设置格式错误") from exc
    if not isinstance(config, dict):
        raise ValueError("运行时间设置必须是一个对象")
    encoded_config, next_run_at = _schedule_values(
        trigger_type, config, bool(enabled)
    )
    return clean_description, trigger_type, encoded_config, next_run_at


def _enqueue_task_sync(
    task_id: int, source: str = "manual", requested_by: int | None = None
) -> int:
    now = utc_now()
    try:
        with transaction() as conn:
            task = conn.execute(
                """
                SELECT
                    t.id, t.enabled, t.archived, t.app_id,
                    a.id AS current_app_id, a.name AS app_name,
                    a.script_path, a.env_path, a.environment_status,
                    a.archived AS app_archived
                FROM tasks t
                LEFT JOIN rpa_apps a ON a.id = t.app_id
                WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
            if not task or task["archived"]:
                raise HTTPException(status_code=404, detail="任务不存在")
            if not task["enabled"]:
                raise HTTPException(status_code=409, detail="任务已停用")
            if not task["current_app_id"] or task["app_archived"]:
                raise HTTPException(
                    status_code=409, detail="自动化程序不存在或已经移除"
                )
            app_item = dict(task)
            try:
                script, python_path = managed_runtime_paths(app_item)
            except (ValueError, FileNotFoundError, RuntimeError, OSError) as exc:
                if task["environment_status"] != "ready":
                    detail = "Python 环境尚未准备好"
                else:
                    detail = str(exc)
                raise HTTPException(status_code=409, detail=detail) from exc
            running = conn.execute(
                """
                SELECT id FROM executions
                WHERE task_id = ? AND status IN ('pending', 'running')
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if running:
                raise HTTPException(status_code=409, detail="任务已经在排队或运行")
            cursor = conn.execute(
                """
                INSERT INTO executions (
                    task_id, status, trigger_source, requested_by,
                    script_path_snapshot, python_path_snapshot, created_at
                ) VALUES (?, 'pending', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    source,
                    requested_by,
                    str(script),
                    str(python_path),
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE tasks
                SET last_status = 'pending', app_name = ?, script_path = ?,
                    python_path = ?, updated_at = ?
                WHERE id = ? AND archived = 0 AND enabled = 1
                """,
                (app_item["app_name"], str(script), str(python_path), now, task_id),
            )
            return int(cursor.lastrowid)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务已经在排队或运行") from exc


async def _enqueue_task(
    task_id: int, source: str = "manual", requested_by: int | None = None
) -> int:
    return await asyncio.to_thread(_enqueue_task_sync, task_id, source, requested_by)


def _next_pending_execution_id() -> int | None:
    item = fetch_one(
        "SELECT id FROM executions WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
    )
    return int(item["id"]) if item else None


def _set_execution_waiting(execution_id: int, message: str) -> None:
    execute(
        """
        UPDATE executions
        SET error_message = ?
        WHERE id = ? AND status = 'pending' AND error_message != ?
        """,
        (message[:1000], execution_id, message[:1000]),
    )


def _host_waiting_reason() -> str:
    busy = check_host_busy()
    if busy.busy:
        return f"等待宿主机空闲：{busy.message}"
    try:
        clear_work_directory(WORK_DIR)
    except HostRuntimeError as exc:
        return f"等待公共工作文件夹可用：{exc}"
    return ""


def _claim_next_execution() -> int | None:
    with transaction() as conn:
        row = conn.execute(
            "SELECT id, task_id FROM executions WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        now = utc_now()
        cursor = conn.execute(
            """
            UPDATE executions
            SET status = 'running', started_at = NULL, error_message = ''
            WHERE id = ? AND status = 'pending'
            """,
            (row["id"],),
        )
        if cursor.rowcount != 1:
            return None
        conn.execute(
            "UPDATE tasks SET last_status = 'running', updated_at = ? WHERE id = ?",
            (now, row["task_id"]),
        )
        return int(row["id"])


async def _queue_worker_loop() -> None:
    while True:
        pending_id = await asyncio.to_thread(_next_pending_execution_id)
        if pending_id is None:
            await asyncio.sleep(0.5)
            continue
        waiting_reason = await asyncio.to_thread(_host_waiting_reason)
        if waiting_reason:
            await asyncio.to_thread(_set_execution_waiting, pending_id, waiting_reason)
            await asyncio.sleep(HOST_CHECK_INTERVAL_SECONDS)
            continue
        execution_id = await asyncio.to_thread(_claim_next_execution)
        if execution_id is None:
            await asyncio.sleep(0.5)
            continue
        try:
            await run_execution(execution_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("执行记录 %s 发生未处理异常", execution_id)
            try:
                await asyncio.to_thread(
                    _mark_execution_worker_failure, execution_id, str(exc)
                )
            except Exception:
                logger.exception("执行记录 %s 的兜底失败状态写入失败", execution_id)
            await asyncio.sleep(0.1)


def _mark_execution_worker_failure(execution_id: int, message: str) -> None:
    """Keep one unexpected execution failure from stopping the serial queue."""
    now = utc_now()
    with transaction() as conn:
        item = conn.execute(
            "SELECT task_id, status FROM executions WHERE id = ?", (execution_id,)
        ).fetchone()
        if not item or item["status"] not in {"pending", "running"}:
            return
        summary = (message.strip() or "SpiderFly 执行器发生内部错误")[:5000]
        conn.execute(
            """
            UPDATE executions
            SET status = 'failed', ended_at = ?, error_message = ?
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (now, summary, execution_id),
        )
        conn.execute(
            "UPDATE tasks SET last_status = 'failed', last_run_at = ?, updated_at = ? WHERE id = ?",
            (now, now, item["task_id"]),
        )


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


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": "shared-central-python",
        "scheduler": "running"
        if _scheduler_task and not _scheduler_task.done()
        else "stopped",
        "queue_worker": "running"
        if _queue_worker_task and not _queue_worker_task.done()
        else "stopped",
        "environment_worker": "running"
        if _environment_worker_task and not _environment_worker_task.done()
        else "stopped",
    }


@app.post("/api/auth/login")
def login(
    payload: LoginPayload, request: Request, response: Response
) -> dict:
    user = authenticate_user(payload.username, payload.password)
    if not user:
        write_audit(
            request,
            None,
            "login_failed",
            target_type="user",
            summary=f"用户名：{payload.username.strip()[:50]}",
        )
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    token, max_age = create_session(int(user["id"]))
    set_session_cookie(response, token, max_age)
    write_audit(request, user, "login", target_type="user", target_id=user["id"])
    return public_user(user)


@app.get("/api/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return public_user(user)


@app.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: dict = Depends(current_user),
) -> None:
    write_audit(request, user, "logout", target_type="user", target_id=user["id"])
    clear_session(response, request.cookies.get("spiderfly_session"))


@app.post("/api/auth/change-password")
def update_password(
    payload: ChangePasswordPayload,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    try:
        change_password(user, payload.current_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        request, user, "change_password", target_type="user", target_id=user["id"]
    )
    refreshed = fetch_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    assert refreshed is not None
    return public_user(refreshed)


@app.get("/api/users")
def users_list(user: dict = Depends(admin_user)) -> list[dict]:
    del user
    return list_users()


@app.post("/api/users", status_code=201)
def users_create(
    payload: UserCreatePayload,
    request: Request,
    user: dict = Depends(admin_user),
) -> dict:
    try:
        created = create_user(
            payload.username, payload.display_name, payload.role, payload.password
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    write_audit(
        request,
        user,
        "create_user",
        target_type="user",
        target_id=created["id"],
        summary=f"创建用户 {created['username']}（{created['role']}）",
    )
    return created


@app.get("/api/audit-logs")
def audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(admin_user),
) -> list[dict]:
    del user
    return fetch_all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/apps")
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
        _public_app(item, include_private=user["role"] == "admin") for item in rows
    ]


@app.post("/api/apps", status_code=201)
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
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="这个任务名称已经存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    write_audit(
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


@app.post("/api/apps/{app_id}/rebuild")
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
    write_audit(
        request,
        user,
        "rebuild_environment",
        target_type="app",
        target_id=app_id,
        summary=f"重建 {updated['name']} 的 Python 环境",
    )
    return _public_app(updated, include_private=True)


@app.delete("/api/apps/{app_id}")
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
    write_audit(
        request,
        user,
        "delete_app",
        target_type="app",
        target_id=app_id,
        summary=f"彻底删除未绑定程序 {result['name']}",
    )
    return result


@app.get("/api/overview")
def overview(user: dict = Depends(ready_user)) -> dict:
    del user
    counts = fetch_one(
        """
        SELECT
            COUNT(*) AS total_tasks,
            SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled_tasks,
            SUM(CASE WHEN last_status = 'running' THEN 1 ELSE 0 END) AS running_tasks
        FROM tasks
        WHERE archived = 0
        """
    ) or {}
    today = fetch_one(
        """
        SELECT
            COUNT(*) AS total_runs,
            SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_runs,
            SUM(CASE WHEN status IN ('failed', 'timeout') THEN 1 ELSE 0 END) AS failed_runs,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS queued_runs
        FROM executions
        WHERE date(created_at) = date('now')
        """
    ) or {}
    return {**counts, **today}


@app.get("/api/settings")
def settings(user: dict = Depends(ready_user)) -> dict:
    del user
    value = FeishuSettings.from_env()
    return {
        "mode": "shared-central-python",
        "scheduler": "running"
        if _scheduler_task and not _scheduler_task.done()
        else "stopped",
        "queue_worker": "running"
        if _queue_worker_task and not _queue_worker_task.done()
        else "stopped",
        "scheduler_timezone": "Asia/Shanghai",
        "concurrency": 1,
        "collision_policy": "one-active-run-per-task",
        "task_timeout_seconds": DEFAULT_TASK_TIMEOUT_SECONDS,
        "work_directory_name": WORK_DIR.name,
        "host_preflight": "excel-and-managed-browser-port",
        "managed_browser_port": MANAGED_BROWSER_PORT,
        "feishu_configured": value.configured,
        "receiver_id_type": value.receiver_id_type,
        "receiver_masked": ("***" + value.receiver_id[-4:]) if value.receiver_id else "",
        "notification_policy": "one-final-message",
    }


@app.get("/api/tasks")
def list_tasks(user: dict = Depends(ready_user)) -> list[dict]:
    del user
    return [
        _public_task(task)
        for task in fetch_all(f"{TASK_SELECT} WHERE t.archived = 0 ORDER BY t.id DESC")
    ]


@app.post("/api/tasks", status_code=201)
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
                    timeout_seconds, notify_on_success, notify_on_failure,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    write_audit(
        request,
        user,
        "create_task",
        target_type="task",
        target_id=task_id,
        summary=f"创建任务 {payload.name}",
    )
    return _task_or_404(task_id, public=True)


@app.patch("/api/tasks/{task_id}")
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
                conn.execute(
                    """
                    UPDATE executions
                    SET status = 'cancelled', ended_at = ?, error_message = '任务停用，已取消排队'
                    WHERE task_id = ? AND status = 'pending'
                    """,
                    (now, task_id),
                )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="任务名称已存在") from exc

    write_audit(
        request,
        user,
        "update_task",
        target_type="task",
        target_id=task_id,
        summary=f"更新任务 {values.get('name', current['name'])}",
    )
    return _task_or_404(task_id, public=True)


@app.delete("/api/tasks/{task_id}", status_code=204)
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
    write_audit(
        request,
        user,
        "delete_task",
        target_type="task",
        target_id=task_id,
        summary=f"彻底删除任务 {result['name']}、程序 {result['app_name']} 和运行历史",
    )


@app.post("/api/tasks/{task_id}/run", response_model=RunResponse)
async def run_task(
    task_id: int,
    request: Request,
    user: dict = Depends(ready_user),
) -> RunResponse:
    execution_id = await _enqueue_task(task_id, "manual", int(user["id"]))
    task = _task_or_404(task_id)
    write_audit(
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


@app.post("/api/executions/{execution_id}/cancel")
def cancel_execution(
    execution_id: int,
    request: Request,
    user: dict = Depends(ready_user),
) -> dict:
    with transaction() as conn:
        item = conn.execute(
            """
            SELECT e.*, t.name AS task_name
            FROM executions e JOIN tasks t ON t.id = e.task_id
            WHERE e.id = ?
            """,
            (execution_id,),
        ).fetchone()
        if not item:
            raise HTTPException(status_code=404, detail="执行记录不存在")
        if item["status"] != "pending":
            raise HTTPException(status_code=409, detail="只能取消尚未开始的排队任务")
        now = utc_now()
        conn.execute(
            """
            UPDATE executions
            SET status = 'cancelled', ended_at = ?, error_message = '由操作员取消排队'
            WHERE id = ? AND status = 'pending'
            """,
            (now, execution_id),
        )
        conn.execute(
            "UPDATE tasks SET last_status = 'cancelled', updated_at = ? WHERE id = ?",
            (now, item["task_id"]),
        )
    write_audit(
        request,
        user,
        "cancel_execution",
        target_type="execution",
        target_id=execution_id,
        summary=f"取消 {item['task_name']} 的排队记录",
    )
    return {"id": execution_id, "status": "cancelled"}


@app.get("/api/executions")
def list_executions(
    task_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(ready_user),
) -> list[dict]:
    del user
    where = "WHERE e.task_id = ?" if task_id is not None else ""
    params = (task_id, limit) if task_id is not None else (limit,)
    return fetch_all(
        f"""
        SELECT
            e.id, e.task_id, e.status, e.trigger_source, e.requested_by,
            e.started_at, e.ended_at, e.duration_ms, e.exit_code,
            e.error_message, e.notification_status, e.notification_error,
            e.created_at, t.name AS task_name, t.app_name,
            COALESCE(u.display_name, u.username, '系统调度') AS requested_by_name,
            CASE WHEN e.status = 'pending' THEN (
                SELECT COUNT(*) FROM executions q
                WHERE q.status = 'pending' AND q.id <= e.id
            ) ELSE NULL END AS queue_position
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        LEFT JOIN users u ON u.id = e.requested_by
        {where}
        ORDER BY e.id DESC
        LIMIT ?
        """,
        params,
    )


@app.get("/api/executions/{execution_id}")
def get_execution(
    execution_id: int, user: dict = Depends(ready_user)
) -> dict:
    del user
    item = fetch_one(
        """
        SELECT
            e.*, t.name AS task_name, t.app_name,
            COALESCE(u.display_name, u.username, '系统调度') AS requested_by_name,
            CASE WHEN e.status = 'pending' THEN (
                SELECT COUNT(*) FROM executions q
                WHERE q.status = 'pending' AND q.id <= e.id
            ) ELSE NULL END AS queue_position
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        LEFT JOIN users u ON u.id = e.requested_by
        WHERE e.id = ?
        """,
        (execution_id,),
    )
    if not item:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    item.pop("script_path_snapshot", None)
    item.pop("python_path_snapshot", None)
    if item.get("retryable") is not None:
        item["retryable"] = bool(item["retryable"])
    return item


FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        requested = (FRONTEND_DIST / path).resolve()
        if requested.is_file() and FRONTEND_DIST.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(FRONTEND_DIST / "index.html")
