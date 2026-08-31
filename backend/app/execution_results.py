from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .config import EXECUTIONS_DIR


MAX_RESULT_BYTES = 64 * 1024
MAX_RESULT_MESSAGE_CHARS = 1000
MAX_MANUAL_CODE_CHARS = 200
MAX_MANUAL_URL_CHARS = 2000
RESULT_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
RESULT_OUTCOMES = {"success", "failure", "manual_required"}


class ResultProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutionWorkspace:
    root: Path
    result_file: Path
    artifacts_dir: Path
    downloads_dir: Path
    screenshots_dir: Path
    temporary_dir: Path

    def environment(self, execution_id: int) -> dict[str, str]:
        return {
            "SPIDERFLY_EXECUTION_ID": str(execution_id),
            "SPIDERFLY_EXECUTION_DIR": str(self.root),
            "SPIDERFLY_RESULT_FILE": str(self.result_file),
            "SPIDERFLY_ARTIFACT_DIR": str(self.artifacts_dir),
            "SPIDERFLY_DOWNLOAD_DIR": str(self.downloads_dir),
            "SPIDERFLY_SCREENSHOT_DIR": str(self.screenshots_dir),
            "SPIDERFLY_TMP_DIR": str(self.temporary_dir),
        }


@dataclass(frozen=True)
class StructuredResult:
    outcome: str
    code: str
    message: str = ""
    retryable: bool | None = None
    manual_action_url: str = ""
    manual_code: str = ""


@dataclass(frozen=True)
class ResolvedOutcome:
    status: str
    error_message: str
    result_source: str = "legacy"
    business_outcome: str = ""
    result_code: str = ""
    result_message: str = ""
    retryable: bool | None = None
    manual_action_url: str = ""
    manual_code: str = ""


def create_execution_workspace(execution_id: int) -> ExecutionWorkspace:
    if execution_id < 1:
        raise ValueError("执行记录编号无效")
    root_dir = EXECUTIONS_DIR.resolve()
    root_dir.mkdir(parents=True, exist_ok=True)
    execution_dir = (root_dir / str(execution_id)).resolve()
    if execution_dir.parent != root_dir:
        raise RuntimeError("执行资料目录不在 SpiderFly 管理范围内")
    execution_dir.mkdir(exist_ok=False)
    artifacts_dir = execution_dir / "artifacts"
    downloads_dir = execution_dir / "downloads"
    screenshots_dir = execution_dir / "screenshots"
    temporary_dir = execution_dir / "tmp"
    for directory in (artifacts_dir, downloads_dir, screenshots_dir, temporary_dir):
        directory.mkdir()
    return ExecutionWorkspace(
        root=execution_dir,
        result_file=execution_dir / "result.json",
        artifacts_dir=artifacts_dir,
        downloads_dir=downloads_dir,
        screenshots_dir=screenshots_dir,
        temporary_dir=temporary_dir,
    )


def remove_execution_workspaces(execution_ids: list[int] | tuple[int, ...]) -> tuple[str, ...]:
    """Remove only the exact numeric execution directories managed by SpiderFly."""
    root = EXECUTIONS_DIR.expanduser().absolute()
    removed: list[str] = []
    failures: list[str] = []
    for raw_id in execution_ids:
        execution_id = int(raw_id)
        if execution_id < 1:
            raise ValueError("执行记录编号无效")
        candidate = root / str(execution_id)
        if not os.path.lexists(candidate):
            continue
        is_junction = getattr(candidate, "is_junction", lambda: False)
        try:
            if (
                candidate.parent != root
                or candidate.name != str(execution_id)
                or candidate.is_symlink()
                or bool(is_junction())
                or not candidate.is_dir()
            ):
                failures.append(str(candidate))
                continue
            resolved_root = root.resolve()
            resolved_candidate = candidate.resolve()
            if resolved_candidate.parent != resolved_root:
                failures.append(str(candidate))
                continue
            shutil.rmtree(resolved_candidate)
            removed.append(str(resolved_candidate))
        except OSError:
            failures.append(str(candidate))
    if failures:
        names = "、".join(Path(item).name or item for item in failures[:5])
        raise RuntimeError(f"这些执行资料未能安全删除：{names}")
    return tuple(removed)


def _optional_text(payload: dict, key: str, maximum: int) -> str:
    value = payload.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ResultProtocolError(f"result.json 的 {key} 必须是字符串")
    value = value.strip()
    if len(value) > maximum:
        raise ResultProtocolError(f"result.json 的 {key} 过长")
    return value


def _manual_url(payload: dict) -> str:
    value = _optional_text(payload, "manual_action_url", MAX_MANUAL_URL_CHARS)
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise ResultProtocolError("manual_action_url 不是有效的 HTTP/HTTPS 链接") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ResultProtocolError("manual_action_url 只允许不含账号密码的 HTTP/HTTPS 链接")
    return value


def load_structured_result(path: Path) -> StructuredResult | None:
    if not path.exists():
        return None
    is_junction = getattr(path, "is_junction", lambda: False)
    if not path.is_file() or path.is_symlink() or is_junction():
        raise ResultProtocolError("result.json 必须是普通文件")
    if path.stat().st_size > MAX_RESULT_BYTES:
        raise ResultProtocolError("result.json 不能超过 64KB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ResultProtocolError("result.json 必须使用 UTF-8 编码") from exc
    except json.JSONDecodeError as exc:
        raise ResultProtocolError(f"result.json 不是有效 JSON：第 {exc.lineno} 行") from exc
    except (ValueError, RecursionError) as exc:
        raise ResultProtocolError("result.json 的内容过于复杂或包含无效数值") from exc
    if not isinstance(payload, dict):
        raise ResultProtocolError("result.json 顶层必须是对象")
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ResultProtocolError("result.json 的 schema_version 当前必须为 1")
    outcome = payload.get("outcome")
    if not isinstance(outcome, str) or outcome not in RESULT_OUTCOMES:
        raise ResultProtocolError("result.json 的 outcome 不受支持")
    code = payload.get("code")
    if not isinstance(code, str) or not RESULT_CODE_PATTERN.fullmatch(code.strip()):
        raise ResultProtocolError("result.json 的 code 必须是稳定的大写英文编码")
    retryable = payload.get("retryable")
    if retryable is not None and not isinstance(retryable, bool):
        raise ResultProtocolError("result.json 的 retryable 必须是布尔值")
    manual_action_url = _manual_url(payload)
    manual_code = _optional_text(payload, "manual_code", MAX_MANUAL_CODE_CHARS)
    if outcome == "manual_required" and not (manual_action_url or manual_code):
        raise ResultProtocolError("人工介入结果必须提供 manual_action_url 或 manual_code")
    return StructuredResult(
        outcome=outcome,
        code=code.strip(),
        message=_optional_text(payload, "message", MAX_RESULT_MESSAGE_CHARS),
        retryable=retryable,
        manual_action_url=manual_action_url,
        manual_code=manual_code,
    )


def resolve_execution_outcome(
    *,
    process_status: str,
    exit_code: int | None,
    legacy_error: str,
    result_file: Path | None,
) -> ResolvedOutcome:
    try:
        structured = load_structured_result(result_file) if result_file else None
    except (OSError, ResultProtocolError) as exc:
        message = f"结构化结果无效：{exc}"
        status = "failed" if process_status == "success" else process_status
        error_message = legacy_error or message
        if process_status == "success":
            error_message = message
        return ResolvedOutcome(
            status=status,
            error_message=error_message,
            result_source="result_json",
            business_outcome="failure",
            result_code="RESULT_INVALID",
            result_message=message,
            retryable=False,
        )

    if structured is None:
        return ResolvedOutcome(status=process_status, error_message=legacy_error)

    status = process_status
    if process_status == "success" and exit_code == 0:
        status = "success" if structured.outcome == "success" else "failed"
    result_error = legacy_error
    if process_status == "success" and exit_code == 0 and structured.outcome != "success":
        result_error = structured.message or structured.code
    elif process_status == "failed" and not result_error:
        result_error = structured.message or structured.code
    return ResolvedOutcome(
        status=status,
        error_message=result_error,
        result_source="result_json",
        business_outcome=structured.outcome,
        result_code=structured.code,
        result_message=structured.message,
        retryable=structured.retryable,
        manual_action_url=structured.manual_action_url,
        manual_code=structured.manual_code,
    )
