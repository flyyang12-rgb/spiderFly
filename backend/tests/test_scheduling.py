from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.scheduling import compute_next_run, normalize_trigger, shanghai_day_utc_bounds


class SchedulingTests(unittest.TestCase):
    def test_beijing_day_bounds_are_expressed_in_utc(self) -> None:
        self.assertEqual(
            shanghai_day_utc_bounds(
                datetime(2026, 9, 6, 16, 30, tzinfo=timezone.utc)
            ),
            ("2026-09-06T16:00:00+00:00", "2026-09-07T16:00:00+00:00"),
        )

    def test_interval_uses_requested_distance(self) -> None:
        result = compute_next_run(
            "interval",
            {"value": 30, "unit": "minutes"},
            after=datetime(2026, 8, 27, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result, "2026-08-27T00:30:00+00:00")

    def test_daily_rolls_to_tomorrow_after_the_clock_time(self) -> None:
        result = compute_next_run(
            "daily",
            {"time": "09:00"},
            after=datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result, "2026-08-28T01:00:00+00:00")

    def test_weekly_finds_the_next_selected_weekday(self) -> None:
        result = compute_next_run(
            "weekly",
            {"weekdays": [1, 5], "time": "09:00"},
            after=datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result, "2026-08-28T01:00:00+00:00")

    def test_once_interprets_browser_time_as_china_time(self) -> None:
        result = compute_next_run(
            "once",
            {"run_at": "2026-08-27T18:30"},
            after=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(result, "2026-08-27T10:30:00+00:00")

    def test_weekly_requires_at_least_one_day(self) -> None:
        with self.assertRaisesRegex(ValueError, "至少选择一个星期"):
            normalize_trigger("weekly", {"weekdays": [], "time": "09:00"})


if __name__ == "__main__":
    unittest.main()
