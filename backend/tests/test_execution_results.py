from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import execution_results


class ExecutionWorkspaceTests(unittest.TestCase):
    def test_workspace_is_unique_and_exposes_only_generated_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "executions"
            with patch.object(execution_results, "EXECUTIONS_DIR", root):
                workspace = execution_results.create_execution_workspace(42)
                environment = workspace.environment(42)
                with self.assertRaises(FileExistsError):
                    execution_results.create_execution_workspace(42)

            self.assertTrue(workspace.artifacts_dir.is_dir())
            self.assertTrue(workspace.downloads_dir.is_dir())
            self.assertTrue(workspace.screenshots_dir.is_dir())
            self.assertTrue(workspace.temporary_dir.is_dir())
            self.assertEqual(environment["SPIDERFLY_EXECUTION_ID"], "42")
            self.assertEqual(
                Path(environment["SPIDERFLY_RESULT_FILE"]), workspace.result_file
            )


class StructuredResultTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, **overrides: object) -> None:
        payload: dict[str, object] = {
            "schema_version": 1,
            "outcome": "success",
            "code": "OK",
            "message": "处理完成",
        }
        payload.update(overrides)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_missing_result_keeps_legacy_process_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=Path(temp_dir) / "result.json",
            )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.result_source, "legacy")

    def test_business_failure_overrides_a_zero_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(
                path,
                outcome="failure",
                code="DATA_EMPTY",
                message="没有找到可导出的数据",
                retryable=False,
            )
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.business_outcome, "failure")
        self.assertEqual(result.result_code, "DATA_EMPTY")
        self.assertEqual(result.error_message, "没有找到可导出的数据")

    def test_nonzero_exit_code_cannot_be_overridden_by_success_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(path)
            result = execution_results.resolve_execution_outcome(
                process_status="failed",
                exit_code=2,
                legacy_error="进程退出码 2",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "进程退出码 2")
        self.assertEqual(result.result_code, "OK")

    def test_nonzero_exit_code_keeps_technical_error_over_business_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(
                path,
                outcome="failure",
                code="PAGE_REJECTED",
                message="页面拒绝了操作",
            )
            result = execution_results.resolve_execution_outcome(
                process_status="failed",
                exit_code=3,
                legacy_error="Traceback: browser crashed",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error_message, "Traceback: browser crashed")
        self.assertEqual(result.result_message, "页面拒绝了操作")

    def test_manual_result_requires_a_link_or_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(path, outcome="manual_required", code="LOGIN_EXPIRED")
            with self.assertRaisesRegex(
                execution_results.ResultProtocolError, "人工介入结果必须提供"
            ):
                execution_results.load_structured_result(path)

            self._write(
                path,
                outcome="manual_required",
                code="LOGIN_EXPIRED",
                manual_action_url="https://example.com/login",
                manual_code="ACCOUNT_RELOGIN",
            )
            parsed = execution_results.load_structured_result(path)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.manual_code, "ACCOUNT_RELOGIN")

    def test_invalid_result_turns_a_zero_exit_into_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            path.write_text("{not-json", encoding="utf-8")
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result_code, "RESULT_INVALID")
        self.assertIn("结构化结果无效", result.result_message)

    def test_pathologically_nested_json_cannot_escape_the_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            prefix = '{"schema_version":1,"outcome":"success","code":"OK","ignored":'
            path.write_text(
                prefix + ("[" * 10_000) + "0" + ("]" * 10_000) + "}",
                encoding="utf-8",
            )
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result_code, "RESULT_INVALID")
        self.assertIn("过于复杂", result.result_message)

    def test_non_string_outcome_is_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(path, outcome=[])
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result_code, "RESULT_INVALID")

    def test_boolean_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(path, schema_version=True)
            with self.assertRaisesRegex(
                execution_results.ResultProtocolError, "schema_version"
            ):
                execution_results.load_structured_result(path)

    def test_timeout_is_never_overridden_by_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(path)
            result = execution_results.resolve_execution_outcome(
                process_status="timeout",
                exit_code=1,
                legacy_error="运行超时",
                result_file=path,
            )
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.error_message, "运行超时")

    def test_manual_url_rejects_embedded_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(
                path,
                outcome="manual_required",
                code="LOGIN_EXPIRED",
                manual_action_url="https://user:secret@example.com/login",
            )
            with self.assertRaisesRegex(
                execution_results.ResultProtocolError, "不含账号密码"
            ):
                execution_results.load_structured_result(path)

    def test_malformed_manual_url_becomes_a_protocol_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "result.json"
            self._write(
                path,
                outcome="manual_required",
                code="LOGIN_EXPIRED",
                manual_action_url="http://[::1",
            )
            result = execution_results.resolve_execution_outcome(
                process_status="success",
                exit_code=0,
                legacy_error="",
                result_file=path,
            )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.result_code, "RESULT_INVALID")
        self.assertIn("manual_action_url", result.result_message)


if __name__ == "__main__":
    unittest.main()
