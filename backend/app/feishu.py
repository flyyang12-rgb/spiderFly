from __future__ import annotations

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
        )

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret and self.receiver_id)


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
        self._request(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            json_body={
                "receive_id": open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
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
    ) -> None:
        content_rows: list[list[dict[str, str]]] = [
            [{"tag": "text", "text": f"❌「{task_name}」运行失败｜耗时 {format_duration(duration_ms)}"}],
            [{"tag": "text", "text": f"错误：{error_summary[:900]}"}],
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
            image_key = self._upload_image(image_bytes)
            content_rows.append([{"tag": "img", "image_key": image_key}])
        content = {"zh_cn": {"title": "SpiderFly 任务通知", "content": content_rows}}
        self._request(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            json_body={
                "receive_id": open_id,
                "msg_type": "post",
                "content": json.dumps(content, ensure_ascii=False),
            },
        )

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
    ) -> None:
        if not self.configured:
            raise FeishuError("未配置飞书应用或收件人")
        open_id = self._resolve_open_id()
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
