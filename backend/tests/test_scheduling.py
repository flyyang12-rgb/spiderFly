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

    def test_monthly_requires_an_integer_calendar_day_and_valid_time(self):
        for day in (None, 0, 32, True, False, 1.5, '1', 'LAST', 'last ', 'first', [], {}):
            with self.subTest(day=day), self.assertRaisesRegex(ValueError, '1 到 31'):
                normalize_trigger('monthly', {'day': day, 'time': '09:00'})
        for value in (None, '', '25:00', '09:70'):
            with self.subTest(time=value), self.assertRaises(ValueError):
                normalize_trigger('monthly', {'day': 1, 'time': value})
        self.assertEqual(normalize_trigger('monthly', {'day': 31, 'time': '09:05'}),
                         {'day': 31, 'time': '09:05'})
        self.assertEqual(normalize_trigger('monthly', {'day': 'last', 'time': '09:05'}),
                         {'day': 'last', 'time': '09:05'})

    def test_month_end_uses_actual_calendar_and_strictly_future_beijing_time(self):
        cases = [
            ('2027-01-31T01:00:00+00:00', '2027-02-28T01:00:00+00:00'),
            ('2028-01-31T01:00:00+00:00', '2028-02-29T01:00:00+00:00'),
            ('2027-02-28T01:00:00+00:00', '2027-03-31T01:00:00+00:00'),
            ('2027-03-31T01:00:00+00:00', '2027-04-30T01:00:00+00:00'),
            ('2026-12-31T01:00:00+00:00', '2027-01-31T01:00:00+00:00'),
            ('2027-02-28T08:59:59+08:00', '2027-02-28T01:00:00+00:00'),
            ('2027-02-27T20:00:00-05:00', '2027-03-31T01:00:00+00:00'),
        ]
        for after, expected in cases:
            with self.subTest(after=after):
                self.assertEqual(compute_next_run('monthly', {'day': 'last', 'time': '09:00'}, after), expected)
        self.assertEqual(compute_next_run('monthly', {'day': 'last', 'time': '00:00'},
            '2027-02-27T15:59:59+00:00'), '2027-02-27T16:00:00+00:00')

    def test_monthly_follows_beijing_calendar_and_is_strictly_future(self):
        cases = [
            (1, '2026-10-31T23:30:00+08:00', '2026-11-01T01:00:00+00:00'),
            (21, '2026-10-21T00:59:59+00:00', '2026-10-21T01:00:00+00:00'),
            (21, '2026-10-21T01:00:00+00:00', '2026-11-21T01:00:00+00:00'),
            (1, '2026-12-31T16:00:00+00:00', '2027-01-01T01:00:00+00:00'),
        ]
        for day, after, expected in cases:
            with self.subTest(after=after):
                self.assertEqual(compute_next_run('monthly', {'day': day, 'time': '09:00'}, after), expected)

    def test_monthly_skips_absent_dates_and_handles_leap_february(self):
        cases = [
            (31, '2027-01-31T01:00:00+00:00', '2027-03-31T01:00:00+00:00'),
            (31, '2027-03-31T01:00:00+00:00', '2027-05-31T01:00:00+00:00'),
            (29, '2028-02-01T00:00:00+00:00', '2028-02-29T01:00:00+00:00'),
            (29, '2027-02-01T00:00:00+00:00', '2027-03-29T01:00:00+00:00'),
        ]
        for day, after, expected in cases:
            with self.subTest(after=after):
                self.assertEqual(compute_next_run('monthly', {'day': day, 'time': '09:00'}, after), expected)


if __name__ == "__main__":
    unittest.main()
