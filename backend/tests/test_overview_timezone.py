from __future__ import annotations

import unittest
from unittest.mock import patch

from app import database, main
from tests import test_app_api as api_helpers


class OverviewTimezoneTests(unittest.TestCase):
    def test_today_counts_use_beijing_calendar_day(self) -> None:
        helper = api_helpers.ManagedAppApiTests()
        with helper.fixture() as item:
            task = helper.create_task(item["app_id"], item["user"])
            records = (
                ("success", "2026-09-06T15:59:59+00:00"),
                ("success", "2026-09-06T16:00:00+00:00"),
                ("failed", "2026-09-07T00:00:00+00:00"),
                ("pending", "2026-09-07T01:00:00+00:00"),
                ("success", "2026-09-07T16:00:00+00:00"),
            )
            for status, created_at in records:
                database.execute(
                    "INSERT INTO executions(task_id, status, created_at) VALUES (?, ?, ?)",
                    (task["id"], status, created_at),
                )

            with patch.object(main, "utc_now", return_value="2026-09-07T01:30:00+00:00"):
                result = main.overview(item["user"])

            self.assertEqual(result["total_runs"], 3)
            self.assertEqual(result["success_runs"], 1)
            self.assertEqual(result["failed_runs"], 1)
            self.assertEqual(result["queued_runs"], 1)


if __name__ == "__main__":
    unittest.main()
