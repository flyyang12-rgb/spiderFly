from __future__ import annotations

import asyncio
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import database, main, runner, scheduling
from app.execution_results import ResolvedOutcome
from app.schemas import TaskPatch
from tests import test_app_api as api_helpers


class ScheduledQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.helper = api_helpers.ManagedAppApiTests()

    def status(self, task_id: int) -> str:
        return database.fetch_one("SELECT last_status FROM tasks WHERE id = ?", (task_id,))["last_status"]

    def tick(self, now: str) -> None:
        with (
            patch.object(scheduling, "utc_now", return_value=now),
            patch.object(scheduling.asyncio, "sleep", new=AsyncMock(side_effect=asyncio.CancelledError)),
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(scheduling.scheduler_loop(main._enqueue_task))

    def test_due_schedule_runs_even_when_manual_execution_is_pending_or_running(self):
        for state in ("pending", "running"):
            with self.subTest(state=state), self.helper.fixture() as item:
                task = self.helper.create_task(item["app_id"], item["user"])
                database.execute(
                    """UPDATE tasks SET trigger_type = 'daily', trigger_config = '{"time":"09:00"}',
                    next_run_at = '2026-09-07T01:00:00+00:00' WHERE id = ?""",
                    (task["id"],),
                )
                manual_id = main._enqueue_task_sync(task["id"], "manual", item["user"]["id"])
                self.assertEqual(database.fetch_one("SELECT next_run_at FROM tasks WHERE id = ?", (task["id"],))["next_run_at"], "2026-09-07T01:00:00+00:00")
                if state == "running":
                    self.assertEqual(main._claim_next_execution(), manual_id)
                self.tick("2026-09-07T01:00:00+00:00")
                self.tick("2026-09-07T01:00:01+00:00")
                records = database.fetch_all("SELECT * FROM executions ORDER BY id")
                self.assertEqual([(r["trigger_source"], r["status"]) for r in records], [("manual", state), ("schedule", "pending")])
                self.assertEqual(records[0]["requested_by"], item["user"]["id"])
                self.assertIsNone(records[1]["requested_by"])
                self.assertEqual(self.status(task["id"]), state)
                self.assertEqual(database.fetch_one("SELECT next_run_at FROM tasks WHERE id = ?", (task["id"],))["next_run_at"], "2026-09-08T01:00:00+00:00")

    def test_manual_duplicate_protection_stays_in_place(self):
        for source in ("manual", "schedule"):
            with self.subTest(source=source), self.helper.fixture() as item:
                task = self.helper.create_task(item["app_id"], item["user"])
                main._enqueue_task_sync(task["id"], source)
                with self.assertRaises(HTTPException) as caught:
                    main._enqueue_task_sync(task["id"], "manual")
                self.assertEqual(caught.exception.status_code, 409)
                self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM executions")["n"], 1)

    def test_scheduled_occurrences_follow_manual_execution_in_fifo_order(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            ids = [main._enqueue_task_sync(task["id"], source) for source in ("manual", "schedule", "schedule")]
            for index, execution_id in enumerate(ids):
                self.assertEqual(main._claim_next_execution(), execution_id)
                self.assertEqual(database.fetch_one("SELECT COUNT(*) AS n FROM executions WHERE status = 'running'")["n"], 1)
                asyncio.run(runner._finalize(execution_id, task["id"], ResolvedOutcome("success", ""), 100, 0))
                self.assertEqual(self.status(task["id"]), "pending" if index < 2 else "success")
            self.assertIsNone(main._next_pending_execution_id())

    def test_database_still_rejects_simultaneous_running_executions_of_one_task(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            main._enqueue_task_sync(task["id"], "manual")
            second = main._enqueue_task_sync(task["id"], "schedule")
            main._claim_next_execution()
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute("UPDATE executions SET status = 'running' WHERE id = ?", (second,))

    def test_restart_preserves_each_pending_scheduled_occurrence(self):
        for state in ("pending", "running"):
            with self.subTest(state=state), self.helper.fixture() as item:
                task = self.helper.create_task(item["app_id"], item["user"])
                manual = main._enqueue_task_sync(task["id"], "manual")
                planned = [main._enqueue_task_sync(task["id"], "schedule") for _ in range(2)]
                if state == "running":
                    self.assertEqual(main._claim_next_execution(), manual)
                database.init_db()
                database.init_db()
                rows = database.fetch_all("SELECT id, status FROM executions ORDER BY id")
                self.assertEqual(rows, [{"id": manual, "status": "failed" if state == "running" else "pending"}] + [{"id": key, "status": "pending"} for key in planned])
                self.assertEqual(self.status(task["id"]), "pending")

    def test_old_database_index_is_migrated_without_losing_execution_rows(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            manual = main._enqueue_task_sync(task["id"], "manual")
            database.execute("CREATE UNIQUE INDEX uq_executions_one_active_task ON executions(task_id) WHERE status IN ('pending', 'running')")
            database.init_db()
            scheduled = main._enqueue_task_sync(task["id"], "schedule")
            self.assertEqual([r["id"] for r in database.fetch_all("SELECT id FROM executions ORDER BY id")], [manual, scheduled])
            self.assertIsNone(database.fetch_one("SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'uq_executions_one_active_task'"))

    def test_legacy_duplicate_manual_rows_are_merged_without_cancelling_schedules(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            manual = main._enqueue_task_sync(task["id"], "manual")
            scheduled = main._enqueue_task_sync(task["id"], "schedule")
            database.execute("DROP INDEX uq_executions_one_active_manual_task")
            duplicate = database.execute("INSERT INTO executions(task_id, status, created_at) VALUES(?, 'pending', ?)", (task["id"], database.utc_now()))
            database.init_db()
            self.assertEqual(database.fetch_all("SELECT id, status FROM executions ORDER BY id"), [{"id": manual, "status": "pending"}, {"id": scheduled, "status": "pending"}, {"id": duplicate, "status": "cancelled"}])

    def test_cancel_one_occurrence_preserves_other_pending_or_running_work(self):
        for state in ("pending", "running"):
            with self.subTest(state=state), self.helper.fixture() as item:
                task = self.helper.create_task(item["app_id"], item["user"])
                manual = main._enqueue_task_sync(task["id"], "manual")
                planned = main._enqueue_task_sync(task["id"], "schedule")
                if state == "running":
                    self.assertEqual(main._claim_next_execution(), manual)
                with patch.object(main, "write_audit"):
                    main.cancel_execution(planned, object(), item["user"])
                self.assertEqual(database.fetch_one("SELECT status FROM executions WHERE id = ?", (manual,))["status"], state)
                self.assertEqual(self.status(task["id"]), state)

    def test_disabling_cancels_all_pending_occurrences_and_keeps_current_run(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            manual = main._enqueue_task_sync(task["id"], "manual")
            planned = [main._enqueue_task_sync(task["id"], "schedule") for _ in range(2)]
            self.assertEqual(main._claim_next_execution(), manual)
            with patch.object(main, "write_audit"):
                main.update_task(task["id"], TaskPatch(enabled=False), object(), item["user"])
            self.assertEqual(database.fetch_all("SELECT id, status FROM executions ORDER BY id"), [{"id": manual, "status": "running"}] + [{"id": key, "status": "cancelled"} for key in planned])
            self.assertEqual(self.status(task["id"]), "running")
            with self.assertRaises(HTTPException):
                main._enqueue_task_sync(task["id"], "schedule")

    def test_worker_failure_keeps_later_schedule_waiting(self):
        with self.helper.fixture() as item:
            task = self.helper.create_task(item["app_id"], item["user"])
            manual = main._enqueue_task_sync(task["id"], "manual")
            planned = main._enqueue_task_sync(task["id"], "schedule")
            self.assertEqual(main._claim_next_execution(), manual)
            main._mark_execution_worker_failure(manual, "synthetic worker failure")
            self.assertEqual(self.status(task["id"]), "pending")
            self.assertEqual(main._claim_next_execution(), planned)


if __name__ == "__main__":
    unittest.main()
