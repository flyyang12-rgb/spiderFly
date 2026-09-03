"""Small platform entry for Python flows; importing it does not load platform configuration."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .core import InstructionError


@dataclass(frozen=True)
class TaskContext:
    input_file: Path | None
    output_dir: Path


@dataclass(frozen=True)
class TaskResult:
    message: str
    code: str = "TASK_DONE"

    def __post_init__(self) -> None:
        if not isinstance(self.message, str) or len(self.message) > 1000:
            raise ValueError("任务说明必须是最多 1000 字的文字。")
        if not isinstance(self.code, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", self.code):
            raise ValueError("任务结果编码须为最多 64 位的大写字母、数字、点、横线或下划线。")


def _required_path(name: str) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        raise RuntimeError(f"缺少运行变量 {name}，请从平台运行此入口。")
    path = Path(raw)
    if not path.is_absolute():
        raise RuntimeError(f"运行变量 {name} 必须是绝对路径。")
    return path.resolve()


def _copy_input(source: Path, directory: Path) -> Path:
    destination = directory / ("输入" + source.suffix)
    temporary = directory / ".input.part"
    try:
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        # directory belongs exclusively to this invocation.
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _write_receipt(path: Path, outcome: str, result: TaskResult) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".task-result-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump({
                "schema_version": 1, "outcome": outcome,
                "code": result.code, "message": result.message, "retryable": False,
            }, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _log(message: str, *, error: bool = False) -> None:
    try:
        print(message, file=sys.stderr if error else sys.stdout, flush=True)
    except OSError:
        # A logging failure must not rewrite an already saved business receipt.
        pass


def run_task(process: Callable[[TaskContext], TaskResult], *, require_input: bool = True) -> int:
    """Run one synchronous flow once, preserve input, and save the platform receipt.

    The callback writes deliverables into context.output_dir and returns TaskResult.
    No retries, scheduling, environment setup, or automatic output validation happen here.
    """
    receipt: Path | None = None
    may_write_receipt = False
    try:
        if not callable(process) or type(require_input) is not bool:
            raise TypeError("process 必须是函数，require_input 必须是布尔值。")
        receipt = _required_path("SPIDERFLY_RESULT_FILE")
        artifacts = _required_path("SPIDERFLY_ARTIFACT_DIR")
        if not receipt.parent.is_dir() or not artifacts.is_dir():
            raise RuntimeError("本次运行资料目录不存在，请从平台运行此入口。")
        if os.path.lexists(receipt):
            raise RuntimeError("本次运行已有回执，请新建一条执行记录，不要重复运行同一目录。")
        may_write_receipt = True
        directory = artifacts / "流程文件"
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise RuntimeError("本次流程目录已存在，请从平台重新运行，使用新的执行记录。") from exc

        input_file = None
        has_input = bool(os.environ.get("SPIDERFLY_TEMPLATE_FILE", "").strip())
        if require_input and not has_input:
            raise RuntimeError("未收到上传文件，请在创建任务时上传 Excel 模板。")
        if has_input:
            uploaded = _required_path("SPIDERFLY_TEMPLATE_FILE")
            if not uploaded.is_file():
                raise RuntimeError("上传文件不存在或不是普通文件。")
            input_file = _copy_input(uploaded, directory)
        output_dir = directory / "输出"
        output_dir.mkdir()
        result = process(TaskContext(input_file=input_file, output_dir=output_dir))
        if not isinstance(result, TaskResult):
            raise TypeError("业务函数必须返回 TaskResult，才能确认本次处理结果。")
        # Revalidate in case a callback bypassed the frozen dataclass constructor.
        result = TaskResult(message=result.message, code=result.code)
        _write_receipt(receipt, "success", result)
    except (Exception, SystemExit, KeyboardInterrupt) as exc:
        code = "TASK_FAILED"
        if isinstance(exc, InstructionError):
            code = exc.code if re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{0,63}", exc.code) else code
            message = f"{exc.instruction_id}（{exc.stage}）：{exc}"
        elif isinstance(exc, (SystemExit, KeyboardInterrupt)):
            code, message = "TASK_INTERRUPTED", "业务流程提前结束，未确认成功。"
        else:
            message = str(exc) or type(exc).__name__
        failure = TaskResult(message=message[:1000], code=code)
        if may_write_receipt and receipt is not None:
            try:
                _write_receipt(receipt, "failure", failure)
            except OSError as receipt_error:
                _log(f"失败回执未能保存：{receipt_error}", error=True)
        _log(f"任务失败：{failure.message}", error=True)
        return 1
    _log(result.message)
    return 0

