"""Signed, non-redirecting protocol v1 transport."""

import base64
import hashlib
import json
import os
from pathlib import Path
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

    def signed_headers(self, method: str, path: str, body: bytes = b'') -> dict:
        if not path.startswith('/api/agent/') or '?' in path or '#' in path or self.host_id is None:
            raise ValueError('无效的机器接口或身份')
        timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
        return {'X-SpiderFly-Host': str(self.host_id), 'X-SpiderFly-Time': timestamp,
                'X-SpiderFly-Nonce': nonce, 'X-SpiderFly-Signature': base64.b64encode(
                    self.key.sign(canonical(timestamp, nonce, method, unquote(path), body))).decode('ascii')}

    def download(self, path: str, destination: Path, *, size: int, sha256: str):
        if not 0 < size <= 16 * 1024 * 1024:
            raise ValueError('升级包大小无效')
        temporary = destination.with_suffix('.partial')
        try:
            with self.session.request('GET', self.server + path, headers=self.signed_headers('GET', path),
                                      timeout=(5, 15), allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise ApiError(response.status_code, '升级包下载失败')
                digest = hashlib.sha256();received = 0
                with temporary.open('wb') as stream:
                    for chunk in response.iter_content(64 * 1024):
                        received += len(chunk)
                        if received > size:
                            raise ValueError('升级包超过授权大小')
                        digest.update(chunk);stream.write(chunk)
                if received != size or digest.hexdigest() != sha256:
                    raise ValueError('升级包校验失败')
                os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

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
            headers.update(self.signed_headers(method, path, body))
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
