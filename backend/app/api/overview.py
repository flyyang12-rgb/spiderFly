"""工作台统计接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from ..database import fetch_one, utc_now
from ..scheduling import shanghai_day_utc_bounds
from ..security import ready_user


router = APIRouter()


@router.get("/api/overview")
def overview(user: dict = Depends(ready_user)) -> dict:
    del user
    today_start, tomorrow_start = shanghai_day_utc_bounds(utc_now())
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
        WHERE created_at >= ? AND created_at < ?
        """,
        (today_start, tomorrow_start),
    ) or {}
    return {**counts, **today}
