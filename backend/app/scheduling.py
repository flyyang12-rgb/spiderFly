from __future__ import annotations

import asyncio
import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable

from .database import execute, fetch_all, fetch_one, utc_now


CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
TRIGGER_TYPES = {"manual", "once", "interval", "daily", "weekly"}
INTERVAL_SECONDS = {
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}


def _aware(value: datetime | str | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.astimezone(timezone.utc)


def _clock(value: Any, field_name: str = "执行时间") -> time:
    try:
        return time.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name}格式应为 HH:MM") from exc


def normalize_trigger(trigger_type: str, config: dict[str, Any] | None) -> dict[str, Any]:
    trigger_type = (trigger_type or "manual").strip().lower()
    if trigger_type not in TRIGGER_TYPES:
        raise ValueError("不支持的触发方式")
    source = dict(config or {})

    if trigger_type == "manual":
        return {}
    if trigger_type == "once":
        run_at = str(source.get("run_at") or "").strip()
        if not run_at:
            raise ValueError("请选择单次执行时间")
        _aware(run_at)
        return {"run_at": run_at}
    if trigger_type == "interval":
        try:
            value = int(source.get("value", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("间隔数值必须是整数") from exc
        unit = str(source.get("unit") or "minutes")
        if value < 1 or value > 100000:
            raise ValueError("间隔数值必须在 1 到 100000 之间")
        if unit not in INTERVAL_SECONDS:
            raise ValueError("不支持的间隔单位")
        return {"value": value, "unit": unit}
    if trigger_type == "daily":
        clock = _clock(source.get("time"))
        return {"time": clock.strftime("%H:%M")}

    clock = _clock(source.get("time"))
    try:
        weekdays = sorted({int(item) for item in source.get("weekdays", [])})
    except (TypeError, ValueError) as exc:
        raise ValueError("每周执行日期格式错误") from exc
    if not weekdays or any(day < 1 or day > 7 for day in weekdays):
        raise ValueError("请至少选择一个星期")
    return {"weekdays": weekdays, "time": clock.strftime("%H:%M")}


def compute_next_run(
    trigger_type: str,
    config: dict[str, Any] | None,
    after: datetime | str | None = None,
) -> str | None:
    config = normalize_trigger(trigger_type, config)
    after_utc = _aware(after)
    local_after = after_utc.astimezone(CHINA_TZ)

    if trigger_type == "manual":
        return None
    if trigger_type == "once":
        candidate = _aware(config["run_at"])
        return candidate.isoformat(timespec="seconds") if candidate > after_utc else None
    if trigger_type == "interval":
        seconds = config["value"] * INTERVAL_SECONDS[config["unit"]]
        return (after_utc + timedelta(seconds=seconds)).isoformat(timespec="seconds")

    clock = _clock(config["time"])
    if trigger_type == "daily":
        candidate = datetime.combine(local_after.date(), clock, CHINA_TZ)
        if candidate <= local_after:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc).isoformat(timespec="seconds")

    weekdays = set(config["weekdays"])
    for offset in range(8):
        day = local_after.date() + timedelta(days=offset)
        if day.isoweekday() not in weekdays:
            continue
        candidate = datetime.combine(day, clock, CHINA_TZ)
        if candidate > local_after:
            return candidate.astimezone(timezone.utc).isoformat(timespec="seconds")
    raise ValueError("无法计算下一次执行时间")


def decode_trigger_config(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def encode_trigger_config(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def reconcile_schedules() -> None:
    for task in fetch_all("SELECT * FROM tasks WHERE enabled = 1 AND trigger_type != 'manual'"):
        if task.get("next_run_at"):
            continue
        config = decode_trigger_config(task.get("trigger_config"))
        next_run = compute_next_run(task["trigger_type"], config)
        execute("UPDATE tasks SET next_run_at = ? WHERE id = ?", (next_run, task["id"]))


async def scheduler_loop(enqueue: Callable[[int, str], Awaitable[int]]) -> None:
    while True:
        now = utc_now()
        due_tasks = await asyncio.to_thread(
            fetch_all,
            """
            SELECT * FROM tasks
            WHERE enabled = 1
              AND trigger_type != 'manual'
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now,),
        )
        for task in due_tasks:
            config = decode_trigger_config(task.get("trigger_config"))
            next_run = compute_next_run(task["trigger_type"], config, after=now)
            await asyncio.to_thread(
                execute,
                "UPDATE tasks SET next_run_at = ?, last_triggered_at = ?, updated_at = ? WHERE id = ?",
                (next_run, now, now, task["id"]),
            )
            try:
                await enqueue(task["id"], "schedule")
            except Exception:
                # 到点时若任务仍在运行，本轮跳过；下一次执行时间已经顺延。
                continue
        await asyncio.sleep(1)
