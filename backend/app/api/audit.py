"""操作审计查询接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from ..database import fetch_all
from ..security import admin_user


router = APIRouter()


@router.get("/api/audit-logs")
def audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    user: dict = Depends(admin_user),
) -> list[dict]:
    del user
    return fetch_all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,))
