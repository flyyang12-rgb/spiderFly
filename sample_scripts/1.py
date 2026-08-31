"""用 DrissionPage 启动独立 Chrome，并监听百度首页数据包。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
TARGET_URL = "https://www.baidu.com/"
LISTEN_TARGET = "baidu.com"


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RuntimeError(f"缺少 SpiderFly 运行变量：{variable}")
    path = Path(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _optional_directory(variable: str) -> Path | None:
    value = os.environ.get(variable, "").strip()
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_result(
    outcome: str,
    code: str,
    message: str,
    *,
    retryable: bool,
) -> None:
    result_file = _required_path("SPIDERFLY_RESULT_FILE")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "outcome": outcome,
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    temporary = result_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(result_file)


def _body_preview(body: object, limit: int = 600) -> str:
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    elif isinstance(body, (dict, list)):
        text = json.dumps(body, ensure_ascii=False)
    else:
        text = str(body)
    return " ".join(text.split())[:limit]


def _bypass_proxy_for_local_browser() -> None:
    """确保 DrissionPage 的本机调试连接不被系统代理转发。"""
    required = ("127.0.0.1", "localhost")
    for variable in ("NO_PROXY", "no_proxy"):
        entries = [
            item.strip()
            for item in os.environ.get(variable, "").split(",")
            if item.strip()
        ]
        lowered = {item.casefold() for item in entries}
        entries.extend(item for item in required if item.casefold() not in lowered)
        os.environ[variable] = ",".join(entries)


def main() -> None:
    _bypass_proxy_for_local_browser()
    from DrissionPage import ChromiumOptions, ChromiumPage

    if not CHROME_PATH.is_file():
        raise FileNotFoundError(f"没有找到 Google Chrome：{CHROME_PATH}")

    download_dir = _optional_directory("SPIDERFLY_DOWNLOAD_DIR")
    screenshot_dir = _optional_directory("SPIDERFLY_SCREENSHOT_DIR")
    profile_dir = _optional_directory("SPIDERFLY_BROWSER_PROFILE_DIR")
    if profile_dir is None:
        raise RuntimeError("缺少 SpiderFly 运行变量：SPIDERFLY_BROWSER_PROFILE_DIR")
    try:
        browser_port = int(os.environ.get("SPIDERFLY_BROWSER_PORT", "9123"))
    except ValueError as exc:
        raise RuntimeError("SPIDERFLY_BROWSER_PORT 必须是有效端口号") from exc
    page: ChromiumPage | None = None
    owned_browser = False

    try:
        options = (
            ChromiumOptions(read_file=False)
            .set_browser_path(CHROME_PATH)
            .set_local_port(browser_port)
            .set_user_data_path(profile_dir)
        )
        if download_dir is not None:
            options.set_download_path(download_dir)
        options.set_timeouts(base=10, page_load=30, script=30)

        page = ChromiumPage(options)
        owned_browser = not page.browser.states.is_existed
        if not owned_browser:
            raise RuntimeError(f"专用浏览器端口 {browser_port} 已被其他浏览器占用")

        # DrissionPage 只能捕获启动监听之后产生的数据包。
        page.listen.start(
            targets=LISTEN_TARGET,
            method="GET",
            res_type="Document",
        )
        page.get(TARGET_URL)

        packet = page.listen.wait(timeout=20)
        if packet is False:
            raise TimeoutError("20 秒内没有监听到百度数据包")
        if packet.is_failed:
            failure = getattr(packet.fail_info, "errorText", "未知网络错误")
            raise RuntimeError(f"百度请求失败：{failure}")

        status = packet.response.status
        if not isinstance(status, int) or not 200 <= status < 400:
            raise RuntimeError(f"百度返回了异常 HTTP 状态：{status}")

        preview = _body_preview(packet.response.body)
        print("===== 百度数据包监听成功 =====", flush=True)
        print(f"请求地址：{packet.url}", flush=True)
        print(f"请求方式：{packet.method}", flush=True)
        print(f"资源类型：{packet.resourceType}", flush=True)
        print(f"响应状态：{status}", flush=True)
        print(f"响应内容摘要：{preview}", flush=True)
        print("===== LISTEN_SUCCESS =====", flush=True)
        _write_result(
            "success",
            "BAIDU_PACKET_CAPTURED",
            f"已监听并打印百度数据包，HTTP {status}",
            retryable=False,
        )
    except Exception as exc:
        if page is not None and screenshot_dir is not None:
            try:
                page.get_screenshot(
                    path=screenshot_dir,
                    name=f"baidu-listen-failure-{os.environ.get('SPIDERFLY_EXECUTION_ID', 'unknown')}.png",
                    full_page=True,
                )
            except Exception:
                pass
        try:
            _write_result(
                "failure",
                "BAIDU_PACKET_CAPTURE_FAILED",
                str(exc)[:800] or "百度数据包监听失败",
                retryable=False,
            )
        except Exception:
            pass
        raise
    finally:
        if page is not None:
            already_failing = sys.exc_info()[0] is not None
            try:
                if page.listen.listening:
                    page.listen.stop()
                if owned_browser:
                    page.quit(timeout=5, force=True, del_data=True)
            except Exception:
                if not already_failing:
                    raise


if __name__ == "__main__":
    main()
