from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request, Response, status

from .config import COOKIE_SECURE, DATA_DIR, SESSION_COOKIE_NAME, SESSION_HOURS
from .database import execute, fetch_all, fetch_one, utc_now


PASSWORD_ITERATIONS = 600_000
BOOTSTRAP_FILE = DATA_DIR / "首次登录信息.txt"


def _hash_password(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 10:
        raise ValueError("密码至少需要 10 个字符")
    if len(password) > 200:
        raise ValueError("密码长度不能超过 200 个字符")
    if password.isspace():
        raise ValueError("密码不能只包含空格")


def _public_user(user: dict) -> dict:
    return {
        "id": int(user["id"]),
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "active": bool(user["active"]),
        "must_change_password": bool(user["must_change_password"]),
        "last_login_at": user.get("last_login_at"),
        "created_at": user.get("created_at"),
    }


def ensure_bootstrap_admin() -> Path | None:
    if fetch_one("SELECT id FROM users LIMIT 1"):
        return BOOTSTRAP_FILE if BOOTSTRAP_FILE.exists() else None
    password = secrets.token_urlsafe(15)
    now = utc_now()
    execute(
        """
        INSERT INTO users (
            username, display_name, password_hash, role, active,
            must_change_password, created_at, updated_at
        ) VALUES ('admin', '系统管理员', ?, 'admin', 1, 1, ?, ?)
        """,
        (_hash_password(password), now, now),
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOOTSTRAP_FILE.write_text(
        "SpiderFly 首次登录信息\n"
        "======================\n"
        "用户名：admin\n"
        f"临时密码：{password}\n\n"
        "首次登录后必须修改密码；修改成功后此文件会自动删除。\n",
        encoding="utf-8",
    )
    return BOOTSTRAP_FILE


def authenticate_user(username: str, password: str) -> dict | None:
    user = fetch_one(
        "SELECT * FROM users WHERE lower(username) = lower(?)", (username.strip(),)
    )
    if not user or not user["active"] or not verify_password(password, user["password_hash"]):
        return None
    execute(
        "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
        (utc_now(), utc_now(), user["id"]),
    )
    user["last_login_at"] = utc_now()
    return user


def create_session(user_id: int) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=SESSION_HOURS)
    execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
    execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token_hash, user_id, expires.isoformat(), now.isoformat()),
    )
    return token, int(timedelta(hours=SESSION_HOURS).total_seconds())


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_session(response: Response, token: str | None = None) -> None:
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def _session_user(token: str | None) -> dict | None:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = fetch_one(
        """
        SELECT u.*
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1
        """,
        (token_hash, datetime.now(timezone.utc).isoformat()),
    )
    return row


def current_user(request: Request) -> dict:
    user = _session_user(request.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def ready_user(user: dict = Depends(current_user)) -> dict:
    if user["must_change_password"]:
        raise HTTPException(status_code=403, detail="首次登录请先修改密码")
    return user


def admin_user(user: dict = Depends(ready_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def public_user(user: dict) -> dict:
    return _public_user(user)


def list_users() -> list[dict]:
    return [
        _public_user(item)
        for item in fetch_all("SELECT * FROM users ORDER BY active DESC, id ASC")
    ]


def create_user(
    username: str,
    display_name: str,
    role: str,
    password: str,
) -> dict:
    username = username.strip().lower()
    display_name = display_name.strip()
    if not username or len(username) > 50 or not all(
        character.isalnum() or character in {"_", "-", "."} for character in username
    ):
        raise ValueError("用户名只能包含字母、数字、点、下划线和短横线")
    if not display_name or len(display_name) > 100:
        raise ValueError("显示名称不能为空且不能超过 100 个字符")
    if role not in {"admin", "operator"}:
        raise ValueError("角色只能是管理员或操作员")
    validate_password_strength(password)
    now = utc_now()
    user_id = execute(
        """
        INSERT INTO users (
            username, display_name, password_hash, role, active,
            must_change_password, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, 1, ?, ?)
        """,
        (username, display_name, _hash_password(password), role, now, now),
    )
    user = fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))
    assert user is not None
    return _public_user(user)


def change_password(user: dict, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user["password_hash"]):
        raise ValueError("当前密码不正确")
    validate_password_strength(new_password)
    if hmac.compare_digest(current_password.encode("utf-8"), new_password.encode("utf-8")):
        raise ValueError("新密码不能与当前密码相同")
    execute(
        """
        UPDATE users
        SET password_hash = ?, must_change_password = 0, updated_at = ?
        WHERE id = ?
        """,
        (_hash_password(new_password), utc_now(), user["id"]),
    )
    if user["username"] == "admin" and BOOTSTRAP_FILE.exists():
        BOOTSTRAP_FILE.unlink(missing_ok=True)


def write_audit(
    request: Request,
    user: dict | None,
    action: str,
    *,
    target_type: str = "",
    target_id: int | None = None,
    summary: str = "",
) -> None:
    ip = request.client.host if request.client else ""
    execute(
        """
        INSERT INTO audit_logs (
            user_id, username, action, target_type, target_id,
            summary, ip_address, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user["id"] if user else None,
            user["username"] if user else "system",
            action[:100],
            target_type[:50],
            target_id,
            summary[:1000],
            ip[:100],
            utc_now(),
        ),
    )
