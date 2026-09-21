"""Signed, non-redirecting protocol v1 transport."""

import base64
import hashlib
import json
import time
from urllib.parse import unquote, urlsplit
import uuid

import requests


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        self.status = status
        super().__init__(f"主控 HTTP {status}: {detail[:500]}")


def canonical(timestamp: str, nonce: str, method: str, path: str, body: bytes) -> bytes:
    return "\n".join((timestamp, nonce, method.upper(), path,
                      hashlib.sha256(body).hexdigest())).encode("utf-8")


class Api:
    def __init__(self, server: str, key, host_id: int | None = None):
        parsed = urlsplit(server)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}):
            raise ValueError("主控地址必须是 http(s)://主机:端口，不带路径或凭据")
        self.server, self.key, self.host_id = server.rstrip("/"), key, host_id
        self.session = requests.Session()
        # Do not send LAN identity/signatures through ambient proxies or .netrc auth.
        self.session.trust_env = False

    def request(self, method: str, path: str, value=None, *, raw: bytes | None = None,
                signed: bool = True) -> dict:
        if not path.startswith("/api/agent/") or "?" in path or "#" in path:
            raise ValueError("无效的机器接口路径")
        body = raw if raw is not None else (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if value is not None else b"")
        headers = {"Content-Type": "application/octet-stream" if raw is not None else "application/json"}
        if signed:
            if self.host_id is None:
                raise ValueError("尚未注册机器身份")
            timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
            headers.update({"X-SpiderFly-Host": str(self.host_id), "X-SpiderFly-Time": timestamp,
                            "X-SpiderFly-Nonce": nonce, "X-SpiderFly-Signature": base64.b64encode(
                                self.key.sign(canonical(timestamp, nonce, method, unquote(path), body))).decode("ascii")})
        # Retry is owned by the main loop; every invocation creates a fresh nonce/signature.
        response = self.session.request(method, self.server + path, data=body, headers=headers,
                                        timeout=(5, 10), allow_redirects=False)
        if not 200 <= response.status_code < 300:
            try:
                detail = str(response.json().get("detail", "请求失败"))
            except (ValueError, AttributeError):
                detail = "请求失败"
            raise ApiError(response.status_code, detail)
        return response.json()
