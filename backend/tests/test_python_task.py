from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from spiderfly_instructions import InstructionError
from spiderfly_instructions import task
from spiderfly_instructions.task import TaskContext, TaskResult, run_task


class PythonTaskTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="spiderfly-python-task-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.upload = self.root / "原件.xlsx"
        self.upload.write_bytes(b"unchanged input fixture")
        self.index = 0

    def execution(self, upload=True):
        self.index += 1
        directory = self.root / str(self.index)
        directory.mkdir()
        self.artifacts = directory / "artifacts"
        self.artifacts.mkdir()
        self.receipt = directory / "result.json"
        environment = {
            "SPIDERFLY_RESULT_FILE": str(self.receipt),
            "SPIDERFLY_ARTIFACT_DIR": str(self.artifacts),
        }
        if upload:
            environment["SPIDERFLY_TEMPLATE_FILE"] = str(self.upload)
        return environment

    def invoke(self, callback, environment, **kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, environment, clear=True), redirect_stdout(stdout), redirect_stderr(stderr):
            result = run_task(callback, **kwargs)
        return result, stdout.getvalue(), stderr.getvalue()

    def payload(self):
        return json.loads(self.receipt.read_text(encoding="utf-8"))

    def test_input_is_fully_copied_before_callback_and_success_receipt(self):
        environment = self.execution()
        seen = []

        def process(context):
            self.assertIsInstance(context, TaskContext)
            self.assertNotEqual(context.input_file, self.upload)
            self.assertEqual(context.input_file.read_bytes(), self.upload.read_bytes())
            self.assertTrue(context.output_dir.is_relative_to(self.artifacts))
            (context.output_dir / "结果.txt").write_text("已处理", encoding="utf-8")
            seen.append(context)
            return TaskResult("处理成功", "CUSTOM_DONE")

        result, stdout, stderr = self.invoke(process, environment)
        self.assertEqual((result, stdout, stderr), (0, "处理成功\n", ""))
        self.assertEqual(len(seen), 1)
        self.assertEqual(self.payload(), {
            "schema_version": 1, "outcome": "success", "code": "CUSTOM_DONE",
            "message": "处理成功", "retryable": False,
        })
        self.assertEqual(self.upload.read_bytes(), b"unchanged input fixture")
        self.assertFalse(list(self.receipt.parent.glob(".task-result-*")))

    def test_optional_input_runs_without_upload_but_required_input_does_not(self):
        environment = self.execution(upload=False)

        def process(context):
            self.assertIsNone(context.input_file)
            return TaskResult("无需输入")

        self.assertEqual(self.invoke(process, environment, require_input=False)[0], 0)
        environment = self.execution(upload=False)
        callback = Mock()
        self.assertEqual(self.invoke(callback, environment)[0], 1)
        callback.assert_not_called()
        self.assertEqual(self.payload()["outcome"], "failure")

    def test_bad_paths_or_missing_runtime_never_execute_business(self):
        for problem in ("missing_result", "relative_result", "missing_artifacts", "missing_upload"):
            environment = self.execution()
            if problem == "missing_result":
                environment.pop("SPIDERFLY_RESULT_FILE")
            elif problem == "relative_result":
                environment["SPIDERFLY_RESULT_FILE"] = "result.json"
            elif problem == "missing_artifacts":
                environment["SPIDERFLY_ARTIFACT_DIR"] = str(self.root / "absent")
            else:
                environment["SPIDERFLY_TEMPLATE_FILE"] = str(self.root / "absent.xlsx")
            with self.subTest(problem=problem):
                callback = Mock()
                self.assertEqual(self.invoke(callback, environment)[0], 1)
                callback.assert_not_called()
                if self.receipt.exists():
                    self.assertEqual(self.payload()["outcome"], "failure")

    def test_partial_copy_does_not_expose_input_or_execute_business(self):
        environment = self.execution()
        callback = Mock()

        def interrupted(source, destination):
            destination.write(source.read(5))
            raise OSError("copy interrupted")

        with patch.object(task.shutil, "copyfileobj", side_effect=interrupted):
            self.assertEqual(self.invoke(callback, environment)[0], 1)
        callback.assert_not_called()
        self.assertEqual(self.payload()["outcome"], "failure")
        self.assertEqual(list((self.artifacts / "流程文件").iterdir()), [])
        self.assertEqual(self.upload.read_bytes(), b"unchanged input fixture")

    def test_business_errors_invalid_results_and_early_exit_are_failures(self):
        for label, callback, code in (
            ("instruction", Mock(side_effect=InstructionError("EXCEL_COLUMN_MISSING", "excel.read", "execute", "缺少金额列")), "EXCEL_COLUMN_MISSING"),
            ("exception", Mock(side_effect=ValueError("计算失败")), "TASK_FAILED"),
            ("empty_return", Mock(return_value=None), "TASK_FAILED"),
            ("system_exit_zero", Mock(side_effect=SystemExit(0)), "TASK_INTERRUPTED"),
            ("invalid_result_code", lambda _: TaskResult("结果", "not valid"), "TASK_FAILED"),
        ):
            with self.subTest(label=label):
                environment = self.execution()
                self.assertEqual(self.invoke(callback, environment)[0], 1)
                self.assertEqual(self.payload()["outcome"], "failure")
                self.assertEqual(self.payload()["code"], code)
                if label == "instruction":
                    self.assertIn("excel.read（execute）", self.payload()["message"])
                self.assertEqual((self.artifacts / "流程文件/输入.xlsx").read_bytes(), self.upload.read_bytes())

    def test_receipt_write_failure_never_reports_success(self):
        environment = self.execution()
        original = task._write_receipt
        calls = []

        def fail_success(path, outcome, result):
            calls.append(outcome)
            if outcome == "success":
                raise OSError("cannot save success receipt")
            return original(path, outcome, result)

        with patch.object(task, "_write_receipt", side_effect=fail_success):
            result, stdout, _ = self.invoke(lambda _: TaskResult("应该成功"), environment)
        self.assertEqual(result, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(calls, ["success", "failure"])
        self.assertEqual(self.payload()["outcome"], "failure")
        environment = self.execution()
        with patch.object(task.os, "replace", side_effect=OSError("no replace")):
            self.assertEqual(self.invoke(lambda _: TaskResult("不能成功"), environment)[0], 1)
        self.assertFalse(self.receipt.exists())
        self.assertFalse(list(self.receipt.parent.glob(".task-result-*")))

    def test_same_execution_cannot_overwrite_previous_receipt_or_artifacts(self):
        environment = self.execution()

        def process(context):
            (context.output_dir / "结果.txt").write_text("保留", encoding="utf-8")
            return TaskResult("完成")

        self.assertEqual(self.invoke(process, environment)[0], 0)
        before = {path.relative_to(self.receipt.parent): path.read_bytes()
                  for path in self.receipt.parent.rglob("*") if path.is_file()}
        callback = Mock()
        self.assertEqual(self.invoke(callback, environment)[0], 1)
        callback.assert_not_called()
        self.assertEqual(before, {path.relative_to(self.receipt.parent): path.read_bytes()
                                  for path in self.receipt.parent.rglob("*") if path.is_file()})

    def test_logging_failure_cannot_rewrite_completed_business_receipt(self):
        environment = self.execution()
        with patch.dict(os.environ, environment, clear=True), patch("builtins.print", side_effect=OSError("pipe closed")):
            self.assertEqual(run_task(lambda _: TaskResult("业务已完成")), 0)
        self.assertEqual(self.payload()["outcome"], "success")


if __name__ == "__main__":
    unittest.main()

