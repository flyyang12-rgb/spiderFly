"""SpiderFly + DrissionPage 自管浏览器模板。

上传前请替换 TARGET_URL 和“业务操作”区域，并在应用依赖中加入 DrissionPage。
该模式每次启动独立浏览器，适合不需要复用人工登录态的网页流程。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from DrissionPage import Chromium, ChromiumOptions


TARGET_URL = "https://example.com/"


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        raise RuntimeError(f"缺少 SpiderFly 运行变量：{variable}")
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True) if path.suffix == "" else path.parent.mkdir(
        parents=True, exist_ok=True
    )
    return path


def write_result(
    outcome: str,
    code: str,
    message: str,
    *,
    retryable: bool | None = None,
    manual_action_url: str = "",
    manual_code: str = "",
) -> None:
    result_file = _required_path("SPIDERFLY_RESULT_FILE")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "outcome": outcome,
        "code": code,
        "message": message,
    }
    if retryable is not None:
        payload["retryable"] = retryable
    if manual_action_url:
        payload["manual_action_url"] = manual_action_url
    if manual_code:
        payload["manual_code"] = manual_code
    temporary = result_file.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(result_file)


def main() -> None:
    download_dir = _required_path("SPIDERFLY_DOWNLOAD_DIR")
    screenshot_dir = _required_path("SPIDERFLY_SCREENSHOT_DIR")
    profile_dir = _required_path("SPIDERFLY_BROWSER_PROFILE_DIR")
    try:
        browser_port = int(os.environ.get("SPIDERFLY_BROWSER_PORT", "9123"))
    except ValueError as exc:
        raise RuntimeError("SPIDERFLY_BROWSER_PORT 必须是有效端口号") from exc
    browser: Chromium | None = None
    owned_browser = False
    try:
        options = (
            ChromiumOptions(read_file=False)
            .set_local_port(browser_port)
            .set_user_data_path(profile_dir)
        )
        options.set_download_path(download_dir)
        browser = Chromium(options)
        owned_browser = not browser.states.is_existed
        if not owned_browser:
            raise RuntimeError(f"专用浏览器端口 {browser_port} 已被其他浏览器占用")
        tab = browser.latest_tab
        tab.get(TARGET_URL)

        # TODO：在这里编写网页定位、点击、下载和业务判断。
        # 登录失效等需要人工介入时，可写入以下结果后 return：
        # write_result(
        #     "manual_required",
        #     "LOGIN_EXPIRED",
        #     "登录状态失效，需要人工重新登录",
        #     manual_action_url="https://example.com/login",
        #     manual_code="ACCOUNT_RELOGIN",
        # )
        # return

        write_result("success", "OK", "网页流程执行完成", retryable=False)
    except Exception as exc:
        if browser is not None and owned_browser:
            try:
                browser.latest_tab.get_screenshot(
                    path=screenshot_dir,
                    name=f"failure-{os.environ.get('SPIDERFLY_EXECUTION_ID', 'unknown')}.png",
                    full_page=True,
                )
            except Exception:
                pass
        write_result(
            "failure",
            "BROWSER_AUTOMATION_ERROR",
            str(exc)[:800] or "网页自动化执行失败",
            retryable=False,
        )
        raise
    finally:
        if browser is not None and owned_browser:
            already_failing = sys.exc_info()[0] is not None
            try:
                browser.quit(timeout=5, force=True, del_data=True)
            except Exception as cleanup_error:
                if not already_failing:
                    write_result(
                        "failure",
                        "BROWSER_CLEANUP_ERROR",
                        f"浏览器流程完成，但清理失败：{cleanup_error}"[:800],
                        retryable=False,
                    )
                    raise


if __name__ == "__main__":
    main()
