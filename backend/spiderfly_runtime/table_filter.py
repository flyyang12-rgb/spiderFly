"""Keep rows whose selected column equals a value, without changing the table."""

from __future__ import annotations

from datetime import datetime, time
from math import isfinite, isnan

from pydantic import Field

from ._validation import DataModel, TaskError, checked
from .excel import CellValue


OPERATION = "table.filter_equals"


class FilterEqualsInput(DataModel):
    columns: list[str] = Field(min_length=1, description="表格的完整列名，按原顺序填写")
    rows: list[dict[str, CellValue]] = Field(description="表格数据，每行字段须与列名一致")
    column: str = Field(min_length=1, description="用于筛选的列名，必须存在于表头")
    value: CellValue = Field(description="要匹配的值；不自动转换文字、数字或空值")


class FilterEqualsOutput(DataModel):
    columns: list[str] = Field(min_length=1, description="保留原顺序的全部列名")
    rows: list[dict[str, CellValue]] = Field(description="符合条件的完整数据行，保留顺序和重复行")
    row_count: int = Field(ge=0, description="符合条件的数据行数")


def _failure(code: str, message: str) -> TaskError:
    return TaskError(code, OPERATION, "execute", message)


def _check_comparable(value: CellValue, location: str) -> None:
    if isinstance(value, float) and not isfinite(value):
        raise _failure("TABLE_VALUE_INVALID", f"{location}含非有限数字，请先处理该值。")
    if isinstance(value, (datetime, time)) and value.tzinfo is not None:
        raise _failure("TABLE_VALUE_INVALID", f"{location}带有时区，请先转换为约定的本地时间。")


def _matches(left: CellValue, right: CellValue) -> bool:
    if type(left) is type(right):
        return left == right
    # Numeric cells may be returned as int or float after an Excel round trip.
    # bool is deliberately excluded even though Python considers True == 1.
    return type(left) in (int, float) and type(right) in (int, float) and left == right


def _filter_equals(inputs: FilterEqualsInput) -> dict[str, object]:
    if any(not column or column != column.strip() for column in inputs.columns):
        raise _failure("TABLE_COLUMNS_INVALID", "列名不能为空，也不能在首尾带空格。")
    columns = set(inputs.columns)
    if len(columns) != len(inputs.columns):
        raise _failure("TABLE_COLUMNS_INVALID", "列名不能重复。")
    if inputs.column not in columns:
        raise _failure("TABLE_COLUMN_MISSING", f"找不到筛选列“{inputs.column}”，请检查列名。")
    _check_comparable(inputs.value, "筛选值")
    # Check the entire table, including rows that will not be selected.
    for index, row in enumerate(inputs.rows, start=1):
        if set(row) != columns:
            raise _failure("TABLE_ROW_INVALID", f"第 {index} 条数据的字段与列名不一致。")
        _check_comparable(row[inputs.column], f"第 {index} 条数据的筛选列")
    rows = [
        {column: row[column] for column in inputs.columns}
        for row in inputs.rows
        if _matches(row[inputs.column], inputs.value)
    ]
    return {"columns": list(inputs.columns), "rows": rows, "row_count": len(rows)}


def _unchanged(left: CellValue, right: CellValue) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, (datetime, time)):
        return (
            left.isoformat() == right.isoformat()
            and left.tzinfo == right.tzinfo
            and left.tzname() == right.tzname()
            and left.fold == right.fold
        )
    # Unrelated columns are retained, even if their values are not comparable.
    if isinstance(left, float) and isnan(left) and isnan(right):
        return True
    return left == right


def verify_filter_equals(inputs: FilterEqualsInput, result: FilterEqualsOutput) -> bool:
    expected = [row for row in inputs.rows if _matches(row[inputs.column], inputs.value)]
    return (
        result.columns == inputs.columns
        and result.row_count == len(expected) == len(result.rows)
        and all(
            list(actual) == inputs.columns
            and all(_unchanged(actual[column], original[column]) for column in inputs.columns)
            for actual, original in zip(result.rows, expected)
        )
    )


def filter_equals(columns: list[str], rows: list[dict], column: str, value: CellValue) -> FilterEqualsOutput:
    return checked(OPERATION, FilterEqualsInput, FilterEqualsOutput, _filter_equals, verify_filter_equals, dict(columns=columns, rows=rows, column=column, value=value))
