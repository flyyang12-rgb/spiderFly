"""Example: read surname/given-name columns, combine them, and save a new file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.demo import JOIN_NONEMPTY
from spiderfly_instructions.excel import READ_EXCEL
from spiderfly_instructions.excel_write import WRITE_EXCEL


def _new_registry() -> InstructionRegistry:
    registry = InstructionRegistry()
    for instruction in (READ_EXCEL, JOIN_NONEMPTY, WRITE_EXCEL):
        registry.register(instruction)
    return registry


def run_flow(input_path: str, output_path: str, sheet_name: str | None = None) -> dict:
    registry = _new_registry()

    # 1. Read the table with the existing Excel instruction.
    table = registry.execute("excel.read", {
        "file_path": input_path,
        "sheet_name": sheet_name,
        "required_columns": ["姓", "名"],
    })
    if "姓名" in table.columns:
        raise InstructionError(
            "FLOW_COLUMN_EXISTS", "example.excel_name", "transform",
            "输入表已有“姓名”列，请换一份输入，避免替换已有内容。",
        )

    # 2. Process every row before creating an output file.
    rows = []
    for record_number, row in enumerate(table.rows, start=1):
        parts = [row["姓"], row["名"]]
        if any(value is not None and not isinstance(value, str) for value in parts):
            raise InstructionError(
                "FLOW_NAME_INVALID", "example.excel_name", "transform",
                f"第 {record_number} 条有效记录的“姓”和“名”只能填写文字或留空。",
            )
        combined = registry.execute("text.join_nonempty", {
            "items": ["" if value is None else value for value in parts],
            "separator": "",
        })
        rows.append({**row, "姓名": combined.text})

    # 3. Save through the existing writer; it refuses to overwrite old files.
    result = registry.execute("excel.write", {
        "file_path": output_path,
        "sheet_name": table.sheet_name,
        "columns": [*table.columns, "姓名"],
        "rows": rows,
    })
    return result.model_dump()


def run_demo(directory: str) -> dict:
    """Prepare fictional input and run the same workflow in a new directory."""
    folder = Path(directory).absolute()
    try:
        folder.mkdir()
    except OSError as exc:
        raise InstructionError(
            "FLOW_DEMO_DIRECTORY_INVALID", "example.excel_name", "prepare",
            "演示目录必须是尚不存在的新目录；请检查父目录和访问权限。",
        ) from exc
    input_path = str(folder / "输入.xlsx")
    output_path = str(folder / "结果.xlsx")
    registry = _new_registry()
    registry.execute("excel.write", {
        "file_path": input_path,
        "sheet_name": "示例",
        "columns": ["编号", "姓", "名"],
        "rows": [
            {"编号": "001", "姓": "张", "名": "三"},
            {"编号": "002", "姓": " 李 ", "名": " 四 "},
            {"编号": "003", "姓": "王", "名": None},
        ],
    })
    return run_flow(input_path, output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="示例流程：读取 Excel，把姓和名合成姓名，另存新文件")
    parser.add_argument("input_path", nargs="?", help="输入 .xlsx 文件")
    parser.add_argument("output_path", nargs="?", help="输出的新 .xlsx 文件")
    parser.add_argument("--sheet", help="输入工作表名称，默认第一张")
    parser.add_argument("--demo", metavar="新目录", help="创建虚构样本并运行完整流程")
    args = parser.parse_args()
    if args.demo is not None:
        if args.input_path is not None or args.output_path is not None or args.sheet is not None:
            parser.error("--demo 单独使用，不同时填写输入、输出或工作表。")
    elif args.input_path is None or args.output_path is None:
        parser.error("请同时填写输入和输出文件，或使用 --demo 新目录。")
    try:
        if args.demo is not None:
            result = run_demo(args.demo)
        else:
            result = run_flow(args.input_path, args.output_path, args.sheet)
    except InstructionError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
