"""Pure five-field Cron calculations for fixed Beijing time.

There are no project configuration or database imports. Weekdays use 0/7 for
Sunday, and a candidate must be strictly later than the supplied aware time.
Date searches jump over unselected months and invalid days; each next result
has an eight-calendar-year horizon, including the 2096 to 2104 leap-day gap.
"""

from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
import re


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
MAX_EXPRESSION_LENGTH = 1024
MAX_SEARCH_YEARS = 8
MAX_COUNT = 10_080
_WEEKDAYS = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
_FIELDS = (("分", 0, 59), ("时", 0, 23), ("日", 1, 31), ("月", 1, 12), ("星期", 0, 7))


@dataclass(frozen=True)
class _Schedule:
    days: tuple[int, ...]
    months: tuple[int, ...]
    weekdays: frozenset[int]
    minutes_of_day: tuple[int, ...]
    normalized: str


def _number(token: str, label: str, minimum: int, maximum: int) -> int:
    if label == "星期" and token in _WEEKDAYS:
        return _WEEKDAYS[token]
    if not re.fullmatch(r"[0-9]+", token):
        names = "或 MON—SUN 名称" if label == "星期" else ""
        raise ValueError(f"Cron {label}字段应使用整数{names}；不支持 L/W/# 等扩展")
    number = int(token)
    if not minimum <= number <= maximum:
        raise ValueError(f"Cron {label}字段必须在 {minimum} 到 {maximum} 之间")
    return number


def _field(source: str, label: str, minimum: int, maximum: int) -> tuple[int, ...]:
    values: set[int] = set()
    for part in source.upper().split(","):
        if not part:
            raise ValueError(f"Cron {label}字段列表不能为空")
        if part.count("/") > 1:
            raise ValueError(f"Cron {label}字段步长格式错误")
        base, separator, raw_step = part.partition("/")
        step = 1
        if separator:
            if not re.fullmatch(r"[0-9]+", raw_step) or int(raw_step) < 1:
                raise ValueError(f"Cron {label}字段步长必须是正整数")
            step = int(raw_step)
        if base == "*":
            first, last = minimum, maximum
        elif "-" in base:
            if base.count("-") != 1:
                raise ValueError(f"Cron {label}字段范围格式错误")
            left, right = base.split("-")
            first = _number(left, label, minimum, maximum)
            last = _number(right, label, minimum, maximum)
            # SUN may be the final Sunday in MON-SUN or FRI-SUN. This does not
            # introduce wrapped ranges such as FRI-MON or numeric 6-0.
            if label == "星期" and right == "SUN" and first > last:
                last = 7
            if first > last:
                raise ValueError(f"Cron {label}字段范围必须从小到大，不支持回绕范围")
        else:
            first = _number(base, label, minimum, maximum)
            last = maximum if separator else first
        selected = range(first, last + 1, step)
        values.update(value % 7 if label == "星期" else value for value in selected)
    return tuple(sorted(values))


def _expression(expression: str) -> str:
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Cron 表达式不能为空")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f"Cron 表达式不能超过 {MAX_EXPRESSION_LENGTH} 个字符")
    expression = expression.strip()
    if expression.startswith("@"):
        raise ValueError("Cron 不支持宏，请输入五字段表达式")
    return expression


@lru_cache(maxsize=512)
def _parse(expression: str) -> _Schedule:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("Cron 必须是五字段：分、时、日、月、星期；不支持秒或年")
    fields = tuple(_field(part, *spec) for part, spec in zip(parts, _FIELDS))
    minutes, hours, days, months, weekdays = fields
    if len(days) != 31 and len(weekdays) != 7:
        raise ValueError("Cron 首版不支持日和星期同时受限，请只限制其中一项")
    # The year 2000 is a leap year: this rejects calendars that are impossible
    # in every year, without mistakenly rejecting February 29.
    if not any(day <= monthrange(2000, month)[1] for month in months for day in days):
        raise ValueError("Cron 日与月组合不存在可运行日期")
    normalized = []
    for values, (label, minimum, maximum) in zip(fields, _FIELDS):
        size = 7 if label == "星期" else maximum - minimum + 1
        normalized.append("*" if len(values) == size else ",".join(map(str, values)))
    return _Schedule(
        days=days,
        months=months,
        weekdays=frozenset(weekdays),
        minutes_of_day=tuple(hour * 60 + minute for hour in hours for minute in minutes),
        normalized=" ".join(normalized),
    )


def normalize_cron(expression: str) -> str:
    """Validate and return a canonical five-field expression.

    Lists, ranges and steps expand to sorted effective sets; complete fields
    normalize to ``*``. Sunday aliases normalize to 0 before checking whether
    day-of-month and weekday are both restricted.
    """
    return _parse(_expression(expression)).normalized


def _search_end(after: datetime) -> date:
    year = after.year + MAX_SEARCH_YEARS
    if year > 9999:
        return date.max
    day = min(after.day, monthrange(year, after.month)[1])
    return date(year, after.month, day)


def _search_start(at: datetime) -> date:
    year = at.year - MAX_SEARCH_YEARS
    if year < 1:
        return date.min
    day = min(at.day, monthrange(year, at.month)[1])
    return date(year, at.month, day)


def _next(schedule: _Schedule, after: datetime) -> datetime:
    start = after.date()
    end = _search_end(after)
    for year in range(start.year, end.year + 1):
        for month in schedule.months:
            if (year, month) < (start.year, start.month):
                continue
            if (year, month) > (end.year, end.month):
                break
            last_day = monthrange(year, month)[1]
            for day in schedule.days:
                if day > last_day:
                    break
                candidate_day = date(year, month, day)
                if candidate_day < start:
                    continue
                if candidate_day > end:
                    break
                if candidate_day.isoweekday() % 7 not in schedule.weekdays:
                    continue
                position = 0
                if candidate_day == start:
                    position = bisect_right(schedule.minutes_of_day, after.hour * 60 + after.minute)
                if position < len(schedule.minutes_of_day):
                    minute = schedule.minutes_of_day[position]
                    return datetime.combine(candidate_day, time(minute // 60, minute % 60), CHINA_TZ)
    raise ValueError(f"Cron 在未来 {MAX_SEARCH_YEARS} 年搜索上限内没有可运行时间")


def cron_times(expression: str, after: datetime, count: int = 5) -> list[datetime]:
    """Return UTC-aware trigger times, strictly later than ``after``.

    The horizon applies separately to each next result, so a five-entry
    February 29 preview may extend beyond twenty years. Inputs and output
    size are bounded; no project runtime or local operating-system timezone
    is consulted.
    """
    schedule = _parse(_expression(expression))
    if not isinstance(after, datetime) or after.tzinfo is None or after.utcoffset() is None:
        raise ValueError("Cron 基准时刻必须包含时区")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        raise ValueError(f"Cron 次数必须是 1 到 {MAX_COUNT} 的整数")
    try:
        current = after.astimezone(CHINA_TZ)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Cron 基准时刻超出可计算日期范围") from exc
    result = []
    for _ in range(count):
        current = _next(schedule, current)
        result.append(current.astimezone(timezone.utc))
    return result


def previous_cron_time(expression: str, at: datetime) -> datetime:
    """Return the latest UTC-aware trigger at or before ``at``.

    This uses the same effective fields as preview and forward scheduling.
    Reverse date search lets downtime coalescing find one missed point without
    iterating all intervening minute occurrences.
    """
    schedule = _parse(_expression(expression))
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Cron 基准时刻必须包含时区")
    try:
        local = at.astimezone(CHINA_TZ)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Cron 基准时刻超出可计算日期范围") from exc
    start = _search_start(local)
    end = local.date()
    for year in range(end.year, start.year - 1, -1):
        for month in reversed(schedule.months):
            if (year, month) > (end.year, end.month):
                continue
            if (year, month) < (start.year, start.month):
                break
            last_day = monthrange(year, month)[1]
            for day in reversed(schedule.days):
                if day > last_day:
                    continue
                candidate_day = date(year, month, day)
                if candidate_day > end:
                    continue
                if candidate_day < start:
                    break
                if candidate_day.isoweekday() % 7 not in schedule.weekdays:
                    continue
                position = len(schedule.minutes_of_day) - 1
                if candidate_day == end:
                    position = bisect_right(schedule.minutes_of_day, local.hour * 60 + local.minute) - 1
                if position >= 0:
                    minute = schedule.minutes_of_day[position]
                    candidate = datetime.combine(candidate_day, time(minute // 60, minute % 60), CHINA_TZ)
                    try:
                        return candidate.astimezone(timezone.utc)
                    except (ValueError, OverflowError) as exc:
                        raise ValueError("Cron 结果超出可计算日期范围") from exc
    raise ValueError(f"Cron 在过去 {MAX_SEARCH_YEARS} 年搜索上限内没有可运行时间")
