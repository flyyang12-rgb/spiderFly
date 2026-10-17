from __future__ import annotations

import json
import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.services import cron_runs


class CronRoundTests(unittest.TestCase):
    """Run from the documented isolated backend copy; no task code is executed."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript("""
            CREATE TABLE tasks (id INTEGER PRIMARY KEY, name TEXT, enabled INTEGER,
                archived INTEGER, trigger_type TEXT, trigger_config TEXT, version INTEGER,
                target_host_id INTEGER, next_run_at TEXT, last_triggered_at TEXT, updated_at TEXT);
            CREATE TABLE executions (id INTEGER PRIMARY KEY, task_id INTEGER,
                status TEXT, maintenance_snapshot TEXT DEFAULT '');
            CREATE TABLE remote_runs (id INTEGER PRIMARY KEY, task_id INTEGER, status TEXT);
            CREATE TABLE maintenance_jobs (id INTEGER PRIMARY KEY, execution_id INTEGER,
                task_id INTEGER, status TEXT, rerun_execution_id INTEGER, ended_at TEXT);
            CREATE TABLE execution_settlements (execution_id INTEGER PRIMARY KEY, cleanup_confirmed INTEGER);
        """)
        cron_runs.ensure_schema(self.conn)
        self.conn.execute("""INSERT INTO tasks VALUES
            (1,'test',1,0,'cron','{"expression":"* * * * *"}',1,NULL,
             '2026-10-13T01:00:00+00:00',NULL,NULL)""")
        self.clock = "2026-10-13T01:00:00+00:00"
        self.addCleanup(patch.stopall)
        patch.object(cron_runs, "utc_now", side_effect=lambda: self.clock).start()
        patch.object(cron_runs, "_cleanup_confirmed", side_effect=self.confirmed).start()

    def task(self):
        return dict(self.conn.execute("SELECT * FROM tasks WHERE id=1").fetchone())

    def confirmed(self, conn, execution_id):
        row = conn.execute("SELECT cleanup_confirmed FROM execution_settlements WHERE execution_id=?", (execution_id,)).fetchone()
        return bool(row and row[0])

    def confirm(self, execution_id, value=1):
        self.conn.execute("INSERT OR REPLACE INTO execution_settlements VALUES (?,?)", (execution_id, value))

    def execution(self, round_id, status="pending", execution_id=10):
        self.conn.execute("INSERT INTO executions(id,task_id,status) VALUES (?,1,?)", (execution_id, status))
        cron_runs.attach_execution(self.conn, round_id, execution_id)
        return execution_id

    def manual(self, request_id="click", intent=None):
        return cron_runs.register_manual(self.conn, self.task(), 7, request_id, intent)

    def test_schema_preserves_enclosing_transaction_and_history_on_task_delete(self):
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        cron_runs.ensure_schema(self.conn)
        record = self.manual()
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertIsNone(cron_runs.find_manual(self.conn, 1, 7, "click"))
        record = self.manual()
        self.conn.execute("DELETE FROM tasks WHERE id=1")
        self.assertEqual(cron_runs.find_manual(self.conn, 1, 7, "click")["id"], record["id"])

    def test_retry_is_stable_after_terminal_disable_target_change_and_deletion(self):
        original = self.manual()
        self.execution(original["id"], "success")
        self.confirm(10)
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 1)
        self.conn.execute("UPDATE tasks SET enabled=0,target_host_id=9,trigger_type='daily' WHERE id=1")
        self.assertEqual(self.manual()["id"], original["id"])
        with self.assertRaises(HTTPException) as caught:
            self.manual(intent={"changed": True})
        self.assertEqual(caught.exception.status_code, 409)
        self.conn.execute("DELETE FROM tasks WHERE id=1")
        self.assertEqual(cron_runs.find_manual(self.conn, 1, 7, "click")["execution_id"], 10)

    def test_new_request_rejects_unfinished_round_and_scopes_users_without_wrong_return(self):
        self.manual()
        for request_id, user in (("second", 7), ("click", 8)):
            with self.assertRaises(HTTPException) as caught:
                cron_runs.register_manual(self.conn, self.task(), user, request_id)
            self.assertEqual(caught.exception.status_code, 409)
        self.assertIsNone(cron_runs.find_manual(self.conn, 1, 8, "click"))

    def test_latest_slot_is_merged_once_and_stale_scan_cannot_advance(self):
        at = "2026-10-13T01:05:15+00:00"
        task = self.task()
        record = cron_runs.register_due(1, at, task["next_run_at"], task["version"], conn=self.conn)
        self.assertEqual(record["scheduled_for"], "2026-10-13T01:05:00+00:00")
        self.assertEqual(record["coalesced_from"], task["next_run_at"])
        self.assertEqual(self.task()["next_run_at"], "2026-10-13T01:06:00+00:00")
        self.assertIsNone(cron_runs.register_due(1, at, task["next_run_at"], task["version"], conn=self.conn))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM cron_rounds").fetchone()[0], 1)
        self.assertEqual(json.loads(record["config"])["task_version"], 1)

    def test_saved_revision_or_disabled_plan_wins_against_old_scan(self):
        task = self.task()
        self.conn.execute("UPDATE tasks SET version=2 WHERE id=1")
        self.assertIsNone(cron_runs.register_due(1, self.clock, task["next_run_at"], 1, conn=self.conn))
        self.conn.execute("UPDATE tasks SET enabled=0 WHERE id=1")
        self.assertIsNone(cron_runs.register_due(1, self.clock, conn=self.conn))
        self.assertEqual(self.task()["next_run_at"], task["next_run_at"])

    def test_unfinished_round_skips_future_slots_without_new_business_runs(self):
        record = cron_runs.register_due(1, self.clock, conn=self.conn)
        self.execution(record["id"], "failed")
        skipped = cron_runs.register_due(1, "2026-10-13T01:10:00+00:00", conn=self.conn)
        self.assertEqual(skipped["state"], "skipped")
        self.assertIn("维护", skipped["reason"])
        self.assertEqual(skipped["coalesced_from"], "2026-10-13T01:01:00+00:00")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 1)

    def test_remote_active_and_unconfirmed_native_block_new_round(self):
        for status in cron_runs.REMOTE_ACTIVE:
            self.conn.execute("DELETE FROM remote_runs")
            self.conn.execute("INSERT INTO remote_runs VALUES (1,1,?)", (status,))
            with self.assertRaises(HTTPException):
                self.manual()
        self.conn.execute("DELETE FROM remote_runs")
        self.conn.execute("INSERT INTO executions(id,task_id,status) VALUES (10,1,'failed')")
        self.confirm(10, 0)
        with self.assertRaises(HTTPException):
            self.manual()
        self.confirm(10)
        self.assertEqual(self.manual()["state"], "pending")

    def test_dispatch_rolls_back_partial_enqueue_and_retries_same_round(self):
        record = self.manual()
        def broken(task_id, source, requested_by, conn, round_id):
            conn.execute("INSERT INTO executions(id,task_id,status) VALUES (10,1,'pending')")
            raise RuntimeError("temporary transport failure")
        self.assertEqual(cron_runs.dispatch_pending(broken, conn=self.conn), 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 0)
        self.assertEqual(cron_runs.active_round(self.conn, 1)["id"], record["id"])
        def enqueue(task_id, source, requested_by, conn, round_id):
            self.assertEqual((task_id, source, requested_by, round_id), (1, "manual", 7, record["id"]))
            return self.execution(round_id)
        self.assertEqual(cron_runs.dispatch_pending(enqueue, conn=self.conn), 1)
        self.assertEqual(cron_runs.dispatch_pending(enqueue, conn=self.conn), 0)
        self.assertEqual(cron_runs.active_round(self.conn, 1)["execution_id"], 10)

    def test_changed_remote_target_or_disabled_task_ends_pending_without_fallback(self):
        for assignment in ("enabled=0", "target_host_id=9"):
            with self.subTest(assignment=assignment):
                self.conn.execute("DELETE FROM cron_rounds")
                self.conn.execute("UPDATE tasks SET enabled=1,target_host_id=NULL WHERE id=1")
                record = self.manual()
                self.conn.execute("UPDATE tasks SET " + assignment + " WHERE id=1")
                self.assertEqual(cron_runs.dispatch_pending(lambda *args: self.fail("must not dispatch"), conn=self.conn), 0)
                self.assertEqual(cron_runs._get(self.conn, record["id"])["state"], "error")

    def test_permanent_dispatch_error_is_visible_and_request_retry_never_creates_a_new_run(self):
        record = self.manual()
        def invalid(*args):
            raise HTTPException(409, "冻结执行参数不可用")
        self.assertEqual(cron_runs.dispatch_pending(invalid, conn=self.conn), 0)
        final = self.manual()
        self.assertEqual(final["id"], record["id"])
        self.assertEqual((final["state"], final["reason"]), ("error", "冻结执行参数不可用"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM executions").fetchone()[0], 0)

    def test_missing_cleanup_and_live_maintenance_decision_never_release(self):
        record = self.manual()
        self.execution(record["id"], "failed")
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 0)
        self.confirm(10)
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 0)
        self.assertEqual(cron_runs.active_round(self.conn, 1)["maintenance_decision"], "pending")
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock, recover=True), 1)
        final = cron_runs._get(self.conn, record["id"])
        self.assertEqual(final["maintenance_decision"], "none")
        self.assertIn("未自动重发", final["reason"])

    def test_maintenance_rerun_waits_for_cleanup_and_drains_slots_before_release(self):
        record = self.manual()
        self.execution(record["id"], "failed")
        self.confirm(10)
        self.conn.execute("INSERT INTO maintenance_jobs VALUES (5,10,1,'pending',NULL,NULL)")
        cron_runs.decide_maintenance(10, conn=self.conn)
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 0)
        self.conn.execute("INSERT INTO executions(id,task_id,status) VALUES (11,1,'success')")
        self.conn.execute("UPDATE maintenance_jobs SET status='activated',rerun_execution_id=11,ended_at=? WHERE id=5", (self.clock,))
        self.confirm(11, 0)
        end = "2026-10-13T01:09:00+00:00"
        self.assertEqual(cron_runs.reconcile(self.conn, end), 0)
        self.confirm(11)
        self.assertEqual(cron_runs.reconcile(self.conn, end), 1)
        rows = list(self.conn.execute("SELECT * FROM cron_rounds ORDER BY id"))
        self.assertEqual([(r["state"], r["rerun_execution_id"]) for r in rows], [("settled", 11), ("skipped", None)])
        self.assertEqual(rows[1]["scheduled_for"], end)
        self.assertEqual(self.task()["next_run_at"], "2026-10-13T01:10:00+00:00")
        self.assertEqual(cron_runs.reconcile(self.conn, end), 0)
        self.assertIsNone(cron_runs.register_due(1, end, conn=self.conn))

    def test_interrupted_maintenance_closes_without_new_model_and_missing_records_keep_occupancy(self):
        record = self.manual()
        self.execution(record["id"], "failed")
        self.confirm(10)
        self.conn.execute("INSERT INTO maintenance_jobs VALUES (5,10,1,'interrupted',NULL,?)", (self.clock,))
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock, recover=True), 1)
        self.assertEqual(cron_runs._get(self.conn, record["id"])["maintenance_decision"], "entered")
        self.assertEqual(cron_runs._get(self.conn, record["id"])["final_status"], "failed")
        self.conn.execute("DELETE FROM executions")
        self.conn.execute("DELETE FROM execution_settlements")
        second = self.manual("second")
        self.execution(second["id"], "failed", 11)
        self.confirm(11)
        self.conn.execute("INSERT INTO maintenance_jobs VALUES (6,11,1,'ready',NULL,NULL)")
        cron_runs.decide_maintenance(11, conn=self.conn)
        self.conn.execute("DELETE FROM maintenance_jobs WHERE id=6")
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 0)
        self.assertIn("维护记录缺失", cron_runs.active_round(self.conn, 1)["reason"])
        self.conn.execute("DELETE FROM executions WHERE id=11")
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 0)
        self.assertIn("执行记录缺失", cron_runs.active_round(self.conn, 1)["reason"])

    def test_settlement_freezes_original_and_repair_results_before_history_is_deleted(self):
        for column in ("result_code", "result_message", "error_message", "ended_at", "stdout"):
            self.conn.execute(f"ALTER TABLE executions ADD COLUMN {column} TEXT")
        record = self.manual()
        self.execution(record["id"], "failed")
        self.conn.execute("UPDATE executions SET result_code='BROKEN',result_message='原执行失败',error_message=?,ended_at=?,stdout='private output' WHERE id=10",
                          ("error " * 300, self.clock))
        self.confirm(10)
        self.conn.execute("INSERT INTO maintenance_jobs VALUES (5,10,1,'pending',NULL,NULL)")
        cron_runs.decide_maintenance(10, conn=self.conn)
        self.conn.execute("INSERT INTO executions(id,task_id,status,result_code,result_message,ended_at) VALUES (11,1,'success','OK','重跑完成',?)", (self.clock,))
        self.conn.execute("UPDATE maintenance_jobs SET status='activated',rerun_execution_id=11,ended_at=? WHERE id=5", (self.clock,))
        self.confirm(11)
        self.assertEqual(cron_runs.reconcile(self.conn, self.clock), 1)
        frozen = cron_runs._get(self.conn, record["id"])
        self.assertEqual((frozen["execution_status"], frozen["rerun_status"], frozen["final_status"]), ("failed", "success", "success"))
        original, repair = json.loads(frozen["execution_result"]), json.loads(frozen["rerun_result"])
        self.assertEqual((original["result_code"], repair["result_code"]), ("BROKEN", "OK"))
        self.assertEqual(len(original["error_message"]), 1000)
        self.assertNotIn("stdout", original)
        self.assertIn("维护链路", frozen["result_summary"])
        self.conn.execute("UPDATE executions SET status='cancelled',result_code='CHANGED' WHERE id IN (10,11)")
        cron_runs.ensure_schema(self.conn)
        self.assertEqual(cron_runs._get(self.conn, record["id"]), frozen)
        for table in ("tasks", "executions", "maintenance_jobs"):
            self.conn.execute("DELETE FROM " + table)
        cron_runs.ensure_schema(self.conn)
        self.assertEqual(cron_runs.find_manual(self.conn, 1, 7, "click"), frozen)

    def test_existing_ledger_migrates_idempotently_and_backfills_only_known_final_facts(self):
        for column in ("enqueued_at", "execution_status", "rerun_status", "final_status", "execution_result", "rerun_result", "result_summary"):
            self.conn.execute("ALTER TABLE cron_rounds DROP COLUMN " + column)
        self.conn.execute("INSERT INTO executions(id,task_id,status) VALUES (10,1,'failed')")
        self.conn.execute("INSERT INTO executions(id,task_id,status) VALUES (30,1,'failed')")
        for execution_id, rerun_id in ((10, None), (20, None), (30, 31)):
            self.conn.execute("""INSERT INTO cron_rounds(task_id,task_name,source,config,schedule_revision,state,
                execution_id,rerun_execution_id,maintenance_decision,created_at,ended_at)
                VALUES (?,'old','schedule','{}',1,'settled',?,?,'none',?,?)""",
                (execution_id + (rerun_id or 0), execution_id, rerun_id, self.clock, self.clock))
        cron_runs.ensure_schema(self.conn)
        rows = [dict(item) for item in self.conn.execute("SELECT * FROM cron_rounds ORDER BY id")]
        self.assertEqual(rows[0]["final_status"], "failed")
        self.assertIsNone(rows[1]["final_status"])
        self.assertIn("未核实", rows[1]["result_summary"])
        self.assertEqual(rows[2]["execution_status"], "failed")
        self.assertIsNone(rows[2]["final_status"])
        columns = [item["name"] for item in self.conn.execute("PRAGMA table_info(cron_rounds)")]
        cron_runs.ensure_schema(self.conn)
        self.assertEqual([dict(item) for item in self.conn.execute("SELECT * FROM cron_rounds ORDER BY id")], rows)
        self.assertEqual([item["name"] for item in self.conn.execute("PRAGMA table_info(cron_rounds)")], columns)

    def test_database_uniqueness_and_missing_schema_compatibility(self):
        self.manual()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("""INSERT INTO cron_rounds(task_id,task_name,source,config,schedule_revision,state,created_at)
                VALUES (1,'duplicate','manual','{}',1,'pending','now')""")
        other = sqlite3.connect(":memory:")
        self.addCleanup(other.close)
        self.assertIsNone(cron_runs.active_round(other, 1))
        self.assertEqual(cron_runs.dispatch_pending(lambda *args: 0, conn=other), 0)
        self.assertEqual(cron_runs.reconcile(other), 0)


if __name__ == "__main__":
    unittest.main()
