"""成员管理接口。"""

from __future__ import annotations

import sqlite3
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from .. import security
from ..schemas import UserCreatePayload, UserUpdatePayload
from ..security import (
    admin_user,
    create_user,
    delete_user,
    list_users,
    super_admin_user,
    update_user,
)


router = APIRouter()


@router.get("/api/users")
def users_list(user: dict = Depends(admin_user)) -> list[dict]:
    del user
    return list_users()


@router.post("/api/users", status_code=201)
def users_create(
    payload: UserCreatePayload,
    request: Request,
    user: dict = Depends(super_admin_user),
) -> dict:
    try:
        created = create_user(
            payload.username, payload.display_name, payload.role, payload.password
        )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    security.write_audit(
        request,
        user,
        "create_user",
        target_type="user",
        target_id=created["id"],
        summary=f"创建用户 {created['username']}（{created['role']}）",
    )
    return created


@router.patch("/api/users/{user_id}")
def users_update(
    user_id: int, payload: UserUpdatePayload, request: Request,
    user: dict = Depends(super_admin_user),
) -> dict:
    changes = payload.model_dump(exclude_unset=True, exclude={"version"})
    try:
        updated = update_user(user, user_id, changes, payload.version)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    fields = {"username": "账号", "display_name": "显示名称", "role": "角色", "active": "启用状态", "password": "重置密码"}
    security.write_audit(request, user, "update_user", target_type="user", target_id=user_id,
                summary=f"修改成员 {updated['username']}：" + "、".join(fields[key] for key in changes))
    return updated


@router.delete("/api/users/{user_id}", status_code=204)
def users_delete(
    user_id: int, request: Request, version: int = Query(ge=1),
    user: dict = Depends(super_admin_user),
) -> None:
    try:
        deleted = delete_user(user, user_id, version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    security.write_audit(request, user, "delete_user", target_type="user", target_id=user_id,
                summary=f"删除成员 {deleted['username']}，保留历史记录")
