from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app import database, main


class ExecutionHistoryTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir) / "data"
            with (
                patch.object(database, "DATA_DIR", data_dir),
                patch.object(database, "DB_PATH", data_dir / "spiderfly.db"),
                patch.object(database, "RPA_APPS_DIR", data_dir / "apps"),
                patch.object(database, "RPA_ENVS_DIR", data_dir / "envs"),
            ):
                database.init_db()
                user_ids = {}
                for username, display_name in (("kaoya", "考拉"), ("xiaowang", "小王")):
                    user_ids[username] = database.execute(
                        """
                        INSERT INTO users (
                            username, display_name, password_hash, role, active,
                            must_change_password, created_at, updated_at
                        ) VALUES (?, ?, 'unused', 'operator', 1, 0, ?, ?)
                        """,
                        (username, display_name, "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"),
                    )
                task_ids = {}
                for name, owner in (("财务日报", "kaoya"), ("库存同步", "xiaowang")):
                    task_ids[name] = database.execute(
                        """
                        INSERT INTO tasks (
                            name, app_name, script_path, python_path, created_by,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, '', ?, ?, ?)
                        """,
                        (
                            name,
                            name,
                            str(data_dir / f"{name}.py"),
                            user_ids[owner],
                            "2026-09-01T00:00:00+00:00",
                            "2026-09-01T00:00:00+00:00",
                        ),
                    )

                inserted = []
                statuses = ("success", "failed", "timeout", "cancelled")
                for index in range(23):
                    task_name = "财务日报" if index % 2 == 0 else "库存同步"
                    requester = "kaoya" if index % 3 else "xiaowang"
                    day = index % 3 + 1
                    created_at = f"2026-09-{day:02d}T08:{index:02d}:00+00:00"
                    status = statuses[index % len(statuses)]
                    execution_id = database.execute(
                        """
                        INSERT INTO executions (
                            task_id, status, requested_by, created_at,
                            ended_at, duration_ms, notification_status
                        ) VALUES (?, ?, ?, ?, ?, 1000, 'sent')
                        """,
                        (task_ids[task_name], status, user_ids[requester], created_at, created_at),
                    )
                    inserted.append(
                        {
                            "id": execution_id,
                            "task_name": task_name,
                            "status": status,
                            "requester": requester,
                            "day": day,
                        }
                    )
                yield inserted

    @staticmethod
    def query(**overrides):
        values = {
            "task_name": "",
            "status": "",
            "requester": "",
            "date_from": None,
            "date_to": None,
            "page": 1,
            "page_size": 10,
            "user": {"id": 1},
        }
        values.update(overrides)
        return main.list_execution_history(**values)

    def test_paginates_ten_records_per_page(self):
        with self.fixture() as inserted:
            first = self.query(page=1)
            second = self.query(page=2)
            third = self.query(page=3)

            self.assertEqual(first["total"], 23)
            self.assertEqual(first["page_size"], 10)
            self.assertEqual(first["total_pages"], 3)
            self.assertEqual([item["id"] for item in first["items"]], [item["id"] for item in reversed(inserted[-10:])])
            self.assertEqual(len(second["items"]), 10)
            self.assertEqual(len(third["items"]), 3)

    def test_combines_task_status_requester_and_beijing_date_filters(self):
        with self.fixture() as inserted:
            result = self.query(
                task_name="财务",
                status="timeout",
                requester="考拉",
                date_from=date(2026, 9, 2),
                date_to=date(2026, 9, 2),
            )
            expected = [
                item for item in inserted
                if item["task_name"] == "财务日报"
                and item["status"] == "timeout"
                and item["requester"] == "kaoya"
                and item["day"] == 2
            ]
            self.assertEqual(result["total"], len(expected))
            self.assertEqual(
                [item["id"] for item in result["items"]],
                [item["id"] for item in reversed(expected)],
            )
            self.assertTrue(all(item["requested_by_name"] == "考拉" for item in result["items"]))

    def test_keeps_running_and_queued_records_before_completed_history(self):
        with self.fixture():
            task_ids = [
                item["id"] for item in database.fetch_all("SELECT id FROM tasks ORDER BY id")
            ]
            user_id = database.fetch_one("SELECT id FROM users WHERE username = 'kaoya'")["id"]
            pending_id = database.execute(
                """
                INSERT INTO executions (task_id, status, requested_by, created_at)
                VALUES (?, 'pending', ?, '2026-09-05T00:00:00+00:00')
                """,
                (task_ids[0], user_id),
            )
            running_id = database.execute(
                """
                INSERT INTO executions (task_id, status, requested_by, created_at)
                VALUES (?, 'running', ?, '2026-09-05T00:01:00+00:00')
                """,
                (task_ids[1], user_id),
            )

            result = self.query()
            self.assertEqual(
                [(item["id"], item["status"]) for item in result["items"][:2]],
                [(running_id, "running"), (pending_id, "pending")],
            )

    def test_rejects_invalid_status_and_reversed_date_range(self):
        with self.fixture():
            with self.assertRaises(HTTPException) as status_error:
                self.query(status="unknown")
            self.assertEqual(status_error.exception.status_code, 400)

            with self.assertRaises(HTTPException) as date_error:
                self.query(date_from=date(2026, 9, 3), date_to=date(2026, 9, 2))
            self.assertEqual(date_error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
