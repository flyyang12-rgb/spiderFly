from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from example_flows.excel_name import run_demo, run_flow
from spiderfly_instructions import InstructionError, InstructionRegistry
from spiderfly_instructions.excel import READ_EXCEL
from spiderfly_instructions.excel_write import WRITE_EXCEL


class ExcelNameFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory(prefix="spiderfly-name-flow-tests-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.registry = InstructionRegistry()
        self.registry.register(READ_EXCEL)
        self.registry.register(WRITE_EXCEL)

    def make_input(self, name: str, columns: list[str], rows: list[dict]) -> Path:
        path = self.directory / name
        self.registry.execute("excel.write", {
            "file_path": str(path), "columns": columns, "rows": rows,
        })
        return path

    def read(self, path: Path):
        return self.registry.execute("excel.read", {"file_path": str(path)})

    def cli(self, *arguments: str):
        return subprocess.run(
            [sys.executable, "-m", "example_flows.excel_name", *arguments],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=15,
        )

    def test_real_flow_calls_shared_instructions_and_preserves_source_and_other_values(self) -> None:
        columns = ["编号", "姓", "名", "启用", "数量", "备注"]
        rows = [
            {"编号": "001", "姓": " 张 ", "名": " 三 ", "启用": False, "数量": 0, "备注": "  保留空格  "},
            {"编号": "002", "姓": "李", "名": "四", "启用": True, "数量": 2.5, "备注": "=原文"},
        ]
        source = self.make_input("input.xlsx", columns, rows)
        output = self.directory / "output.xlsx"
        original = source.read_bytes()
        actual_execute = InstructionRegistry.execute
        calls = []

        def record_execute(registry, instruction_id, inputs=None):
            calls.append(instruction_id)
            return actual_execute(registry, instruction_id, inputs)

        with patch.object(InstructionRegistry, "execute", new=record_execute):
            result = run_flow(str(source), str(output))

        self.assertEqual(calls, ["excel.read", "text.join_nonempty", "text.join_nonempty", "excel.write"])
        self.assertEqual(result, {
            "file_path": str(output.absolute()), "sheet_name": "数据", "row_count": 2,
        })
        self.assertEqual(source.read_bytes(), original)
        table = self.read(output)
        self.assertEqual(table.columns, [*columns, "姓名"])
        self.assertEqual(table.rows, [
            {**rows[0], "姓名": "张三"}, {**rows[1], "姓名": "李四"},
        ])
        self.assertIs(table.rows[0]["启用"], False)
        self.assertIs(type(table.rows[0]["数量"]), int)

    def test_none_blank_names_empty_rows_and_header_only_input_follow_existing_rules(self) -> None:
        columns = ["编号", "姓", "名"]
        rows = [
            {"编号": "001", "姓": None, "名": " 小明 "},
            {"编号": "002", "姓": " 王 ", "名": None},
            {"编号": "003", "姓": " ", "名": None},
            {"编号": None, "姓": None, "名": None},
        ]
        source = self.make_input("empty-values.xlsx", columns, rows)
        output = self.directory / "empty-values-result.xlsx"
        result = run_flow(str(source), str(output))
        self.assertEqual(result["row_count"], 3)
        self.assertEqual(self.read(output).rows, [
            {**rows[0], "姓名": "小明"},
            {**rows[1], "姓名": "王"},
            {**rows[2], "姓名": None},
        ])
        empty_source = self.make_input("header-only.xlsx", ["姓", "名"], [])
        empty_output = self.directory / "header-only-result.xlsx"
        empty_result = run_flow(str(empty_source), str(empty_output))
        self.assertEqual(empty_result["row_count"], 0)
        empty_table = self.read(empty_output)
        self.assertEqual(empty_table.columns, ["姓", "名", "姓名"])
        self.assertEqual(empty_table.rows, [])

    def test_invalid_names_missing_columns_and_existing_full_name_never_create_output(self) -> None:
        cases = [
            ("number", ["姓", "名"], [{"姓": "张", "名": "三"}, {"姓": None, "名": None}, {"姓": 123, "名": "四"}], "FLOW_NAME_INVALID"),
            ("boolean", ["姓", "名"], [{"姓": "张", "名": "三"}, {"姓": None, "名": None}, {"姓": "李", "名": False}], "FLOW_NAME_INVALID"),
            ("missing", ["姓"], [{"姓": "张"}], "EXCEL_COLUMNS_MISSING"),
            ("existing", ["姓", "名", "姓名"], [{"姓": "张", "名": "三", "姓名": "已有姓名"}], "FLOW_COLUMN_EXISTS"),
        ]
        for label, columns, rows, expected_code in cases:
            with self.subTest(case=label):
                source = self.make_input(f"{label}.xlsx", columns, rows)
                output = self.directory / f"{label}-result.xlsx"
                original = source.read_bytes()
                with self.assertRaises(InstructionError) as caught:
                    run_flow(str(source), str(output))
                self.assertEqual(caught.exception.code, expected_code)
                if expected_code == "FLOW_NAME_INVALID":
                    self.assertIn("第 2 条有效记录", caught.exception.to_dict()["message"])
                self.assertFalse(output.exists())
                self.assertEqual(source.read_bytes(), original)

    def test_existing_output_and_same_path_do_not_change_files(self) -> None:
        source = self.make_input("input.xlsx", ["姓", "名"], [{"姓": "张", "名": "三"}])
        output = self.directory / "existing.xlsx"
        output.write_bytes(b"existing output must stay unchanged")
        original_source = source.read_bytes()
        original_output = output.read_bytes()
        for target in (output, source):
            with self.subTest(target=target.name), self.assertRaises(InstructionError) as caught:
                run_flow(str(source), str(target))
            self.assertEqual(caught.exception.code, "EXCEL_FILE_EXISTS")
            self.assertEqual(source.read_bytes(), original_source)
            self.assertEqual(output.read_bytes(), original_output)

    def test_demo_creates_fictional_samples_and_refuses_an_existing_directory(self) -> None:
        folder = self.directory / "demo"
        result = run_demo(str(folder))
        self.assertEqual(result, {
            "file_path": str((folder / "结果.xlsx").absolute()), "sheet_name": "示例", "row_count": 3,
        })
        self.assertEqual({path.name for path in folder.iterdir()}, {"输入.xlsx", "结果.xlsx"})
        inputs = self.read(folder / "输入.xlsx")
        outputs = self.read(folder / "结果.xlsx")
        self.assertEqual(inputs.rows, [
            {"编号": "001", "姓": "张", "名": "三"},
            {"编号": "002", "姓": " 李 ", "名": " 四 "},
            {"编号": "003", "姓": "王", "名": None},
        ])
        self.assertEqual(outputs.rows, [
            {**row, "姓名": name} for row, name in zip(inputs.rows, ["张三", "李四", "王"])
        ])
        snapshots = {path.name: path.read_bytes() for path in folder.iterdir()}
        with self.assertRaises(InstructionError) as caught:
            run_demo(str(folder))
        self.assertEqual(caught.exception.code, "FLOW_DEMO_DIRECTORY_INVALID")
        self.assertEqual({path.name: path.read_bytes() for path in folder.iterdir()}, snapshots)

    def test_normal_cli_selects_a_sheet_and_returns_structured_instruction_errors(self) -> None:
        source = self.directory / "multiple-sheets.xlsx"
        workbook = Workbook()
        workbook.active.title = "说明"
        workbook.active.append(["这里不是数据表"])
        names = workbook.create_sheet("名单")
        names.append(["姓", "名"])
        names.append(["赵", "六"])
        try:
            workbook.save(source)
        finally:
            workbook.close()
        output = self.directory / "cli-result.xlsx"
        completed = self.cli(str(source), str(output), "--sheet", "名单")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["sheet_name"], "名单")
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(self.read(output).rows, [{"姓": "赵", "名": "六", "姓名": "赵六"}])
        failed_output = self.directory / "failed-cli-result.xlsx"
        failed = self.cli(str(source), str(failed_output), "--sheet", "不存在")
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(failed.stdout, "")
        self.assertEqual(json.loads(failed.stderr)["code"], "EXCEL_SHEET_MISSING")
        self.assertFalse(failed_output.exists())

    def test_demo_cli_runs_and_rejects_reuse_or_conflicting_modes(self) -> None:
        folder = self.directory / "cli-demo"
        completed = self.cli("--demo", str(folder))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        self.assertEqual(result["row_count"], 3)
        self.assertEqual([row["姓名"] for row in self.read(Path(result["file_path"])).rows], ["张三", "李四", "王"])
        repeated = self.cli("--demo", str(folder))
        self.assertEqual(repeated.returncode, 2)
        self.assertEqual(repeated.stdout, "")
        self.assertEqual(json.loads(repeated.stderr)["code"], "FLOW_DEMO_DIRECTORY_INVALID")
        unused_folder = self.directory / "invalid-mode"
        conflicting = self.cli("--demo", str(unused_folder), "--sheet", "名单")
        self.assertEqual(conflicting.returncode, 2)
        self.assertFalse(unused_folder.exists())


if __name__ == "__main__":
    unittest.main()
