from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests


TOKEN_ERROR_CODES = {99991661, 99991663, 99991664}


def format_duration(duration_ms: int | None) -> str:
    seconds = max(0, int((duration_ms or 0) / 1000))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{seconds}秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分"


@dataclass(frozen=True)
class FeishuSettings:
    app_id: str
    app_secret: str
    receiver_id: str
    receiver_id_type: str = "open_id"
    base_url: str = "https://open.feishu.cn"
    webhook_url: str = ""
    webhook_secret: str = ""

    @classmethod
    def from_env(cls) -> "FeishuSettings":
        receiver_type = os.getenv("FEISHU_RECEIVER_ID_TYPE", "open_id").strip()
        if receiver_type not in {"open_id", "mobile"}:
            receiver_type = "open_id"
        return cls(
            app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            receiver_id=os.getenv("FEISHU_RECEIVER_ID", "").strip(),
            receiver_id_type=receiver_type,
            base_url=os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/"),
            webhook_url=os.getenv("FEISHU_WEBHOOK_URL", "").strip(),
            webhook_secret=os.getenv("FEISHU_WEBHOOK_SECRET", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.webhook_url or (self.app_id and self.app_secret and self.receiver_id))


class FeishuError(RuntimeError):
    pass


class FeishuNotifier:
    def __init__(self, settings: FeishuSettings | None = None):
        self.settings = settings or FeishuSettings.from_env()
        self._session = requests.Session()
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def _parse(self, response: requests.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise FeishuError(f"飞书返回了非 JSON 响应（HTTP {response.status_code}）") from exc
        if response.status_code >= 400 or payload.get("code", 0) != 0:
            raise FeishuError(f"飞书接口调用失败（code={payload.get('code', response.status_code)}）")
        return payload

    def _tenant_token(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            now = time.monotonic()
            if not force_refresh and self._token and now < self._token_expires_at:
                return self._token
            response = self._session.post(
                f"{self.settings.base_url}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.settings.app_id, "app_secret": self.settings.app_secret},
                timeout=15,
            )
            payload = self._parse(response)
            token = payload.get("tenant_access_token")
            if not token:
                raise FeishuError("飞书响应中没有 tenant_access_token")
            self._token = token
            self._token_expires_at = now + max(60, int(payload.get("expire", 7200)) - 60)
            return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = self._tenant_token(force_refresh=attempt == 1)
            response = self._session.request(
                method,
                f"{self.settings.base_url}{path}",
                params=params,
                json=json_body,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {token}"},
                timeout=20,
            )
            try:
                return self._parse(response)
            except FeishuError:
                try:
                    code = response.json().get("code")
                except ValueError:
                    code = None
                if attempt == 0 and code in TOKEN_ERROR_CODES:
                    continue
                raise
        raise FeishuError("飞书接口重试失败")

    def _resolve_open_id(self) -> str:
        if self.settings.receiver_id_type == "open_id":
            return self.settings.receiver_id
        payload = self._request(
            "POST",
            "/open-apis/contact/v3/users/batch_get_id",
            params={"user_id_type": "open_id"},
            json_body={"mobiles": [self.settings.receiver_id], "include_resigned": False},
        )
        users = payload.get("data", {}).get("user_list", [])
        if len(users) != 1 or not (users[0].get("user_id") or users[0].get("open_id")):
            raise FeishuError("没有找到飞书收件人")
        return users[0].get("user_id") or users[0].get("open_id")

    def _upload_image(self, image_bytes: bytes) -> str:
        payload = self._request(
            "POST",
            "/open-apis/im/v1/images",
            data={"image_type": "message"},
            files={"image": ("spiderfly-error.jpg", io.BytesIO(image_bytes), "image/jpeg")},
        )
        image_key = payload.get("data", {}).get("image_key")
        if not image_key:
            raise FeishuError("飞书图片上传结果缺少 image_key")
        return image_key

    def _send_text(self, open_id: str, text: str) -> None:
        if self.settings.webhook_url:
            text = "SpiderFly\n" + text
        self._send_message(open_id, "text", {"text": text})

    def _send_message(self, open_id: str, msg_type: str, content: dict[str, Any]) -> None:
        if self.settings.webhook_url:
            body: dict[str, Any] = {
                "msg_type": msg_type,
                "content": {"post": content} if msg_type == "post" else content,
            }
            if self.settings.webhook_secret:
                timestamp = str(int(time.time()))
                signing_key = f"{timestamp}\n{self.settings.webhook_secret}".encode("utf-8")
                body["timestamp"] = timestamp
                body["sign"] = base64.b64encode(
                    hmac.new(signing_key, b"", hashlib.sha256).digest()
                ).decode("ascii")
            # Do not retry or fall back to a personal recipient: a timeout may
            # happen after delivery, and the selected audience must not change.
            try:
                response = self._session.post(
                    self.settings.webhook_url,
                    json=body,
                    timeout=20,
                    allow_redirects=False,
                )
            except requests.RequestException:
                # requests errors may contain the secret webhook URL.
                raise FeishuError("飞书群机器人网络请求失败，请检查网络后查看群内是否已送达") from None
            try:
                payload = response.json()
            except ValueError:
                raise FeishuError(f"飞书群机器人返回了非 JSON 响应（HTTP {response.status_code}）") from None
            code = payload.get("code", payload.get("StatusCode")) if isinstance(payload, dict) else None
            if not 200 <= response.status_code < 300 or type(code) is not int or code != 0:
                safe_code = code if type(code) is int else "unknown"
                if safe_code == 19021:
                    raise FeishuError("飞书群机器人签名校验失败（code=19021），请检查 FEISHU_WEBHOOK_SECRET 和本机时间")
                raise FeishuError(f"飞书群机器人发送失败（HTTP {response.status_code}，code={safe_code}）")
            return
        self._request(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            json_body={
                "receive_id": open_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )

    def _send_failure_post(
        self,
        open_id: str,
        task_name: str,
        duration_ms: int,
        error_summary: str,
        image_bytes: bytes | None,
        result_code: str = "",
        manual_action_url: str = "",
        manual_code: str = "",
        screenshot_note: str = "",
    ) -> None:
        error_excerpt = error_summary if len(error_summary) <= 900 else "…" + error_summary[-899:]
        content_rows: list[list[dict[str, str]]] = [
            [{"tag": "text", "text": f"❌「{task_name}」运行失败｜耗时 {format_duration(duration_ms)}"}],
            [{"tag": "text", "text": f"错误：{error_excerpt}"}],
        ]
        if result_code:
            content_rows.append(
                [{"tag": "text", "text": f"结果编码：{result_code[:64]}"}]
            )
        if manual_code:
            content_rows.append(
                [{"tag": "text", "text": f"人工处理编码：{manual_code[:200]}"}]
            )
        if manual_action_url:
            content_rows.append(
                [
                    {"tag": "text", "text": "人工处理："},
                    {"tag": "a", "href": manual_action_url[:2000], "text": "打开处理页面"},
                ]
            )
        if image_bytes:
            if not (self.settings.app_id and self.settings.app_secret):
                screenshot_note = "；".join(filter(None, [screenshot_note, "飞书附图需要配置应用 App ID、App Secret 和图片上传权限，请查看本次文件。"]))
            else:
                try:
                    image_key = self._upload_image(image_bytes)
                    content_rows.append([{"tag": "img", "image_key": image_key}])
                except Exception:
                    # Upload happens before sending the one final message. Do not retry
                    # a message after an ambiguous send failure or change its recipient.
                    screenshot_note = "；".join(filter(None, [screenshot_note, "截图上传失败，请查看运行记录的本次文件。"]))
        if screenshot_note:
            content_rows.append([{"tag": "text", "text": screenshot_note}])
        content = {"zh_cn": {"title": "SpiderFly 任务通知", "content": content_rows}}
        self._send_message(open_id, "post", content)

    def send_maintenance_result(self, task_name: str, note: str) -> None:
        if not self.settings.webhook_url:
            raise FeishuError("未配置群通知")
        self._send_text("", f"「{task_name}」维护结果：{note[:1800]}")

    def send_final_result(
        self,
        *,
        task_name: str,
        status: str,
        duration_ms: int,
        error_summary: str = "",
        result_code: str = "",
        manual_action_url: str = "",
        manual_code: str = "",
        image_bytes: bytes | None = None,
        screenshot_note: str = "",
    ) -> None:
        if not self.configured:
            raise FeishuError("未配置飞书群 Webhook 或应用收件人")
        open_id = "" if self.settings.webhook_url else self._resolve_open_id()
        if status == "success":
            self._send_text(open_id, f"✅「{task_name}」运行成功｜耗时 {format_duration(duration_ms)}")
            return
        self._send_failure_post(
            open_id,
            task_name,
            duration_ms,
            error_summary or "程序异常结束",
            image_bytes,
            result_code,
            manual_action_url,
            manual_code,
            screenshot_note,
        )


def capture_active_window_jpeg() -> bytes | None:
    """Capture the foreground window for a failure notification.

    The notification still falls back to text when capture is unavailable.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        import ctypes.wintypes
        from PIL import ImageGrab

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        bbox = (rect.left, rect.top, rect.right, rect.bottom)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return None
        image = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB")
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=86, optimize=True)
        value = output.getvalue()
        return value if 0 < len(value) <= 10 * 1024 * 1024 else None
    except Exception:
        return None
