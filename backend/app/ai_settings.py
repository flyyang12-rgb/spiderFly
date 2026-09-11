"""Local DeepSeek configuration. Secrets never enter task environments or API replies."""
from __future__ import annotations

import ctypes
import json
import os
import re
import tempfile
from ctypes import wintypes
from pathlib import Path

import requests

from .config import DATA_DIR

MODELS = ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp")
TURN_MAX_SECONDS = 20 * 60
DEFAULTS = {"model": MODELS[0], "max_seconds": TURN_MAX_SECONDS, "max_calls": 16, "max_tokens": 100000}
BASE_URL = "https://api.deepseek.com"
AI_DIR = DATA_DIR / "ai"
SECRET_PATH = DATA_DIR / "secrets" / "deepseek.key.dpapi"


def redact(value: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[密钥已隐藏]", str(value))


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(content: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise ValueError("本机密钥保存需要 Windows DPAPI；其他系统请配置 DEEPSEEK_API_KEY")
    buffer = ctypes.create_string_buffer(content)
    source = _Blob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        arguments = (ctypes.byref(source), "SpiderFly DeepSeek", None, None, None, 1, ctypes.byref(target))
    operation.restype = wintypes.BOOL
    try:
        if not operation(*arguments):
            raise ValueError("无法使用当前 Windows 账号读取或保存模型密钥")
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.memset(buffer, 0, len(content))
        if target.data:
            ctypes.memset(target.data, 0, target.size)
            kernel.LocalFree(target.data)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".ai-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def api_key() -> str:
    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if value:
        return value
    if not SECRET_PATH.is_file():
        raise ValueError("尚未配置 DeepSeek API 密钥")
    return _crypt(SECRET_PATH.read_bytes(), decrypt=True).decode("utf-8")


def settings() -> dict:
    path = AI_DIR / "settings.json"
    value = {**DEFAULTS, **(json.loads(path.read_text("utf-8")) if path.exists() else {})}
    value["max_seconds"] = TURN_MAX_SECONDS
    value.update(provider="DeepSeek", base_url=BASE_URL, models=list(MODELS),
                 key_configured=bool(os.getenv("DEEPSEEK_API_KEY") or SECRET_PATH.is_file()))
    return value


def save_settings(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {*DEFAULTS, "api_key"}:
        raise ValueError("模型配置字段不受支持")
    current = {key: settings()[key] for key in DEFAULTS}
    current.update({key: payload[key] for key in DEFAULTS if key in payload})
    if current["model"] not in MODELS:
        raise ValueError("请选择支持的 DeepSeek 模型")
    for name, lower, upper in (("max_seconds", 30, 7200), ("max_calls", 1, 128), ("max_tokens", 1000, 2000000)):
        if type(current[name]) is not int or not lower <= current[name] <= upper:
            raise ValueError(f"{name} 必须在 {lower} 至 {upper} 之间")
    current["max_seconds"] = TURN_MAX_SECONDS
    secret = payload.get("api_key", "")
    if not isinstance(secret, str) or len(secret) > 500:
        raise ValueError("密钥格式无效")
    if secret.strip():
        atomic_write(SECRET_PATH, _crypt(secret.strip().encode(), decrypt=False))
    atomic_write(AI_DIR / "settings.json", json.dumps(current, ensure_ascii=False).encode())
    return settings()


def model_request(messages: list, tools: list, config: dict, *, remaining: int) -> dict:
    # A new session with environment proxy discovery disabled prevents forwarding credentials
    # to an inherited debugging proxy. The official endpoint is not user/model configurable.
    with requests.Session() as client:
        client.trust_env = False
        response = client.post(BASE_URL + "/chat/completions", headers={"Authorization": "Bearer " + api_key()},
            json={"model": config["model"], "messages": messages, "tools": tools,
                  "thinking": {"type": "disabled"}, "max_tokens": min(8192, remaining)}, timeout=(10, 90))
        if not response.ok:
            raise ValueError(f"DeepSeek 请求失败（HTTP {response.status_code}）")
        data = response.json()
    if not isinstance(data.get("choices"), list) or not data["choices"]:
        raise ValueError("DeepSeek 未返回有效响应")
    return data


def check_connection() -> dict:
    with requests.Session() as client:
        client.trust_env = False
        response = client.get(BASE_URL + "/models", headers={"Authorization": "Bearer " + api_key()}, timeout=(10, 20))
        if not response.ok:
            raise ValueError(f"DeepSeek 连接失败（HTTP {response.status_code}）")
        available = [row["id"] for row in response.json().get("data", []) if row.get("id") in MODELS]
    return {"connected": True, "models": available}
