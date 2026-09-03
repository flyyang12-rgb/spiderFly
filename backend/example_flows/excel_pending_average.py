"""通过共享指令计算待处理行的金额平均数，保留原工作簿并追加结果列。"""

from __future__ import annotations

import argparse
import json
import sys

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.average import AVERAGE
from spiderfly_instructions.excel import READ_EXCEL
from spiderfly_instructions.excel_write import WRITE_EXCEL


RESULT_COLUMN = "待处理平均数"


def run_flow(input_path: str, output_path: str, sheet_name: str | None = None) -> dict:
    registry = InstructionRegistry()
    for instruction in (READ_EXCEL, AVERAGE, WRITE_EXCEL):
        registry.register(instruction)
    instruction_calls = []

    table = registry.execute("excel.read", {
        "file_path": input_path,
        "sheet_name": sheet_name,
        "required_columns": ["状态", "金额"],
    })
    instruction_calls.append("excel.read")
    if RESULT_COLUMN in table.columns:
        raise InstructionError(
            "FLOW_COLUMN_EXISTS", "example.excel_pending_average", "transform",
            "输入表已有“待处理平均数”列，请换一份输入，避免替换已有内容。",
        )

    rows = []
    processed_row_count = 0
    for row in table.rows:
        average = None
        if row["状态"] == "待处理":
            result = registry.execute("math.average", {"value": row["金额"]})
            instruction_calls.append("math.average")
            average = result.average
            processed_row_count += 1
        rows.append({**row, RESULT_COLUMN: average})

    # 所有待处理行通过检查后才写文件；模板模式保留原表并追加新列。
    saved = registry.execute("excel.write", {
        "file_path": output_path,
        "template_file": input_path,
        "sheet_name": table.sheet_name,
        "columns": [*table.columns, RESULT_COLUMN],
        "rows": rows,
    })
    instruction_calls.append("excel.write")
    return {
        **saved.model_dump(),
        "processed_row_count": processed_row_count,
        "instruction_calls": instruction_calls,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="读取 Excel，为待处理行计算金额平均数并另存新文件")
    parser.add_argument("input_path", help="输入 .xlsx 文件")
    parser.add_argument("output_path", help="输出的新 .xlsx 文件")
    parser.add_argument("--sheet", help="输入工作表名称，默认第一张")
    args = parser.parse_args()
    try:
        result = run_flow(args.input_path, args.output_path, args.sheet)
    except InstructionError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
