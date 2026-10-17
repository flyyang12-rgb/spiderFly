"""Runtime group-notification configuration, sealed with the service account's DPAPI."""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from urllib.parse import urlsplit

from .ai_settings import _crypt, atomic_write
from .config import DATA_DIR


SETTINGS_PATH = DATA_DIR / "secrets" / "feishu.settings.dpapi"
_LOCK = threading.RLock()
_ENV_FIELDS = {
    "app_id": "FEISHU_APP_ID", "app_secret": "FEISHU_APP_SECRET",
    "receiver_id": "FEISHU_RECEIVER_ID", "webhook_url": "FEISHU_WEBHOOK_URL",
    "webhook_secret": "FEISHU_WEBHOOK_SECRET",
}
_READ_ERROR = "飞书本机配置无法读取，请清除后重新配置"
_DEFAULTS = {"mode": "webhook", "webhook_url": "", "webhook_secret": "",
             "app_id": "", "app_secret": "", "receiver_id": "", "receiver_id_type": "mobile"}


class ConfigurationConflict(ValueError):
    pass


def _environment() -> dict:
    values = {key: os.getenv(name, "").strip() for key, name in _ENV_FIELDS.items()}
    receiver_type = os.getenv("FEISHU_RECEIVER_ID_TYPE", "open_id").strip()
    values.update(receiver_id_type=receiver_type if receiver_type in {"open_id", "mobile"} else "open_id",
                  base_url=os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/"))
    return values


def _has_environment(values: dict) -> bool:
    # Treat the deployment as one configuration: never combine half of an
    # application recipient with a different, locally saved audience.
    return any(values[name] for name in _ENV_FIELDS)


def _snapshot() -> tuple[bytes | None, str]:
    try:
        content = SETTINGS_PATH.read_bytes()
    except FileNotFoundError:
        return None, "none"
    except OSError:
        raise ValueError(_READ_ERROR) from None
    return content, hashlib.sha256(content).hexdigest()


def _saved(content: bytes | None) -> dict:
    if content is None:
        return dict(_DEFAULTS)
    try:
        if not 0 < len(content) <= 16384:
            raise ValueError()
        value = json.loads(_crypt(content, decrypt=True).decode("utf-8"))
        if (not isinstance(value, dict) or set(value) != {"version", *_DEFAULTS}
                or type(value["version"]) is not int or value["version"] != 1):
            raise ValueError()
        if value["mode"] not in {"webhook", "application"} or value["receiver_id_type"] not in {"mobile", "open_id"}:
            raise ValueError()
        value["webhook_url"] = _webhook(value["webhook_url"]) if value["webhook_url"] else ""
        for name in ("webhook_secret", "app_secret"):
            value[name] = _secret(value[name])
        for name in ("app_id", "receiver_id"):
            value[name] = _identifier(value[name])
        _complete(value)
        return {key: value[key] for key in _DEFAULTS}
    except Exception:
        # Neither ciphertext, webhook tokens, DPAPI details nor file paths
        # belong in HTTP errors or execution notification logs.
        raise ValueError(_READ_ERROR) from None


def _webhook(value) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Webhook 地址格式无效")
    value = value.strip()
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError("Webhook 地址格式无效")
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in {"open.feishu.cn", "open.larksuite.com"}
                 and url.port in {None, 443} and not url.username and not url.password
                 and not url.query and not url.fragment
                 and re.fullmatch(r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]{1,200}", url.path))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请输入飞书群机器人的 HTTPS Webhook 地址")
    return value


def _secret(value) -> str:
    if not isinstance(value, str) or len(value) > 500:
        raise ValueError("签名密钥格式无效")
    value = value.strip()
    if any(not 33 <= ord(character) <= 126 for character in value):
        raise ValueError("签名密钥格式无效")
    return value


def _identifier(value) -> str:
    if (not isinstance(value, str) or len(value) > 200
            or any(not character.isprintable() or character.isspace() for character in value)):
        raise ValueError("应用或收件人格式无效")
    return value


def _complete(value: dict) -> None:
    if value["mode"] == "webhook":
        _webhook(value["webhook_url"])
    else:
        if not value["app_id"] or not value["app_secret"] or not value["receiver_id"]:
            raise ValueError("请填写 App ID、App Secret 和收件人")
        if value["receiver_id_type"] == "mobile":
            valid = re.fullmatch(r"\+?[0-9]{6,20}", value["receiver_id"])
        else:
            valid = re.fullmatch(r"ou_[A-Za-z0-9_-]{1,197}", value["receiver_id"])
        if not valid:
            raise ValueError("收件人手机号或 Open ID 格式无效")


def effective_settings() -> dict:
    """One immutable snapshot per notifier; no process-environment mutation."""
    with _LOCK:
        values = _environment()
        if not _has_environment(values):
            content, _ = _snapshot()
            local = _saved(content)
            if content is not None:
                if local["mode"] == "webhook":
                    values.update(webhook_url=local["webhook_url"], webhook_secret=local["webhook_secret"])
                else:
                    values.update({key: local[key] for key in ("app_id", "app_secret", "receiver_id", "receiver_id_type")})
        return values


def public_settings(*, include_details: bool = False) -> dict:
    with _LOCK:
        values = _environment()
        environment = _has_environment(values)
        error = ""
        if environment:
            revision, source = "environment", "environment"
            mode = "webhook" if values["webhook_url"] else "application" if any(values[name] for name in ("app_id", "app_secret", "receiver_id")) else "webhook"
        else:
            content, revision = _snapshot()
            source = "saved" if content is not None else "none"
            try:
                values.update(_saved(content))
            except ValueError:
                error = _READ_ERROR
            mode = values.get("mode", "webhook")
        webhook = bool(values["webhook_url"])
        application = bool(values["app_id"] and values["app_secret"] and values["receiver_id"])
        result = {"configured": not error and (webhook if mode == "webhook" else application), "source": source,
                "read_only": environment, "mode": mode,
                "webhook_url_configured": webhook, "webhook_secret_configured": bool(values["webhook_secret"]),
                "app_secret_configured": bool(values["app_secret"]),
                "receiver_id_type": values["receiver_id_type"],
                "revision": revision, "configuration_error": error}
        if include_details:
            result.update(app_id=values["app_id"], receiver_id=values["receiver_id"])
        return result


def _validate_payload(payload, *, clear: bool = False) -> dict:
    allowed = {"revision"} if clear else {"revision", *_DEFAULTS, "clear_webhook_secret"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("飞书配置字段不受支持")
    revision = payload.get("revision")
    if not isinstance(revision, str) or (revision not in {"none", "environment"} and not re.fullmatch(r"[0-9a-f]{64}", revision)):
        raise ValueError("请重新加载飞书配置后保存")
    if not clear:
        for name in ("webhook_url", "webhook_secret", "app_id", "app_secret", "receiver_id"):
            limit = 2048 if name == "webhook_url" else 500 if name.endswith("secret") else 200
            if name in payload and (not isinstance(payload[name], str) or len(payload[name]) > limit):
                raise ValueError("飞书配置格式无效")
        if payload.get("mode") not in {"webhook", "application"}:
            raise ValueError("请选择群通知或个人通知")
        if "receiver_id_type" in payload and payload["receiver_id_type"] not in {"mobile", "open_id"}:
            raise ValueError("收件人类型无效")
        if "clear_webhook_secret" in payload and type(payload["clear_webhook_secret"]) is not bool:
            raise ValueError("清除签名密钥选项无效")
    return payload


def _check_mutation(payload: dict) -> bytes | None:
    if _has_environment(_environment()):
        raise ConfigurationConflict("当前使用部署环境配置，请在部署端修改")
    content, revision = _snapshot()
    if revision != payload["revision"]:
        raise ConfigurationConflict("配置已被其他操作修改，请重新加载")
    return content


def save_settings(payload: dict) -> dict:
    payload = _validate_payload(payload)
    with _LOCK:
        current = _saved(_check_mutation(payload))
        current["mode"] = payload["mode"]
        supplied_webhook = payload.get("webhook_url", "").strip()
        if supplied_webhook:
            current["webhook_url"] = _webhook(supplied_webhook)
        supplied_secret = _secret(payload.get("webhook_secret", ""))
        if supplied_secret and payload.get("clear_webhook_secret"):
            raise ValueError("不能同时替换和清除签名密钥")
        current["webhook_secret"] = "" if payload.get("clear_webhook_secret") else supplied_secret or current["webhook_secret"]
        supplied_app_secret = _secret(payload.get("app_secret", ""))
        app_id = _identifier(payload.get("app_id", current["app_id"]).strip())
        if app_id != current["app_id"] and not supplied_app_secret:
            raise ValueError("App ID 已修改，请同时填写 App Secret")
        current.update(app_id=app_id, app_secret=supplied_app_secret or current["app_secret"],
                       receiver_id=_identifier(payload.get("receiver_id", current["receiver_id"]).strip()),
                       receiver_id_type=payload.get("receiver_id_type", current["receiver_id_type"]))
        _complete(current)
        value = {"version": 1, **current}
        try:
            encoded = _crypt(json.dumps(value, ensure_ascii=False).encode("utf-8"), decrypt=False)
            atomic_write(SETTINGS_PATH, encoded)
        except Exception:
            raise ValueError("飞书配置保存失败，请检查当前 Windows 账号及配置目录") from None
        return public_settings(include_details=True)


def clear_settings(payload: dict) -> dict:
    payload = _validate_payload(payload, clear=True)
    with _LOCK:
        _check_mutation(payload)
        try:
            SETTINGS_PATH.unlink(missing_ok=True)
        except OSError:
            raise ValueError("飞书配置清除失败，请检查配置目录") from None
        return public_settings(include_details=True)
