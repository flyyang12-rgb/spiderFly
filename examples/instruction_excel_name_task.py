"""读取上传的 Excel 并合并姓名，需要任务环境已安装 spiderfly-instructions。

创建任务时在“Excel 模板”上传 .xlsx，第一张工作表须含“姓”和“名”列。
保留本次输入副本，复用指令包处理并另存结果；平台根据 result.json 判断业务结果。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        if variable == "SPIDERFLY_TEMPLATE_FILE":
            raise RuntimeError("未收到上传的 Excel，请在创建任务时通过“Excel 模板”上传需要处理的 .xlsx 文件。")
        raise RuntimeError(f"缺少 SpiderFly 运行变量：{variable}，请从平台运行此示例。")
    path = Path(value)
    if not path.is_absolute():
        raise RuntimeError(f"SpiderFly 运行变量 {variable} 必须是绝对路径。")
    return path.resolve()


def _copy_input(source: Path, directory: Path) -> Path:
    destination = directory / "输入.xlsx"
    temporary = directory / ".input.xlsx.tmp"
    try:
        # 只读取平台传来的副本；完整复制后才作为本次流程的输入。
        with source.open("rb") as incoming, temporary.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _write_result(path: Path, outcome: str, code: str, message: str) -> None:
    payload = {
        "schema_version": 1,
        "outcome": outcome,
        "code": code,
        "message": message[:1000],
        "retryable": False,
    }
    temporary: Path | None = None
    try:
        # 临时文件与回执位于同一目录，写完后一次替换，避免读到半份 JSON。
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".result-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    result_file: Path | None = None
    try:
        result_file = _required_path("SPIDERFLY_RESULT_FILE")
        artifact_dir = _required_path("SPIDERFLY_ARTIFACT_DIR")
        if not result_file.parent.is_dir() or not artifact_dir.is_dir():
            raise RuntimeError("本次执行的资料目录不存在，请从 SpiderFly 平台运行此示例。")
        uploaded_file = _required_path("SPIDERFLY_TEMPLATE_FILE")
        if uploaded_file.suffix.lower() != ".xlsx":
            raise RuntimeError("姓名合并只接受 .xlsx 文件，请在“Excel 模板”中上传 .xlsx 数据表。")
        if not uploaded_file.is_file():
            raise RuntimeError("上传的 Excel 文件不存在或不是普通文件，请检查任务中的 Excel 模板。")
        try:
            from example_flows.excel_name import run_flow
        except ImportError as exc:
            raise RuntimeError("任务环境未安装完整的 spiderfly-instructions 指令包，请先安装其依赖。") from exc

        directory = artifact_dir / "姓名合并示例"
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise RuntimeError("本次姓名合并目录已存在，请从平台重新运行，使用新的执行记录和新目录。") from exc
        input_file = _copy_input(uploaded_file, directory)
        result = run_flow(str(input_file), str(directory / "结果.xlsx"))
        message = f"已合并 {result['row_count']} 行姓名，结果文件：{result['file_path']}"
        print(message, flush=True)
        # 只有共享流程完整完成之后，才向平台提交成功回执。
        _write_result(result_file, "success", "EXCEL_NAME_DONE", message)
        return 0
    except Exception as exc:
        message = f"姓名合并示例失败：{exc}"[:1000]
        print(message, file=sys.stderr, flush=True)
        if result_file is not None:
            try:
                _write_result(result_file, "failure", "EXCEL_NAME_FAILED", message)
            except OSError as result_error:
                print(f"失败回执未能保存：{result_error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
