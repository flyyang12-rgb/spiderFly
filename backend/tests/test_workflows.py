"""Run only from the documented isolated backend copy; all files are synthetic."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app import environments, native_process
from app.services import workflows


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="spiderfly-workflow-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.apps = self.root / "apps"
        self.envs = self.root / "envs"
        self.snapshots = self.root / "workflow_snapshots"
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE users(id INTEGER PRIMARY KEY,display_name TEXT);
            INSERT INTO users VALUES(7,'测试管理员');
            CREATE TABLE rpa_apps(id INTEGER PRIMARY KEY,script_path TEXT,requirements_text TEXT,
                env_path TEXT,environment_status TEXT,archived INTEGER,template_path TEXT,
                template_filename TEXT,revision INTEGER);
            CREATE TABLE tasks(id INTEGER PRIMARY KEY,name TEXT,description TEXT DEFAULT '',
                app_id INTEGER,version INTEGER DEFAULT 1,enabled INTEGER DEFAULT 1,
                archived INTEGER DEFAULT 0,target_host_id INTEGER,notify_on_success INTEGER DEFAULT 0,
                notify_on_failure INTEGER DEFAULT 0,failure_screenshot INTEGER DEFAULT 0,
                last_status TEXT,updated_at TEXT,trigger_type TEXT DEFAULT 'manual');
            CREATE TABLE executions(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER,status TEXT,
                trigger_source TEXT,requested_by INTEGER,script_path_snapshot TEXT,
                python_path_snapshot TEXT,maintenance_snapshot TEXT DEFAULT '',created_at TEXT,
                started_at TEXT,ended_at TEXT,error_message TEXT DEFAULT '',result_message TEXT DEFAULT '');
            CREATE TABLE remote_runs(id INTEGER PRIMARY KEY,task_id INTEGER,status TEXT);
            CREATE TABLE maintenance_jobs(id INTEGER PRIMARY KEY,task_id INTEGER,status TEXT,
                rerun_execution_id INTEGER,ended_at TEXT);
        """)
        workflows.ensure_schema(self.conn)
        native_process.ensure_schema(self.conn)
        self.addCleanup(patch.stopall)
        patch.object(workflows, "SNAPSHOT_ROOT", self.snapshots).start()
        patch.object(workflows, "RPA_APPS_DIR", self.apps).start()
        patch.object(workflows, "RPA_ENVS_DIR", self.envs).start()
        patch.object(environments, "RPA_APPS_DIR", self.apps).start()
        patch.object(environments, "RPA_ENVS_DIR", self.envs).start()
        patch.object(workflows, "transaction", self.transaction).start()
        patch.object(workflows, "DEFAULT_TASK_TIMEOUT_SECONDS", 123).start()
        patch.object(workflows, "MANAGED_BROWSER_PORT", 9123).start()
        for number in range(1, 4):
            app = self.apps / str(number)
            app.mkdir(parents=True)
            (app / "main.py").write_text(f"print('synthetic-{number}')\n", encoding="utf-8")
            (app / "template.xlsx").write_bytes(f"synthetic-template-{number}".encode())
            env = self.envs / f"app-{number}-r1"
            python = environments._environment_python(env)
            python.parent.mkdir(parents=True)
            python.write_bytes(b"synthetic-interpreter")
            self.conn.execute("INSERT INTO rpa_apps VALUES(?,?,?,?,'ready',0,?,'template.xlsx',1)", (number, str(app / "main.py"), "", str(env), str(app / "template.xlsx")))
            self.conn.execute("INSERT INTO tasks(id,name,app_id) VALUES(?,?,?)", (number, f"任务{number}", number))
        self.conn.commit()

    @contextmanager
    def transaction(self):
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def definition(self, members=(1, 2, 3), name="合成流程"):
        result = workflows.create_definition(self.conn, name, "顺序运行合成脚本", list(members), 7)
        self.conn.commit()
        return result

    def start(self, definition=None, request="request-1", **kwargs):
        return workflows.start_run((definition or self.definition())["id"], 7, request, **kwargs)

    def view(self, run_id):
        return workflows.get_run(self.conn, run_id)

    def complete(self, execution_id, status="success", *, confirm=True, message=""):
        self.conn.execute("UPDATE executions SET status=?,started_at='2026-10-13T01:00:00+00:00',ended_at='2026-10-13T01:01:00+00:00',error_message=? WHERE id=?", (status, message, execution_id))
        if not self.conn.execute("SELECT 1 FROM execution_settlements WHERE execution_id=?", (execution_id,)).fetchone():
            native_process._begin_settlement_conn(self.conn, execution_id)
        if confirm:
            native_process._update_conn(self.conn, execution_id, processes_gone=1, workspace_cleanup_confirmed=1, runner_returned=1)

    def count_executions(self):
        return self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0]

    def test_schema_keeps_transaction_and_history_after_task_deletion(self):
        self.conn.execute("BEGIN IMMEDIATE")
        workflows.ensure_schema(self.conn)
        self.assertTrue(self.conn.in_transaction)
        workflows.create_definition(self.conn, "rolled-back", "", [1], 7)
        self.conn.rollback()
        self.assertEqual(workflows.list_definitions(self.conn), [])
        definition = self.definition((1,))
        workflows.ensure_schema(self.conn)
        self.conn.execute("DELETE FROM tasks WHERE id=1")
        saved = workflows.get_definition(self.conn, definition["id"])
        self.assertFalse(saved["steps"][0]["available"])

    def test_empty_duplicate_missing_remote_and_non_native_members_rejected(self):
        for members in ([], [1, 1], [99]):
            with self.subTest(members=members), self.assertRaises(HTTPException):
                self.definition(members)
        self.conn.execute("UPDATE tasks SET target_host_id=5 WHERE id=1")
        with self.assertRaises(HTTPException):
            self.definition((1,))
        self.conn.execute("UPDATE tasks SET target_host_id=NULL WHERE id=1")
        self.conn.execute("CREATE TABLE maintenance_policies(task_id INTEGER PRIMARY KEY,runtime TEXT)")
        for runtime in ("readonly-v1", "collection-v1", "drissionpage-v1"):
            self.conn.execute("INSERT OR REPLACE INTO maintenance_policies VALUES(1,?)", (runtime,))
            with self.assertRaises(HTTPException):
                self.definition((1,))

    def test_single_step_valid_and_future_steps_not_execution_rows(self):
        first = self.start()
        self.assertEqual(self.count_executions(), 1)
        self.assertEqual([step["status"] for step in first["steps"]], ["pending", "waiting", "waiting"])
        single = self.definition((3,), "单步流程")
        item = self.start(single)
        self.assertEqual(len(item["steps"]), 1)
        self.complete(item["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(item["id"])["status"], "success")

    def test_success_requires_all_cleanup_facts_before_next_step(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.complete(execution, confirm=False)
        workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 1)
        self.assertIn("确认", self.view(run["id"])["reason"])
        native_process._update_conn(self.conn, execution, processes_gone=1, workspace_cleanup_confirmed=1)
        workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 1)
        native_process._update_conn(self.conn, execution, runner_returned=1)
        workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 2)
        for _ in range(3):
            workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 2)
        self.assertEqual([s["status"] for s in self.view(run["id"])["steps"]], ["success", "pending", "waiting"])

    def test_three_steps_follow_only_their_own_execution_results(self):
        run = self.start()
        for index in range(3):
            current = self.view(run["id"])["steps"][index]
            self.assertEqual(current["status"], "pending")
            self.complete(current["execution_id"])
            workflows.reconcile(self.conn)
            self.assertEqual(self.count_executions(), min(index + 2, 3))
        self.assertEqual(self.view(run["id"])["status"], "success")
        self.assertIsNotNone(self.view(run["id"])["started_at"])

    def test_failed_timeout_and_business_failure_stop_downstream(self):
        for status, message in (("failed", "业务回执 failure"), ("failed", "manual_required"), ("timeout", "全局超时"), ("failed", "清理失败")):
            with self.subTest(status=status, message=message):
                definition = self.definition(name=message)
                run = self.start(definition, request=message)
                execution = run["steps"][0]["execution_id"]
                self.complete(execution, status, message=message)
                workflows.reconcile(self.conn)
                result = self.view(run["id"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual([s["status"] for s in result["steps"]], [status, "skipped", "skipped"])
                self.assertEqual(result["reason"], message)
                self.assertIsNone(result["steps"][1]["execution_id"])

    def test_network_retry_identity_survives_terminal_archive_and_member_delete(self):
        definition = self.definition((1,))
        run = self.start(definition)
        self.assertEqual(self.start(definition)["id"], run["id"])
        with self.assertRaises(HTTPException):
            self.start(definition, intent={"different": True})
        with self.assertRaises(HTTPException):
            self.start(definition, request="new-click")
        self.complete(run["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        workflows.archive_definition(self.conn, definition["id"], 7)
        self.conn.execute("DELETE FROM tasks WHERE id=1")
        self.assertEqual(self.start(definition)["id"], run["id"])
        self.assertEqual(self.count_executions(), 1)

    def test_frozen_sources_templates_not_ai_configuration_dependent(self):
        run = self.start()
        steps = self.conn.execute("SELECT * FROM workflow_steps WHERE run_id=? ORDER BY position", (run["id"],)).fetchall()
        self.assertEqual(len(steps), 3)
        self.assertFalse(self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_policies'").fetchone())
        for step in steps:
            snapshot = json.loads(step["snapshot"])
            self.assertEqual(snapshot["timeout_seconds"], 123)
            self.assertEqual(snapshot["runtime"], "native")
            source = self.apps / str(step["task_id"]) / "main.py"
            template = source.parent / "template.xlsx"
            source.write_text("print('changed')", encoding="utf-8")
            template.write_bytes(b"changed")
            self.assertEqual(Path(snapshot["script_path_snapshot"]).read_text("utf-8"), f"print('synthetic-{step['task_id']}')\n")
            self.assertEqual(Path(snapshot["template_path"]).read_bytes(), f"synthetic-template-{step['task_id']}".encode())
        snapshot = workflows.execution_snapshot(self.conn, run["steps"][0]["execution_id"])
        self.assertFalse(snapshot["notify_on_success"])
        self.conn.execute("UPDATE tasks SET notify_on_success=1,version=9 WHERE id=1")
        self.assertFalse(workflows.execution_snapshot(self.conn, run["steps"][0]["execution_id"])["notify_on_success"])

    def test_preparation_failure_has_no_half_round_and_cleans_owned_files(self):
        definition = self.definition()
        (self.apps / "2" / "template.xlsx").unlink()
        with self.assertRaises(ValueError):
            self.start(definition)
        self.assertEqual(self.count_executions(), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM workflow_runs").fetchone()[0], 0)
        self.assertEqual(list(self.snapshots.iterdir()), [])

    def test_missing_or_corrupt_future_snapshot_fails_without_using_current_version(self):
        run = self.start()
        step = self.conn.execute("SELECT snapshot FROM workflow_steps WHERE run_id=? AND position=2", (run["id"],)).fetchone()
        snapshot = json.loads(step["snapshot"])
        Path(snapshot["template_path"]).write_bytes(b"bad-hash")
        self.complete(run["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        result = self.view(run["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("哈希", result["reason"])
        self.assertEqual(self.count_executions(), 1)
        self.assertEqual([s["status"] for s in result["steps"]], ["success", "failed", "skipped"])

    def test_execution_snapshot_manifest_damage_is_explicit(self):
        run = self.start((self.definition((1,))))
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE workflow_steps SET snapshot='{}' WHERE execution_id=?", (execution,))
        with self.assertRaisesRegex(ValueError, "哈希"):
            workflows.execution_snapshot(self.conn, execution)

    def test_definition_revision_edit_does_not_change_accepted_order(self):
        definition = self.definition()
        run = self.start(definition)
        edited = workflows.update_definition(self.conn, definition["id"], "新名称", "", [3, 2, 1], 7, expected_revision=1)
        self.assertEqual(edited["revision"], 2)
        self.assertEqual([s["task_id"] for s in self.view(run["id"])["steps"]], [1, 2, 3])
        with self.assertRaises(HTTPException):
            workflows.update_definition(self.conn, definition["id"], "冲突", "", [1], 7, expected_revision=1)
        with self.assertRaises(HTTPException):
            workflows.archive_definition(self.conn, definition["id"], 7)

    def test_current_busy_waits_but_future_busy_does_not_prevent_acceptance(self):
        self.conn.execute("INSERT INTO executions(task_id,status) VALUES(1,'pending')")
        first = self.start()
        self.assertEqual(first["status"], "waiting")
        self.assertIsNone(first["steps"][0]["execution_id"])
        self.assertEqual(self.count_executions(), 1)
        self.conn.execute("DELETE FROM executions")
        self.conn.execute("INSERT INTO executions(task_id,status) VALUES(2,'pending')")
        workflows.reconcile(self.conn)
        first = self.view(first["id"])
        self.assertIsNotNone(first["steps"][0]["execution_id"])
        self.complete(first["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        self.assertIsNone(self.view(first["id"])["steps"][1]["execution_id"])
        self.assertEqual(self.view(first["id"])["current_step"], 2)
        self.assertIn("等待", self.view(first["id"])["reason"])

    def test_two_workflows_share_task_without_overlap_or_future_reservation(self):
        a = self.start()
        b = self.start(self.definition((1,), "另一个流程"))
        self.assertEqual(self.count_executions(), 1)
        self.assertEqual(b["status"], "waiting")
        self.assertTrue(workflows.task_busy_reason(self.conn, 1, workflow_only=True))
        self.assertEqual(workflows.task_busy_reason(self.conn, 2, workflow_only=True), "")
        self.complete(a["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 3)
        self.assertIsNotNone(self.view(b["id"])["steps"][0]["execution_id"])

    def test_remote_and_legacy_maintenance_activity_blocks_current_dispatch(self):
        for source, sql in (("remote", "INSERT INTO remote_runs VALUES(1,1,'uncertain')"), ("maintenance", "INSERT INTO maintenance_jobs VALUES(1,1,'ready',NULL,NULL)")):
            with self.subTest(source=source):
                self.conn.execute(sql)
                run = self.start(self.definition((1,), source), request=source)
                self.assertEqual(run["status"], "waiting")
                self.assertIsNone(run["steps"][0]["execution_id"])
                workflows.request_stop(run["id"], "测试管理员", self.conn)
                self.conn.execute("DELETE FROM remote_runs")
                self.conn.execute("DELETE FROM maintenance_jobs")

    def test_stop_pending_is_durable_and_confirms_unstarted_before_release(self):
        run = self.start()
        result = workflows.request_stop(run["id"], "测试管理员", self.conn)
        self.assertTrue(result["stop_requested"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual([s["status"] for s in result["steps"]], ["cancelled", "cancelled", "cancelled"])
        proof = self.conn.execute("SELECT * FROM execution_settlements").fetchone()
        self.assertTrue(proof["cleanup_confirmed"])
        workflows.reconcile(self.conn)
        workflows.request_stop(run["id"], "再停止", self.conn)
        self.assertEqual(self.count_executions(), 1)

    def test_stop_running_waits_for_cleanup_and_late_success_cannot_advance(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE executions SET status='running',started_at='now' WHERE id=?", (execution,))
        result = workflows.request_stop(run["id"], "测试管理员", self.conn)
        self.assertEqual(result["stop_execution_ids"], [execution])
        self.assertEqual(result["status"], "stopping")
        self.complete(execution, confirm=False)
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["status"], "stopping")
        for _ in range(3):
            workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["reason"].count("等待步骤"), 1)
        native_process._update_conn(self.conn, execution, processes_gone=1, workspace_cleanup_confirmed=1, runner_returned=1)
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["status"], "cancelled")
        self.assertEqual(self.count_executions(), 1)

    def test_stop_external_cancelled_step_ends_flow(self):
        run = self.start()
        self.complete(run["steps"][0]["execution_id"], "cancelled", message="普通取消入口")
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["status"], "cancelled")
        self.assertEqual(self.count_executions(), 1)

    def test_disabled_future_task_cancels_but_changed_target_keeps_frozen_local(self):
        run = self.start()
        self.conn.execute("UPDATE tasks SET target_host_id=9 WHERE id=2")
        self.complete(run["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        second = self.view(run["id"])["steps"][1]
        self.assertIsNotNone(second["execution_id"])
        snapshot = workflows.execution_snapshot(self.conn, second["execution_id"])
        self.assertIsNone(snapshot["target_host_id"])
        workflows.assert_execution_allowed(self.conn, second["execution_id"])
        self.conn.execute("UPDATE tasks SET enabled=0 WHERE id=3")
        self.complete(second["execution_id"])
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["status"], "cancelled")
        self.assertEqual(self.count_executions(), 2)

    def test_disabled_current_task_is_checked_before_business_gate(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE tasks SET enabled=0 WHERE id=1")
        with self.assertRaisesRegex(InterruptedError, "停用"):
            workflows.assert_execution_allowed(self.conn, execution)
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["status"], "cancelled")

    def test_restart_interrupted_running_fails_without_retry_and_waits_for_proof(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE executions SET status='running',started_at='before-crash' WHERE id=?", (execution,))
        workflows.reconcile(self.conn, recover=True)
        self.assertEqual(self.view(run["id"])["status"], "running")
        self.assertEqual(self.count_executions(), 1)
        self.complete(execution, "failed", message="主控异常中断")
        workflows.reconcile(self.conn, recover=True)
        self.assertEqual(self.view(run["id"])["status"], "failed")
        self.assertEqual(self.count_executions(), 1)

    def test_missing_execution_association_holds_occupancy_and_deletion(self):
        run = self.start()
        self.conn.execute("DELETE FROM executions WHERE id=?", (run["steps"][0]["execution_id"],))
        workflows.reconcile(self.conn)
        self.assertIn("关联", self.view(run["id"])["reason"])
        self.assertTrue(workflows.active_task_reference(self.conn, 3))
        with self.assertRaises(RuntimeError):
            workflows.delete_task_history(self.conn, 3)

    def test_all_future_environments_protected_and_corrupt_reference_fails_closed(self):
        run = self.start()
        self.assertEqual(len(workflows.protected_python_paths(self.conn)), 3)
        self.complete(run["steps"][0]["execution_id"])
        workflows.reconcile(self.conn)
        self.assertEqual(len(workflows.protected_python_paths(self.conn, 1)), 1)
        self.conn.execute("UPDATE workflow_steps SET snapshot='{}' WHERE run_id=? AND position=3", (run["id"],))
        with self.assertRaises(RuntimeError):
            workflows.protected_python_paths(self.conn)

    def test_member_deletion_stops_whole_round_before_files_can_be_deleted(self):
        run = self.start()
        self.assertEqual(workflows.prepare_task_deletion(3), [])
        self.assertEqual(self.view(run["id"])["status"], "cancelled")
        self.assertFalse(workflows.active_task_reference(self.conn, 3))
        self.assertEqual(self.count_executions(), 1)

    def test_light_history_survives_hard_delete_and_snapshot_cleanup_retries(self):
        run = self.start(self.definition((1,)))
        self.complete(run["steps"][0]["execution_id"], "failed", message="合成错误摘要")
        workflows.reconcile(self.conn)
        paths = workflows.delete_task_history(self.conn, 1)
        self.conn.commit()
        self.assertTrue(paths)
        with patch.object(workflows.shutil, "rmtree", side_effect=PermissionError("synthetic access denied")):
            with self.assertRaises(PermissionError):
                workflows.remove_snapshot_directories(paths)
        self.assertEqual(workflows.delete_task_history(self.conn, 1), paths)
        self.conn.commit()
        workflows.remove_snapshot_directories(paths)
        self.assertFalse(Path(paths[0]).exists())
        self.conn.execute("DELETE FROM executions WHERE task_id=1")
        self.conn.execute("DELETE FROM tasks WHERE id=1")
        history = self.view(run["id"])["steps"][0]
        self.assertEqual((history["task_name"], history["task_version"], history["position"], history["status"], history["error_summary"]), ("任务1", 1, 1, "failed", "合成错误摘要"))
        self.assertIsNotNone(history["started_at"])
        self.assertFalse(history["execution_available"])
        encoded = json.dumps(self.view(run["id"]), ensure_ascii=False)
        for private in ("snapshot", "script_path", "requirements", str(self.root)):
            self.assertNotIn(private, encoded)

    def test_restart_with_accepted_pending_returns_same_execution(self):
        run = self.start()
        for _ in range(4):
            workflows.reconcile(self.conn, recover=True)
        self.assertEqual(self.count_executions(), 1)
        self.assertEqual(self.view(run["id"])["steps"][0]["execution_id"], run["steps"][0]["execution_id"])

    def test_whole_rerun_uses_new_id_and_current_source_without_changing_old(self):
        definition = self.definition((1,))
        first = self.start(definition)
        self.complete(first["steps"][0]["execution_id"], "failed", message="原失败")
        workflows.reconcile(self.conn)
        (self.apps / "1" / "main.py").write_text("print('fixed')", encoding="utf-8")
        second = self.start(definition, request="rerun", rerun_of=first["id"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(self.view(first["id"])["status"], "failed")
        snapshot = workflows.execution_snapshot(self.conn, second["steps"][0]["execution_id"])
        self.assertEqual(Path(snapshot["script_path_snapshot"]).read_text("utf-8"), "print('fixed')")
        self.assertEqual(self.start(definition, request="rerun", rerun_of=first["id"])["id"], second["id"])
        with self.assertRaises(HTTPException):
            self.start(definition, request="cannot-rerun-active", rerun_of=second["id"])

    def test_history_filters_paginate_and_have_no_full_snapshots(self):
        for number in range(3):
            run = self.start(self.definition((1,), f"分页-{number}"), request=str(number))
            self.complete(run["steps"][0]["execution_id"])
            workflows.reconcile(self.conn)
        history = workflows.list_runs(self.conn, status="success", page=2, page_size=1)
        self.assertEqual(history["total"], 3)
        self.assertEqual(len(history["items"]), 1)
        self.assertNotIn("steps", history["items"][0])
        self.assertEqual(workflows.list_runs(self.conn, status="")["total"], 3)
        self.assertEqual(workflows.list_runs(self.conn, date_to="2000-01-01T00:00:00+00:00")["total"], 0)

    def test_code_version_sequence_is_not_task_configuration_revision(self):
        self.conn.execute("UPDATE tasks SET version=91 WHERE id=1")
        first = self.start(self.definition((1,)))
        self.assertEqual(first["steps"][0]["task_version"], 1)
        workflows.request_stop(first["id"], "测试", self.conn)
        self.conn.execute("CREATE TABLE maintenance_policies(task_id INTEGER PRIMARY KEY,runtime TEXT,active_version_id INTEGER)")
        self.conn.execute("CREATE TABLE task_code_versions(id INTEGER PRIMARY KEY,task_id INTEGER,sequence INTEGER,source TEXT,requirements TEXT)")
        self.conn.execute("INSERT INTO task_code_versions VALUES(101,1,7,?,'')", ((self.apps / "1" / "main.py").read_text("utf-8"),))
        self.conn.execute("INSERT INTO maintenance_policies VALUES(1,'native',101)")
        second = self.start(self.definition((1,), "代码序号"))
        self.assertEqual(second["steps"][0]["task_version"], 7)
        workflows.request_stop(second["id"], "测试", self.conn)
        (self.apps / "1" / "main.py").write_text("print('unpublished-content')", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已启用版本"):
            self.start(self.definition((1,), "源码冲突"))

    def test_already_successful_step_rechecks_execution_and_cleanup(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.complete(execution)
        workflows.reconcile(self.conn)
        second = self.view(run["id"])["steps"][1]["execution_id"]
        self.complete(second)
        self.conn.execute("UPDATE executions SET status='failed' WHERE id=?", (execution,))
        workflows.reconcile(self.conn)
        self.assertEqual(self.count_executions(), 2)
        self.assertIn("前序", self.view(run["id"])["reason"])
        self.assertTrue(workflows.active_task_reference(self.conn, 3))

    def test_snapshot_timeout_rejects_bool_and_out_of_range(self):
        run = self.start(self.definition((1,)))
        execution = run["steps"][0]["execution_id"]
        step = self.conn.execute("SELECT snapshot FROM workflow_steps WHERE execution_id=?", (execution,)).fetchone()
        original = json.loads(step["snapshot"])
        for value in (True, 59, 86401, "600"):
            snapshot = dict(original, timeout_seconds=value)
            saved = workflows._json(snapshot)
            self.conn.execute("UPDATE workflow_steps SET snapshot=?,snapshot_digest=? WHERE execution_id=?", (saved, workflows._digest(saved.encode()), execution))
            with self.subTest(timeout=value), self.assertRaisesRegex(ValueError, "超时"):
                workflows.execution_snapshot(self.conn, execution)

    def test_path_links_checked_before_resolve_and_cleanup_rejects_parent(self):
        source = self.apps / "1" / "main.py"
        with patch.object(Path, "is_junction", autospec=True, side_effect=lambda path: path == source.parent):
            with self.assertRaisesRegex(ValueError, "目录联接"):
                workflows._managed_file(str(source), self.apps, 1024, "合成源码")
        with self.assertRaises(RuntimeError):
            workflows.remove_snapshot_directories([str(self.snapshots)])
        with self.assertRaises(ValueError):
            workflows.remove_snapshot_directories([str(self.root)])

    def test_candidate_aliases_explain_ineligible_tasks(self):
        self.conn.execute("UPDATE tasks SET enabled=0 WHERE id=2")
        candidates = workflows.list_task_candidates(self.conn)
        by_id = {item["id"]: item for item in candidates}
        self.assertTrue(by_id[1]["eligible"])
        self.assertEqual(by_id[1]["runtime"], "native")
        self.assertFalse(by_id[2]["eligible"])
        self.assertIn("停用", by_id[2]["unavailable_reason"])

    def test_pending_host_waiting_reason_reaches_flow_details_then_clears(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE executions SET error_message='等待宿主机空闲：合成占用' WHERE id=?", (execution,))
        workflows.reconcile(self.conn)
        view = self.view(run["id"])
        self.assertIn("宿主机空闲", view["reason"])
        self.assertIn("宿主机空闲", view["steps"][0]["error_summary"])
        self.conn.execute("UPDATE executions SET status='running',error_message='',started_at='now' WHERE id=?", (execution,))
        workflows.reconcile(self.conn)
        self.assertEqual(self.view(run["id"])["reason"], "")

    def test_browser_port_is_frozen_and_missing_or_invalid_port_is_explicit(self):
        run = self.start(self.definition((1,)))
        execution = run["steps"][0]["execution_id"]
        with patch.object(workflows, "MANAGED_BROWSER_PORT", 9222):
            self.assertEqual(workflows.execution_snapshot(self.conn, execution)["browser_port"], 9123)
        step = self.conn.execute("SELECT snapshot FROM workflow_steps WHERE execution_id=?", (execution,)).fetchone()
        original = json.loads(step["snapshot"])
        for value in (True, 1023, 65536, "9123", None):
            snapshot = dict(original)
            if value is None:
                snapshot.pop("browser_port")
            else:
                snapshot["browser_port"] = value
            saved = workflows._json(snapshot)
            self.conn.execute("UPDATE workflow_steps SET snapshot=?,snapshot_digest=? WHERE execution_id=?", (saved, workflows._digest(saved.encode()), execution))
            with self.subTest(port=value), self.assertRaisesRegex(ValueError, "浏览器端口"):
                workflows.execution_snapshot(self.conn, execution)

    def test_stop_actor_name_is_saved_without_claiming_admin_role(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE executions SET status='running',started_at='now' WHERE id=?", (execution,))
        first = workflows.request_stop(run["id"], 7, self.conn)
        self.assertEqual(first["reason"], "由 测试管理员 请求停止流程")
        second = workflows.request_stop(run["id"], "另一位成员", self.conn)
        self.assertEqual(second["reason"], first["reason"])

    def test_read_model_reflects_claimed_running_without_advancing_or_writing(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE workflow_runs SET reason='过期排队等待' WHERE id=?", (run["id"],))
        from app.services import execution_queue
        with patch.object(execution_queue, "transaction", self.transaction):
            self.assertEqual(execution_queue._claim_next_execution(), execution)
        claimed = self.view(run["id"])
        self.assertEqual(claimed["steps"][0]["status"], "running")
        self.assertIsNone(claimed["steps"][0]["started_at"])
        self.assertEqual(claimed["reason"], "")
        started = "2026-10-13T01:05:00+00:00"
        self.conn.execute("UPDATE executions SET status='running',started_at=?,error_message='' WHERE id=?", (started, execution))
        changes = self.conn.total_changes
        view = self.view(run["id"])
        compact = workflows.get_run(self.conn, run["id"], include_steps=False)
        self.assertEqual(view["status"], "running")
        self.assertEqual(view["steps"][0]["status"], "running")
        self.assertEqual(view["steps"][0]["started_at"], started)
        self.assertEqual(view["started_at"], started)
        self.assertEqual(compact["started_at"], started)
        self.assertEqual(view["reason"], "")
        self.assertEqual(self.conn.total_changes, changes)
        persisted = self.conn.execute("SELECT state,started_at FROM workflow_steps WHERE execution_id=?", (execution,)).fetchone()
        self.assertEqual(tuple(persisted), ("pending", None))
        persisted_run = self.conn.execute("SELECT reason,started_at FROM workflow_runs WHERE id=?", (run["id"],)).fetchone()
        self.assertEqual(tuple(persisted_run), ("过期排队等待", None))
        self.assertEqual(self.count_executions(), 1)

    def test_read_model_preserves_saved_stop_reason_with_running_execution(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.conn.execute("UPDATE workflow_runs SET stop_requested=1,state='stopping',reason='已持久停止原因' WHERE id=?", (run["id"],))
        self.conn.execute("UPDATE executions SET status='running',started_at='actual-start',error_message='' WHERE id=?", (execution,))
        changes = self.conn.total_changes
        view = self.view(run["id"])
        self.assertEqual(view["status"], "stopping")
        self.assertEqual(view["steps"][0]["status"], "stopping")
        self.assertEqual(view["started_at"], "actual-start")
        self.assertEqual(view["reason"], "已持久停止原因")
        self.assertEqual(self.conn.total_changes, changes)

    def test_read_model_terminal_execution_waits_for_cleanup_and_never_finishes_round(self):
        run = self.start()
        execution = run["steps"][0]["execution_id"]
        self.complete(execution, confirm=False)
        changes = self.conn.total_changes
        view = self.view(run["id"])
        self.assertEqual(view["steps"][0]["status"], "waiting")
        self.assertIn("确认", view["reason"])
        self.assertNotIn(view["status"], {"success", "failed", "cancelled"})
        self.assertEqual(self.count_executions(), 1)
        self.assertEqual(self.conn.total_changes, changes)
        self.conn.execute("UPDATE workflow_runs SET stop_requested=1,state='stopping',reason='停止持久原因' WHERE id=?", (run["id"],))
        stopped = self.view(run["id"])
        self.assertEqual(stopped["steps"][0]["status"], "stopping")
        self.assertEqual(stopped["reason"], "停止持久原因")
        native_process._update_conn(self.conn, execution, processes_gone=1, workspace_cleanup_confirmed=1, runner_returned=1)
        changes = self.conn.total_changes
        confirmed = self.view(run["id"])
        self.assertEqual(confirmed["steps"][0]["status"], "success")
        self.assertEqual(confirmed["status"], "stopping")
        self.assertEqual(confirmed["reason"], "停止持久原因")
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(self.count_executions(), 1)


if __name__ == "__main__":
    unittest.main()
