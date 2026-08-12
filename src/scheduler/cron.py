"""
cron 纯逻辑 + 调度串解析（无第三方依赖，跨平台）。

cron 解析、匹配和续算函数保持既有任务调度行为，
仅把 next_cron_time 的返回从 isoformat 字符串改为 datetime，便于上层直接比较与持久化。

调度串约定（与 TaskSpec.schedule 字段一致）：
- once@<ISO 时间>      例：once@2026-06-25T09:00
- cron@<5 段表达式>     例：cron@30 9 * * 1,3,5（分 时 日 月 周）
- 裸 5 段串（无前缀）按 cron 处理，例：0 8 * * *
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


def parse_cron_field(field: str, min_val: int, max_val: int) -> set:
    """解析单个 cron 字段，仅支持 *, */n, a, a-b, a,b,c 组合。"""
    values = set()
    for part in field.split(","):
        token = part.strip()
        if not token:
            continue
        if token == "*":
            values.update(range(min_val, max_val + 1))
            continue
        if token.startswith("*/"):
            step = int(token[2:])
            if step <= 0:
                raise ValueError("cron step 必须大于 0")
            values.update(range(min_val, max_val + 1, step))
            continue
        if "-" in token:
            start_s, end_s = token.split("-", 1)
            start_i, end_i = int(start_s), int(end_s)
            if start_i > end_i:
                raise ValueError("cron 范围非法")
            if start_i < min_val or end_i > max_val:
                raise ValueError("cron 取值超出范围")
            values.update(range(start_i, end_i + 1))
            continue
        one = int(token)
        if one < min_val or one > max_val:
            raise ValueError("cron 取值超出范围")
        values.add(one)
    if not values:
        raise ValueError("cron 字段不能为空")
    return values


def cron_matches(cron_expr: str, dt: datetime) -> bool:
    """判断给定时间是否匹配 5 段 cron 表达式。"""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        raise ValueError("cron 表达式必须是 5 段：分 时 日 月 周")
    minute_s, hour_s, day_s, month_s, weekday_s = fields
    minute_ok = dt.minute in parse_cron_field(minute_s, 0, 59)
    hour_ok = dt.hour in parse_cron_field(hour_s, 0, 23)
    day_ok = dt.day in parse_cron_field(day_s, 1, 31)
    month_ok = dt.month in parse_cron_field(month_s, 1, 12)
    # Python weekday: Monday=0..Sunday=6；cron 约定 Sunday=0/7
    py_wd = dt.weekday()
    cron_wd = 0 if py_wd == 6 else py_wd + 1
    weekday_vals = parse_cron_field(weekday_s.replace("7", "0"), 0, 6)
    weekday_ok = cron_wd in weekday_vals
    return minute_ok and hour_ok and day_ok and month_ok and weekday_ok


def next_cron_time(cron_expr: str, from_dt: Optional[datetime] = None) -> Optional[datetime]:
    """从 from_dt（默认现在）之后，找下一个匹配 cron 的整分钟时刻。最多向后探 1 年。"""
    base = (from_dt or datetime.now()).replace(second=0, microsecond=0)
    probe = base + timedelta(minutes=1)
    for _ in range(60 * 24 * 366):
        if cron_matches(cron_expr, probe):
            return probe
        probe += timedelta(minutes=1)
    return None


@dataclass(frozen=True)
class Schedule:
    """解析后的调度规格。"""
    trigger_type: str               # "once" | "cron" | "interval"
    run_at: Optional[datetime] = None   # once 的执行时刻
    cron_expr: Optional[str] = None     # cron 的 5 段表达式
    interval_seconds: Optional[int] = None  # interval 的间隔秒数


def parse_schedule(schedule: str) -> Schedule:
    """把 once@.../cron@.../every@<秒数>/裸 cron 串解析为 Schedule，非法则抛 ValueError。"""
    text = (schedule or "").strip()
    if not text:
        raise ValueError("调度串为空")

    prefix, sep, rest = text.partition("@")
    if sep:  # 带前缀
        kind = prefix.strip().lower()
        rest = rest.strip()
        if kind == "once":
            try:
                run_at = datetime.fromisoformat(rest)
            except Exception as e:
                raise ValueError(f"once 时间无法解析: {rest!r}（{e}）")
            if run_at.tzinfo is not None:
                run_at = run_at.astimezone().replace(tzinfo=None)
            return Schedule(trigger_type="once", run_at=run_at)
        if kind == "cron":
            cron_matches(rest, datetime.now())  # 校验合法性（非法抛 ValueError）
            return Schedule(trigger_type="cron", cron_expr=rest)
        if kind == "every":
            try:
                seconds = int(rest)
            except Exception as e:
                raise ValueError(f"interval 秒数无法解析: {rest!r}（{e}）")
            if seconds <= 0:
                raise ValueError("interval 秒数必须大于 0")
            return Schedule(trigger_type="interval", interval_seconds=seconds)
        raise ValueError(f"未知调度类型: {kind!r}（仅支持 once/cron/every）")

    # 无前缀：尝试当作裸 cron（5 段）
    cron_matches(text, datetime.now())  # 非法（如段数不对）会抛 ValueError
    return Schedule(trigger_type="cron", cron_expr=text)


def compute_next_run(
    schedule: Schedule,
    from_dt: Optional[datetime] = None,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Optional[datetime]:
    """计算下次执行时刻。

    once 在未来则返回该时刻、已过返回 None；cron 返回下一匹配时刻；
    interval 返回 from_dt + interval_seconds（首次从创建/续算基准时刻起算）。

    start_date/end_date（ISO 日期，可选）钳制生效区间：算出的时刻早于 start_date 则
    钳到当天零点；晚于 end_date（钳到当天 23:59:59）则视为无后续，返回 None（任务置 done）。
    """
    base = from_dt or datetime.now()
    if schedule.trigger_type == "once":
        next_run = schedule.run_at if (schedule.run_at and schedule.run_at > base) else None
    elif schedule.trigger_type == "cron" and schedule.cron_expr:
        next_run = next_cron_time(schedule.cron_expr, base)
    elif schedule.trigger_type == "interval" and schedule.interval_seconds:
        next_run = base + timedelta(seconds=schedule.interval_seconds)
    else:
        next_run = None

    if next_run is None:
        return None
    if start_date:
        start_dt = datetime.fromisoformat(start_date)
        if next_run < start_dt:
            next_run = start_dt
    if end_date:
        end_dt = datetime.fromisoformat(end_date).replace(hour=23, minute=59, second=59)
        if next_run > end_dt:
            return None
    return next_run
