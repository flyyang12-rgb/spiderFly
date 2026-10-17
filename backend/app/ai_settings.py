"""Local DeepSeek configuration; key display is restricted to the super-admin route."""
from __future__ import annotations

import base64
import binascii
import ctypes
import json
import os
import re
import tempfile
import threading
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import DATA_DIR

# Legacy defaults/snapshots remain valid; selectable choices come from the official catalog.
MODELS = ("deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp")
TURN_MAX_SECONDS = 20 * 60
DEFAULTS = {"model": MODELS[0], "max_seconds": TURN_MAX_SECONDS, "max_calls": 16, "max_tokens": 100000}
BASE_URL = "https://api.deepseek.com"
AI_DIR = DATA_DIR / "ai"
SECRET_PATH = DATA_DIR / "secrets" / "deepseek.key.dpapi"
_CONFIG_LOCK = threading.RLock()
_SAVE_LOCK = threading.Lock()
_PRIVATE_KEY = "_api_key_dpapi"
_MAX_MODELS = 500
_MAX_MODEL_LENGTH = 200


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


def _read_saved() -> dict:
    path = AI_DIR / "settings.json"
    if not path.exists():
        return {}
    try:
        saved = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        raise ValueError("DeepSeek 本机配置无法读取，请检查配置文件") from None
    if not isinstance(saved, dict):
        raise ValueError("DeepSeek 本机配置格式无效")
    return saved


def _valid_model(value) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= _MAX_MODEL_LENGTH
            and value == value.strip() and all(character.isprintable() and not character.isspace() for character in value))


def _validate_models(value) -> list[str]:
    if not isinstance(value, list) or len(value) > _MAX_MODELS or any(not _valid_model(model) for model in value):
        raise ValueError("DeepSeek 模型列表格式无效，请稍后重新获取")
    models = list(dict.fromkeys(value))
    if not models:
        raise ValueError("DeepSeek 未返回可选模型，请检查密钥权限")
    return models


def _input_key(value) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("密钥格式无效")
    key = value.strip()
    if key and any(not 33 <= ord(character) <= 126 for character in key):
        raise ValueError("密钥格式无效")
    return key


def _environment_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def _key_source(saved: dict) -> str:
    if _environment_key():
        return "environment"
    if _PRIVATE_KEY in saved or SECRET_PATH.is_file():
        return "saved"
    return "none"


def _effective_key(saved: dict, supplied: str = "") -> tuple[str, str]:
    environment = _environment_key()
    if environment:
        if supplied:
            raise ValueError("当前使用 DEEPSEEK_API_KEY 环境变量，请留空密钥输入后获取模型")
        return _input_key(environment), "environment"
    if supplied:
        return supplied, "input"
    try:
        if _PRIVATE_KEY in saved:
            encrypted = saved[_PRIVATE_KEY]
            if not isinstance(encrypted, str) or not 0 < len(encrypted) <= 24000:
                raise ValueError()
            content = base64.b64decode(encrypted, validate=True)
        elif SECRET_PATH.is_file():
            content = SECRET_PATH.read_bytes()
        else:
            raise ValueError("尚未配置 DeepSeek API 密钥")
        if not 0 < len(content) <= 16384:
            raise ValueError()
        key = _input_key(_crypt(content, decrypt=True).decode("utf-8"))
        if not key:
            raise ValueError()
        return key, "saved"
    except (OSError, UnicodeError, ValueError, binascii.Error):
        if _PRIVATE_KEY not in saved and not SECRET_PATH.is_file():
            raise ValueError("尚未配置 DeepSeek API 密钥") from None
        raise ValueError("已保存的 DeepSeek 密钥无法读取，请重新输入并保存") from None


def api_key() -> str:
    # A pending catalog check must not block callers reading the last committed key.
    with _CONFIG_LOCK:
        saved = _read_saved()
    return _effective_key(saved)[0]


def _values(saved: dict) -> dict:
    value = {key: saved.get(key, default) for key, default in DEFAULTS.items()}
    value["max_seconds"] = TURN_MAX_SECONDS
    if not _valid_model(value["model"]):
        raise ValueError("已保存的 DeepSeek 模型名称无效")
    for name, lower, upper in (("max_calls", 1, 128), ("max_tokens", 1000, 2000000)):
        if type(value[name]) is not int or not lower <= value[name] <= upper:
            raise ValueError("已保存的模型预算格式无效")
    return value


def _public_settings(saved: dict) -> dict:
    value = _values(saved)
    models, checked_at, catalog_source = [], None, "none"
    try:
        models = _validate_models(saved.get("models"))
        checked_at = saved.get("catalog_checked_at")
        if not isinstance(checked_at, str) or not 0 < len(checked_at) <= 64:
            raise ValueError()
        catalog_source = "saved"
    except ValueError:
        checked_at = None
        if _valid_model(saved.get("model")):
            models, catalog_source = [saved["model"]], "current_only"
        else:
            models = []
    source = _key_source(saved)
    # Never merge raw file contents into an API response: it also contains encrypted key material.
    value.update(provider="DeepSeek", base_url=BASE_URL, models=models,
                 catalog_checked_at=checked_at, catalog_source=catalog_source,
                 key_source=source, key_configured=source != "none")
    return value


def settings() -> dict:
    with _CONFIG_LOCK:
        return _public_settings(_read_saved())


def display_key() -> dict:
    """Only the dedicated super-admin route may return this presentation value."""
    with _CONFIG_LOCK:
        saved = _read_saved()
        source = _key_source(saved)
        return {"api_key": _effective_key(saved)[0] if source != "none" else "",
                "key_source": source}


def _official_models(key: str) -> list[str]:
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.get(BASE_URL + "/models", headers={"Authorization": "Bearer " + key},
                                  timeout=(10, 20), allow_redirects=False)
            if 300 <= response.status_code < 400:
                raise ValueError("DeepSeek 模型列表拒绝重定向，请检查官方连接")
            if not response.ok:
                raise ValueError(f"DeepSeek 模型列表请求失败（HTTP {response.status_code}），请检查密钥和网络")
            try:
                data = response.json()
            except ValueError:
                raise ValueError("DeepSeek 模型列表格式无效，请稍后重新获取") from None
    except requests.RequestException:
        raise ValueError("DeepSeek 模型列表连接失败，请检查网络和密钥") from None
    if not isinstance(data, dict) or not isinstance(data.get("data"), list) or len(data["data"]) > _MAX_MODELS:
        raise ValueError("DeepSeek 模型列表格式无效，请稍后重新获取")
    if any(not isinstance(row, dict) or "id" not in row for row in data["data"]):
        raise ValueError("DeepSeek 模型列表格式无效，请稍后重新获取")
    return _validate_models([row["id"] for row in data["data"]])


def fetch_models(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {"api_key"}:
        raise ValueError("模型列表请求字段不受支持")
    supplied = _input_key(payload.get("api_key", ""))
    with _CONFIG_LOCK:
        saved = _read_saved()
    key, source = _effective_key(saved, supplied)
    models = _official_models(key)
    return {"connected": True, "models": models, "key_source": source}


def save_settings(payload: dict) -> dict:
    if not isinstance(payload, dict) or set(payload) - {*DEFAULTS, "api_key"}:
        raise ValueError("模型配置字段不受支持")
    supplied = _input_key(payload.get("api_key", ""))
    # Saves serialize their complete read/check/commit transaction. Readers only
    # share the short snapshot/commit lock, so provider I/O cannot stall workers.
    with _SAVE_LOCK:
        with _CONFIG_LOCK:
            saved = _read_saved()
            environment_before = _environment_key()
        current = _values(saved)
        current.update({key: payload[key] for key in DEFAULTS if key in payload})
        if not _valid_model(current["model"]):
            raise ValueError("请选择有效的 DeepSeek 模型")
        for name, lower, upper in (("max_seconds", 30, 7200), ("max_calls", 1, 128), ("max_tokens", 1000, 2000000)):
            if type(current[name]) is not int or not lower <= current[name] <= upper:
                raise ValueError(f"{name} 必须在 {lower} 至 {upper} 之间")
        current["max_seconds"] = TURN_MAX_SECONDS
        if _PRIVATE_KEY in saved:
            current[_PRIVATE_KEY] = saved[_PRIVATE_KEY]
        for name in ("models", "catalog_checked_at"):
            if name in saved:
                current[name] = saved[name]
        if "model" in payload or supplied:
            key, _ = _effective_key(saved, supplied)
            models = _official_models(key)
            if current["model"] not in models:
                raise ValueError("所选模型不在当前密钥可用的官方列表中，请重新获取模型列表")
            current.update(models=models, catalog_checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        if supplied:
            try:
                current[_PRIVATE_KEY] = base64.b64encode(_crypt(supplied.encode("utf-8"), decrypt=False)).decode("ascii")
            except (OSError, ValueError):
                raise ValueError("无法使用当前 Windows 账号保存模型密钥") from None
        encoded = json.dumps(current, ensure_ascii=False).encode("utf-8")
        with _CONFIG_LOCK:
            if ("model" in payload or supplied) and _environment_key() != environment_before:
                raise ValueError("DeepSeek 环境密钥在校验期间发生变化，请重新获取模型并保存")
            atomic_write(AI_DIR / "settings.json", encoded)
            return _public_settings(current)


def model_request(messages: list, tools: list, config: dict, *, remaining: int) -> dict:
    # A new session with environment proxy discovery disabled prevents forwarding credentials
    # to an inherited debugging proxy. The official endpoint is not user/model configurable.
    with requests.Session() as client:
        client.trust_env = False
        response = client.post(BASE_URL + "/chat/completions", headers={"Authorization": "Bearer " + api_key()},
            json={"model": config["model"], "messages": messages, "tools": tools,
                  "thinking": {"type": "disabled"}, "max_tokens": min(8192, remaining)}, timeout=(10, 90), allow_redirects=False)
        if 300 <= response.status_code < 400 or not response.ok:
            raise ValueError(f"DeepSeek 请求失败（HTTP {response.status_code}）")
        data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise ValueError("DeepSeek 未返回有效响应")
    return data


def check_connection() -> dict:
    with _CONFIG_LOCK:
        saved = _read_saved()
        selected = _values(saved)["model"]
    key, source = _effective_key(saved)
    models = _official_models(key)
    return {"connected": True, "models": models, "key_source": source,
            "selected_model_available": selected in models}


def account_balance() -> dict:
    """Read the effective saved/environment account; never accept a draft key."""
    with _CONFIG_LOCK:
        key, _ = _effective_key(_read_saved())
    try:
        with requests.Session() as client:
            client.trust_env = False
            response = client.get(BASE_URL + "/user/balance",
                                  headers={"Authorization": "Bearer " + key},
                                  timeout=(10, 20), allow_redirects=False)
            if not 200 <= response.status_code < 300:
                raise ValueError(f"DeepSeek 余额查询失败（HTTP {response.status_code}），请检查密钥和网络")
            try:
                data = response.json()
            except ValueError:
                raise ValueError("DeepSeek 余额格式无效，请稍后重试") from None
    except requests.RequestException:
        raise ValueError("DeepSeek 余额连接失败，请检查网络后刷新") from None
    invalid = "DeepSeek 余额格式无效，请稍后重试"
    if not isinstance(data, dict) or type(data.get("is_available")) is not bool:
        raise ValueError(invalid)
    rows = data.get("balance_infos")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2:
        raise ValueError(invalid)
    balances, currencies = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("currency") not in {"CNY", "USD"}:
            raise ValueError(invalid)
        currency = row["currency"]
        if currency in currencies:
            raise ValueError(invalid)
        currencies.add(currency)
        clean = {"currency": currency}
        for field in ("total_balance", "granted_balance", "topped_up_balance"):
            value = row.get(field)
            # Keep decimal strings intact: do not round money through binary floats.
            if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,20}(?:\.\d{1,20})?", value, re.ASCII):
                raise ValueError(invalid)
            clean[field] = value
        balances.append(clean)
    with _CONFIG_LOCK:
        if _effective_key(_read_saved())[0] != key:
            raise ValueError("查询期间密钥已改变，请重新刷新余额")
    return {"is_available": data["is_available"], "balance_infos": balances,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
