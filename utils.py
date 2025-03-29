# utils.py
from datetime import datetime, timezone, date
from calendar import monthrange

def get_now_utc() -> datetime:
    """Возвращает текущее время в UTC с информацией о часовом поясе."""
    return datetime.now(timezone.utc)

def get_today_utc() -> date:
    """Возвращает текущую дату в UTC."""
    return datetime.now(timezone.utc).date()

def remaining_days_in_month() -> int:
    """Возвращает количество оставшихся дней в текущем месяце."""
    today = get_now_utc().date()
    _, last_day = monthrange(today.year, today.month)
    return last_day - today.day

# Можно добавить другие утилиты, если понадобятся
