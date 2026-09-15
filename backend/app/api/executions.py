"""运行记录、停止与产物下载接口。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from .. import maintenance, security
from ..database import TASK_EXECUTION_STATUS_SQL, fetch_all, fetch_one, transaction, utc_now
from ..execution_artifacts import (
    ArtifactDownloadResponse,
    MAX_PATH_CHARS,
    TERMINAL_STATUSES,
    list_artifacts,
    open_artifact,
)
from ..collection_progress import progress_view
from ..runner import execution_stop_requested, request_execution_stop
from ..security import admin_user, ready_user, write_audit


router = APIRouter()


EXECUTION_FILTER_STATUSES = {
    "pending", "running", "success", "failed", "timeout", "cancelled",
}


SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


@router.post("/api/executions/{execution_id}/stop", status_code=202)
async def force_stop_execution(
    execution_id: int, request: Request, user: dict = Depends(admin_user),
) -> dict:
    item = await asyncio.to_thread(fetch_one,
        "SELECT e.status, t.name AS task_name FROM executions e JOIN tasks t ON t.id=e.task_id WHERE e.id=?",
        (execution_id,))
    if not item:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if item["status"] != "running":
        raise HTTPException(status_code=409, detail="只能强制停止正在运行的任务")
    try:
        accepted = request_execution_stop(execution_id, user["username"])
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if accepted:
        await asyncio.to_thread(write_audit, request, user, "force_stop_execution",
            target_type="execution", target_id=execution_id,
            summary=f"请求强制停止 {item['task_name']}，执行记录 #{execution_id}")
    return {"id": execution_id, "status": "running", "stop_requested": True,
            "message": "已请求强制停止，正在等待进程退出和清理完成"}


@router.post("/api/executions/{execution_id}/cancel")
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
            f"UPDATE tasks SET last_status = {TASK_EXECUTION_STATUS_SQL}, updated_at = ? WHERE id = ?",
            ("cancelled", now, item["task_id"]),
        )
    security.write_audit(
        request,
        user,
        "cancel_execution",
        target_type="execution",
        target_id=execution_id,
        summary=f"取消 {item['task_name']} 的排队记录",
    )
    return {"id": execution_id, "status": "cancelled"}


@router.get("/api/executions")
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


def _execution_date_boundary(value: date, *, next_day: bool = False) -> str:
    target = value + timedelta(days=1) if next_day else value
    local_midnight = datetime.combine(target, time.min, SHANGHAI_TIMEZONE)
    return local_midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/api/executions/history")
def list_execution_history(
    task_name: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=20),
    requester: str = Query(default="", max_length=100),
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    user: dict = Depends(ready_user),
) -> dict:
    del user
    normalized_status = status.strip().lower()
    if normalized_status and normalized_status not in EXECUTION_FILTER_STATUSES:
        raise HTTPException(status_code=400, detail="运行状态筛选值无效")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")

    clauses: list[str] = []
    params: list[object] = []
    normalized_task_name = task_name.strip()
    normalized_requester = requester.strip()
    if normalized_task_name:
        clauses.append("t.name LIKE ? ESCAPE '\\'")
        params.append(_contains_pattern(normalized_task_name))
    if normalized_status:
        clauses.append("e.status = ?")
        params.append(normalized_status)
    if normalized_requester:
        clauses.append(
            "COALESCE(u.display_name, u.username, '系统调度') LIKE ? ESCAPE '\\'"
        )
        params.append(_contains_pattern(normalized_requester))
    if date_from:
        clauses.append("e.created_at >= ?")
        params.append(_execution_date_boundary(date_from))
    if date_to:
        clauses.append("e.created_at < ?")
        params.append(_execution_date_boundary(date_to, next_day=True))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total_row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM executions e
        JOIN tasks t ON t.id = e.task_id
        LEFT JOIN users u ON u.id = e.requested_by
        {where}
        """,
        tuple(params),
    ) or {"total": 0}
    total = int(total_row["total"] or 0)
    offset = (page - 1) * page_size
    items = fetch_all(
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
        ORDER BY
            CASE e.status WHEN 'running' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
            CASE WHEN e.status = 'pending' THEN e.id END ASC,
            e.id DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@router.get("/api/executions/{execution_id}")
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
    item["collection_progress"] = progress_view(item.get("collection_progress"))
    item["stop_requested"] = item["status"] == "running" and execution_stop_requested(execution_id)
    item.pop("script_path_snapshot", None)
    item.pop("maintenance_snapshot", None)
    item["maintenance"] = maintenance.execution_note(execution_id)
    item.pop("python_path_snapshot", None)
    if item.get("retryable") is not None:
        item["retryable"] = bool(item["retryable"])
    item["artifacts"] = (
        list_artifacts(execution_id)
        if item["status"] in TERMINAL_STATUSES
        else {"files": [], "truncated": False, "error": ""}
    )
    return item


@router.api_route(
    "/api/executions/{execution_id}/artifacts/download", methods=["GET", "HEAD"]
)
def download_execution_artifact(
    execution_id: int,
    request: Request,
    path: str = Query(min_length=1, max_length=MAX_PATH_CHARS),
    user: dict = Depends(ready_user),
) -> Response:
    del user
    item = fetch_one(
        """
        SELECT e.status FROM executions e
        JOIN tasks t ON t.id = e.task_id WHERE e.id = ?
        """,
        (execution_id,),
    )
    if not item:
        raise HTTPException(status_code=404, detail="执行记录不存在")
    if item["status"] not in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="运行结束后才可下载文件")
    try:
        stream = open_artifact(execution_id, path)
        try:
            response = ArtifactDownloadResponse(stream, path.rsplit("/", 1)[-1])
        except BaseException:
            stream.close()
            raise
    except (ValueError, OSError):
        raise HTTPException(status_code=404, detail="文件不存在或不可下载") from None
    if request.method == "HEAD":
        stream.close()
        return Response(headers=dict(response.headers))
    return response
