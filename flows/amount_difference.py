"""用已有读取、筛选指令求两组金额之差；汇总和减法由普通 Python 流程完成。

独立流程，只依赖 spiderfly-instructions 0.1.3 的通用指令和运行入口。
带参数时本地运行，不带参数时由平台运行；不修改输入 Excel。
英文逗号沿用样表约定：5,2 表示两个数 5 和 2。
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, DecimalException, Inexact, localcontext
from pathlib import Path

from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.excel import READ_EXCEL
from spiderfly_instructions.table_filter import FILTER_EQUALS
from spiderfly_instructions.task import TaskContext, TaskResult, run_task


FLOW_ID = "example.excel_amount_difference"
STATUS_COLUMN = "状态"
AMOUNT_COLUMN = "金额"
LEFT_STATUS = "待处理"
RIGHT_STATUS = "已完成"


def _group_total(rows: list[dict], column: str, status: str) -> Decimal:
    total = Decimal(0)
    for index, row in enumerate(rows, start=1):
        value = row[column]
        if type(value) not in (str, int, float):
            raise InstructionError(
                "FLOW_AMOUNT_INVALID", FLOW_ID, "calculate",
                f"“{status}”第 {index} 条匹配记录的金额必须是数字或英文逗号分隔的数字文字。",
            )
        parts = value.split(",") if isinstance(value, str) else [str(value)]
        for part in parts:
            try:
                number = Decimal(part.strip())
                if not number.is_finite():
                    raise ValueError("non-finite amount")
            except (DecimalException, ValueError) as exc:
                raise InstructionError(
                    "FLOW_AMOUNT_INVALID", FLOW_ID, "calculate",
                    f"“{status}”第 {index} 条匹配记录含空项或非法金额，请检查原表。",
                ) from exc
            total += number
    return total


def run_flow(
    input_path: str, sheet_name: str | None = None, *,
    status_column: str = STATUS_COLUMN, amount_column: str = AMOUNT_COLUMN,
    left_status: str = LEFT_STATUS, right_status: str = RIGHT_STATUS,
) -> dict:
    registry = InstructionRegistry()
    registry.register(READ_EXCEL)
    registry.register(FILTER_EQUALS)
    calls = []

    table = registry.execute("excel.read", {
        "file_path": input_path, "sheet_name": sheet_name,
        "required_columns": list(dict.fromkeys([status_column, amount_column])),
    })
    calls.append("excel.read")

    groups = []
    for status in (left_status, right_status):
        group = registry.execute("table.filter_equals", {
            "columns": table.columns, "rows": table.rows,
            "column": status_column, "value": status,
        })
        groups.append(group)
        calls.append("table.filter_equals")

    # Decimal avoids binary float tails such as 0.29999999999999893.
    # If the 28-digit arithmetic would lose significant digits, fail explicitly.
    try:
        with localcontext() as context:
            context.prec = 28
            context.traps[Inexact] = True
            left_total = _group_total(groups[0].rows, amount_column, left_status)
            right_total = _group_total(groups[1].rows, amount_column, right_status)
            difference = left_total - right_total
    except DecimalException as exc:
        raise InstructionError(
            "FLOW_AMOUNT_RANGE", FLOW_ID, "calculate",
            "金额汇总超出当前十进制计算精度或范围，未返回近似结果。",
        ) from exc

    return {
        "file_path": str(Path(input_path).absolute()),
        "sheet_name": table.sheet_name,
        "status_column": status_column, "amount_column": amount_column,
        "left_status": left_status, "right_status": right_status,
        "left_row_count": groups[0].row_count,
        "right_row_count": groups[1].row_count,
        # Strings retain the exact decimal result when writing JSON.
        "left_total": str(left_total), "right_total": str(right_total),
        "difference": str(difference),
        "instruction_calls": calls,
        "calculation": "Python Decimal: sum(left) - sum(right)",
    }


def process(context: TaskContext) -> TaskResult:
    result = run_flow(str(context.input_file))
    message = (
        f"{result['left_status']}合计：{result['left_total']}；"
        f"{result['right_status']}合计：{result['right_total']}；"
        f"差额：{result['difference']}"
    )
    with (context.output_dir / "金额差额.txt").open("x", encoding="utf-8") as stream:
        stream.write(message + "\n")
        stream.write(f"匹配行数：{result['left_row_count']} / {result['right_row_count']}\n")
        stream.write("指令调用：" + " → ".join(result["instruction_calls"]) + "\n")
    return TaskResult(message=message, code="AMOUNT_DIFFERENCE_DONE")



def main() -> int:
    if len(sys.argv) == 1:
        return run_task(process)
    parser = argparse.ArgumentParser(description="读取 Excel，两组金额分别汇总后相减")
    parser.add_argument("input_path", help="输入 .xlsx 文件；原文件不会被修改")
    parser.add_argument("--sheet", help="工作表名称，默认第一张")
    parser.add_argument("--status-column", default=STATUS_COLUMN)
    parser.add_argument("--amount-column", default=AMOUNT_COLUMN)
    parser.add_argument("--left-status", default=LEFT_STATUS, help="被减数对应的状态")
    parser.add_argument("--right-status", default=RIGHT_STATUS, help="减数对应的状态")
    args = parser.parse_args()
    try:
        result = run_flow(
            args.input_path, args.sheet,
            status_column=args.status_column, amount_column=args.amount_column,
            left_status=args.left_status, right_status=args.right_status,
        )
    except InstructionError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
