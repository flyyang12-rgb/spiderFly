from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from app.cron_schedule import MAX_COUNT, MAX_EXPRESSION_LENGTH, cron_times, normalize_cron, previous_cron_time


UTC = timezone.utc


class CronScheduleTests(unittest.TestCase):
    def test_daily_preview_is_strict_and_in_utc(self):
        after = datetime(2026, 10, 13, 1, tzinfo=UTC)
        self.assertEqual(cron_times("0 9,14 * * *", after, 3), [
            datetime(2026, 10, 13, 6, tzinfo=UTC),
            datetime(2026, 10, 14, 1, tzinfo=UTC),
            datetime(2026, 10, 14, 6, tzinfo=UTC),
        ])

    def test_partial_minute_never_reuses_current_minute(self):
        for second, microsecond in ((0, 0), (1, 0), (0, 1), (59, 999999)):
            after = datetime(2026, 10, 13, 0, 0, second, microsecond, tzinfo=UTC)
            with self.subTest(second=second, microsecond=microsecond):
                self.assertEqual(cron_times("* * * * *", after, 1), [
                    datetime(2026, 10, 13, 0, 1, tzinfo=UTC),
                ])

    def test_timezone_independent_input(self):
        base = datetime(2026, 10, 13, tzinfo=UTC)
        expected = cron_times("0 9 * * *", base)
        for offset in (-7, 0, 8, 12):
            self.assertEqual(cron_times("0 9 * * *", base.astimezone(timezone(timedelta(hours=offset)))), expected)

    def test_weekday_names_and_numbers_are_equivalent(self):
        base = datetime(2026, 10, 13, tzinfo=UTC)
        expected = [datetime(2026, 10, 18, 1, tzinfo=UTC)]
        for weekday in ("0", "7", "SUN", "sun", "0,7", "SUN,7"):
            with self.subTest(weekday=weekday):
                self.assertEqual(cron_times(f"0 9 * * {weekday}", base, 1), expected)
                self.assertEqual(normalize_cron(f"0 9 * * {weekday}"), "0 9 * * 0")
        self.assertEqual(cron_times("0 9 * * MON", base, 1), [datetime(2026, 10, 19, 1, tzinfo=UTC)])

    def test_presets_and_range_steps(self):
        expression = "*/30 9-18 * * MON-FRI"
        self.assertEqual(normalize_cron(expression), "0,30 9,10,11,12,13,14,15,16,17,18 * * 1,2,3,4,5")
        self.assertEqual(cron_times(expression, datetime(2026, 10, 16, 10, tzinfo=UTC), 3), [
            datetime(2026, 10, 16, 10, 30, tzinfo=UTC),
            datetime(2026, 10, 19, 1, tzinfo=UTC),
            datetime(2026, 10, 19, 1, 30, tzinfo=UTC),
        ])
        self.assertEqual(normalize_cron("5/20 0 1-31/2 * *"), "5,25,45 0 1,3,5,7,9,11,13,15,17,19,21,23,25,27,29,31 * *")

    def test_sunday_alias_is_normalized_after_range_and_step_expansion(self):
        self.assertEqual(normalize_cron("0 9 * * 5-7"), "0 9 * * 0,5,6")
        self.assertEqual(normalize_cron("0 9 * * 1-7/2"), "0 9 * * 0,1,3,5")
        self.assertEqual(normalize_cron("0 9 * * MON-SUN"), "0 9 * * *")
        self.assertEqual(normalize_cron("0 9 * * SUN-SAT"), "0 9 * * *")
        self.assertEqual(normalize_cron("0 9 * * FRI-SUN"), "0 9 * * 0,5,6")

    def test_effective_full_sets_are_not_restricted(self):
        for day in ("*", "*/1", "1-31", ",".join(map(str, range(1, 32)))):
            with self.subTest(day=day):
                self.assertEqual(normalize_cron(f"0 9 {day} * MON"), "0 9 * * 1")
        for weekday in ("*", "*/1", "0-6", "1-7", "0-7", "MON-SUN", "0,1,2,3,4,5,6,7"):
            with self.subTest(weekday=weekday):
                self.assertEqual(normalize_cron(f"0 9 1 * {weekday}"), "0 9 1 * *")

    def test_rejects_both_day_fields_restricted(self):
        for expression in ("0 9 1 * MON", "0 9 */2 * MON", "0 9 1-30 * 1-6"):
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, "日和星期同时受限"):
                normalize_cron(expression)

    def test_month_end_missing_dates_are_skipped(self):
        self.assertEqual(cron_times("0 9 31 * *", datetime(2026, 1, 31, 1, tzinfo=UTC), 3), [
            datetime(2026, 3, 31, 1, tzinfo=UTC),
            datetime(2026, 5, 31, 1, tzinfo=UTC),
            datetime(2026, 7, 31, 1, tzinfo=UTC),
        ])

    def test_previous_time_is_inclusive_and_minute_precise(self):
        exact = datetime(2026, 10, 13, 1, tzinfo=UTC)
        self.assertEqual(previous_cron_time("0 9 * * *", exact), exact)
        self.assertEqual(previous_cron_time("0 9 * * *", exact - timedelta(microseconds=1)),
                         datetime(2026, 10, 12, 1, tzinfo=UTC))
        self.assertEqual(previous_cron_time("* * * * *", exact + timedelta(seconds=37)), exact)

    def test_previous_time_coalesces_distant_minute_occurrences(self):
        at = datetime(2026, 10, 13, 1, 17, 47, tzinfo=UTC)
        self.assertEqual(previous_cron_time("*/15 9-18 * * MON-FRI", at),
                         datetime(2026, 10, 13, 1, 15, tzinfo=UTC))
        self.assertEqual(previous_cron_time("0 9 31 * *", datetime(2026, 10, 13, tzinfo=UTC)),
                         datetime(2026, 8, 31, 1, tzinfo=UTC))

    def test_previous_leap_day_handles_eight_year_century_gap(self):
        self.assertEqual(previous_cron_time("0 9 29 2 *", datetime(2104, 2, 29, tzinfo=UTC)),
                         datetime(2096, 2, 29, 1, tzinfo=UTC))
        exact = datetime(2104, 2, 29, 1, tzinfo=UTC)
        self.assertEqual(previous_cron_time("0 9 29 2 *", exact), exact)

    def test_leap_day_five_results_include_century_gap(self):
        self.assertEqual(cron_times("0 9 29 2 *", datetime(2096, 2, 29, 1, tzinfo=UTC)), [
            datetime(year, 2, 29, 1, tzinfo=UTC) for year in (2104, 2108, 2112, 2116, 2120)
        ])

    def test_impossible_calendars_are_rejected_but_valid_month_lists_survive(self):
        for expression in ("0 9 31 2 *", "0 9 30 2 *", "0 9 31 4,6,9,11 *"):
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, "不存在可运行日期"):
                normalize_cron(expression)
        self.assertEqual(cron_times("0 9 31 2,3 *", datetime(2026, 1, 1, tzinfo=UTC), 1), [
            datetime(2026, 3, 31, 1, tzinfo=UTC),
        ])

    def test_normalization_is_idempotent_and_preserves_times(self):
        base = datetime(2026, 10, 13, tzinfo=UTC)
        for expression in ("*/13 8-17/3 * 1,6,12 0,7", "  00 09 * * mon,fri  ", "0 9 29 2 */1"):
            with self.subTest(expression=expression):
                normalized = normalize_cron(expression)
                self.assertEqual(normalize_cron(normalized), normalized)
                self.assertEqual(cron_times(expression, base), cron_times(normalized, base))

    def test_forward_results_match_independent_short_minute_enumeration(self):
        beijing = timezone(timedelta(hours=8))
        base = datetime(2026, 10, 13, 0, 13, 17, tzinfo=UTC)
        cases = (
            ("*/17 9,14 * * MON-FRI", base, set(range(0, 60, 17)), {9, 14}, None, {1, 2, 3, 4, 5}),
            ("15 0,12 * * SUN", base, {15}, {0, 12}, None, {0}),
            ("2-47/9 10-14/2 * * 0,7", base, set(range(2, 48, 9)), {10, 12, 14}, None, {0}),
            ("0 9 31 * *", datetime(2026, 10, 30, tzinfo=UTC), {0}, {9}, {31}, set(range(7))),
        )
        for expression, after, minutes, hours, days, weekdays in cases:
            with self.subTest(expression=expression):
                expected = []
                cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
                limit = after + timedelta(days=8)
                count = 1 if days else 2
                while cursor < limit and len(expected) < count:
                    local = cursor.astimezone(beijing)
                    if (local.minute in minutes and local.hour in hours
                            and (days is None or local.day in days)
                            and local.isoweekday() % 7 in weekdays):
                        expected.append(cursor)
                    cursor += timedelta(minutes=1)
                self.assertEqual(len(expected), count)
                self.assertEqual(cron_times(expression, after, count), expected)
                for point in expected:
                    self.assertEqual(previous_cron_time(expression, point), point)

    def test_invalid_syntax_and_values_have_field_errors(self):
        examples = {
            "60 9 * * *": "分字段", "0 24 * * *": "时字段",
            "0 9 0 * *": "日字段", "0 9 * 13 *": "月字段",
            "0 9 * * 8": "星期字段", "0 9 * * FUNDAY": "星期字段",
            "*/0 9 * * *": "步长", "*/-1 9 * * *": "步长",
            "*/1/2 9 * * *": "步长", "0, 9 * * *": "列表",
            "0 9 * * FRI-MON": "回绕", "0 9 * * 6-0": "回绕",
            "0 9 L * *": "日字段", "0 9 1W * *": "日字段",
            "0 9 * * MON#2": "星期字段", "0 9 * JAN *": "月字段",
            "-1 9 * * *": "分字段", "0 9 * * ?": "星期字段",
        }
        for expression, message in examples.items():
            with self.subTest(expression=expression), self.assertRaisesRegex(ValueError, message):
                normalize_cron(expression)

    def test_input_and_search_limits(self):
        for expression in (None, "", "   ", "0 9 * *", "0 9 * * * *", "0 9 * * * * *", "@daily", "*" * (MAX_EXPRESSION_LENGTH + 1)):
            with self.subTest(expression=str(expression)[:30]), self.assertRaises(ValueError):
                normalize_cron(expression)
        for count in (0, -1, True, 1.5, MAX_COUNT + 1):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, "次数"):
                cron_times("* * * * *", datetime(2026, 1, 1, tzinfo=UTC), count)
        with self.assertRaisesRegex(ValueError, "时区"):
            cron_times("* * * * *", datetime(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, "时区"):
            previous_cron_time("* * * * *", datetime(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, "搜索上限"):
            cron_times("0 9 29 2 *", datetime(9997, 1, 1, tzinfo=UTC), 1)
        with self.assertRaisesRegex(ValueError, "搜索上限"):
            previous_cron_time("0 9 29 2 *", datetime(1, 1, 2, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
