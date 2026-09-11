"""登录、退出与密码接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from .. import security
from ..database import fetch_one
from ..schemas import ChangePasswordPayload, LoginPayload
from ..security import (
    authenticate_user,
    change_password,
    clear_session,
    create_session,
    current_user,
    public_user,
    set_session_cookie,
)


router = APIRouter()


@router.post("/api/auth/login")
def login(
    payload: LoginPayload, request: Request, response: Response
) -> dict:
    user = authenticate_user(payload.username, payload.password)
    if not user:
        security.write_audit(
            request,
            None,
            "login_failed",
            target_type="user",
            summary=f"用户名：{payload.username.strip()[:50]}",
        )
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    token, max_age = create_session(int(user["id"]))
    set_session_cookie(response, token, max_age)
    security.write_audit(request, user, "login", target_type="user", target_id=user["id"])
    return public_user(user)


@router.get("/api/auth/me")
def me(user: dict = Depends(current_user)) -> dict:
    return public_user(user)


@router.post("/api/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: dict = Depends(current_user),
) -> None:
    security.write_audit(request, user, "logout", target_type="user", target_id=user["id"])
    clear_session(response, request.cookies.get("spiderfly_session"))


@router.post("/api/auth/change-password")
def update_password(
    payload: ChangePasswordPayload,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    try:
        change_password(user, payload.current_password, payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    security.write_audit(
        request, user, "change_password", target_type="user", target_id=user["id"]
    )
    refreshed = fetch_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    assert refreshed is not None
    return public_user(refreshed)
