from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook
from spiderfly_instructions import InstructionError, InstructionRegistry


EXAMPLE = Path(__file__).resolve().parents[2] / "examples/instruction_excel_amount_difference.py"
spec = importlib.util.spec_from_file_location("amount_difference_example", EXAMPLE)
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


class AmountDifferenceFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="spiderfly-amount-difference-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def book(self, rows, headers=("订单号", "状态", "金额")):
        target = self.directory / "input.xlsx"
        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = "订单"
            sheet.append(list(headers))
            for row in rows:
                sheet.append(row)
            workbook.create_sheet("其他表")["A1"] = "不要参与计算"
            workbook.save(target)
        finally:
            workbook.close()
        return target

    def test_real_instruction_calls_comma_values_and_unchanged_input(self):
        path = self.book([
            ["001", "待处理", "5,2"], ["002", "已完成", 12.3],
            ["003", "待处理", 5.6], ["004", "取消", "不参与计算"],
        ])
        before = path.read_bytes()
        calls = []
        original = InstructionRegistry.execute

        def trace(registry, name, inputs=None):
            result = original(registry, name, inputs)
            calls.append((name, inputs.get("value")))
            return result

        with patch.object(InstructionRegistry, "execute", new=trace):
            result = flow.run_flow(str(path))
        self.assertEqual(calls, [
            ("excel.read", None), ("table.filter_equals", "待处理"),
            ("table.filter_equals", "已完成"),
        ])
        self.assertEqual(result["instruction_calls"], [item[0] for item in calls])
        self.assertEqual((result["left_total"], result["right_total"], result["difference"]),
                         ("12.6", "12.3", "0.3"))
        self.assertEqual((result["left_row_count"], result["right_row_count"]), (2, 1))
        self.assertEqual(path.read_bytes(), before)

    def test_exact_decimals_empty_groups_and_negative_difference(self):
        for rows, expected in (
            ([["1", "待处理", 0.1], ["2", "待处理", 0.2], ["3", "已完成", 0.3]], "0.0"),
            ([["1", "已完成", 12.3]], "-12.3"),
            ([["1", "待处理 ", 5], ["2", "取消", "非法但不在分组内"]], "0"),
            ([], "0"),
            ([["1", "待处理", "-1,2"], ["2", "已完成", 1]], "0"),
        ):
            with self.subTest(rows=rows):
                self.assertEqual(flow.run_flow(str(self.book(rows)))["difference"], expected)

    def test_custom_sheet_columns_and_statuses(self):
        path = self.book([["a", "未发货", "2,3"], ["b", "已发货", 1]],
                         headers=("编号", "进度", "货款"))
        result = flow.run_flow(str(path), "订单", status_column="进度",
                               amount_column="货款", left_status="未发货", right_status="已发货")
        self.assertEqual(result["difference"], "4")
        self.assertEqual((result["left_status"], result["right_status"]), ("未发货", "已发货"))

    def test_invalid_matched_amounts_are_not_silently_skipped(self):
        for value in (None, True, " ", "5,,2", "oops", "NaN", "Infinity"):
            for status in ("待处理", "已完成"):
                with self.subTest(value=value, status=status):
                    path = self.book([["1", status, value]])
                    before = path.read_bytes()
                    with self.assertRaises(InstructionError) as caught:
                        flow.run_flow(str(path))
                    self.assertEqual(caught.exception.code, "FLOW_AMOUNT_INVALID")
                    self.assertEqual(path.read_bytes(), before)
        for value in ("1e99999999", "12345678901234567890123456789"):
            with self.subTest(value=value), self.assertRaises(InstructionError) as caught:
                flow.run_flow(str(self.book([["1", "待处理", value]])))
            self.assertEqual(caught.exception.code, "FLOW_AMOUNT_RANGE")

    def test_missing_required_column_comes_from_read_instruction(self):
        path = self.book([["1", "待处理"]], headers=("订单号", "状态"))
        with self.assertRaises(InstructionError) as caught:
            flow.run_flow(str(path))
        self.assertEqual(caught.exception.instruction_id, "excel.read")

    def test_cli_uses_same_flow_and_errors_return_nonzero(self):
        path = self.book([["1", "待处理", 5], ["2", "已完成", 2]])
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(EXAMPLE), str(path)],
            cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["difference"], "3")
        self.book([["1", "待处理", "bad"]])
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(EXAMPLE), str(path)],
            cwd=self.directory, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["code"], "FLOW_AMOUNT_INVALID")


if __name__ == "__main__":
    unittest.main()

