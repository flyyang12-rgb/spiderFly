"""上传 .xlsx，筛出状态为“待处理”的行，保存输入与结果并提交平台业务回执。

创建任务时上传本文件与 Excel 模板，依赖填写 spiderfly-instructions==0.1.1。
只读取第一张工作表；修改下面两个常量即可调整单列相等条件。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


FILTER_COLUMN = "状态"
FILTER_VALUE = "待处理"


def run_filter(input_path: Path, output_path: Path) -> dict:
    try:
        from spiderfly_instructions import InstructionRegistry
        from spiderfly_instructions.excel import READ_EXCEL
        from spiderfly_instructions.excel_write import WRITE_EXCEL
        from spiderfly_instructions.table_filter import FILTER_EQUALS
    except ImportError as exc:
        raise RuntimeError(
            "任务环境缺少筛选指令，请用 spiderfly-instructions==0.1.1 创建任务并完成依赖安装。"
        ) from exc

    registry = InstructionRegistry()
    for instruction in (READ_EXCEL, FILTER_EQUALS, WRITE_EXCEL):
        registry.register(instruction)

    table = registry.execute("excel.read", {
        "file_path": str(input_path),
        "required_columns": [FILTER_COLUMN],
    })
    filtered = registry.execute("table.filter_equals", {
        "columns": table.columns,
        "rows": table.rows,
        "column": FILTER_COLUMN,
        "value": FILTER_VALUE,
    })
    # 无匹配行也写出原表头；写入指令会拒绝覆盖已有文件。
    result = registry.execute("excel.write", {
        "file_path": str(output_path),
        "sheet_name": table.sheet_name,
        "columns": filtered.columns,
        "rows": filtered.rows,
    })
    return {**result.model_dump(), "input_row_count": table.row_count}


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
        # 本文件须能单独上传，所以保留少量文件交接代码，不依赖相邻示例。
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
            raise RuntimeError("订单筛选只接受 .xlsx 文件，请在“Excel 模板”中上传 .xlsx 数据表。")
        if not uploaded_file.is_file():
            raise RuntimeError("上传的 Excel 文件不存在或不是普通文件，请检查任务中的 Excel 模板。")

        directory = artifact_dir / "订单筛选示例"
        try:
            directory.mkdir()
        except FileExistsError as exc:
            raise RuntimeError("本次订单筛选目录已存在，请从平台重新运行，使用新的执行记录和新目录。") from exc
        input_file = _copy_input(uploaded_file, directory)
        result = run_filter(input_file, directory / "结果.xlsx")
        message = (
            f"读取 {result['input_row_count']} 行，保留 {result['row_count']} 行，"
            f"结果文件：{result['file_path']}"
        )
        _write_result(result_file, "success", "EXCEL_FILTER_DONE", message)
        print(message, flush=True)
        return 0
    except Exception as exc:
        message = f"订单筛选示例失败：{exc}"[:1000]
        print(message, file=sys.stderr, flush=True)
        if result_file is not None:
            try:
                _write_result(result_file, "failure", "EXCEL_FILTER_FAILED", message)
            except OSError as result_error:
                print(f"失败回执未能保存：{result_error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
