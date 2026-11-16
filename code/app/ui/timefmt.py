"""界面统一按北京时间显示数据库中的 UTC 时间。"""

from datetime import datetime, timedelta, timezone

CHINA_TIME = timezone(timedelta(hours=8))


def local_time(value: str) -> str:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(CHINA_TIME).strftime("%Y-%m-%d %H:%M")


def local_today() -> str:
    return datetime.now(CHINA_TIME).date().isoformat()
