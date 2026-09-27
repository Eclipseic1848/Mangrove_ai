"""产品时间固定为北京时间；存量无偏移时间不得猜测来源时区。"""
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8), name='Asia/Shanghai')


def now() -> datetime:
    return datetime.now(BEIJING)


def wall_time(value: datetime | None = None) -> datetime:
    """仅用于用户明确按北京时间定义的计划，不用于解释旧历史。"""
    value = value or now()
    return value.astimezone(BEIJING).replace(tzinfo=None) if value.tzinfo else value


def timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(BEIJING).isoformat() if parsed.tzinfo else value
