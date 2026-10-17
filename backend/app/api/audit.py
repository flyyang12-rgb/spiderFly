"""操作审计查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ..database import connection, fetch_all
from ..security import admin_user


router = APIRouter()


@router.get("/api/audit-logs/history")
def audit_history(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(admin_user),
) -> dict:
    del user
    with connection() as conn:
        # Count and rows share one read snapshot, without taking a writer lock.
        conn.execute("BEGIN")
        total = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]
        page = min(page, max(1, (total + page_size - 1) // page_size))
        items = conn.execute(
            "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        ).fetchall()
    return {"items": [dict(item) for item in items], "total": total,
            "page": page, "page_size": page_size}


@router.get("/api/audit-logs")
def audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(admin_user),
) -> list[dict]:
    del user
    return fetch_all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
