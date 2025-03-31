# utils.py
import logging # <--- ДОБАВЛЕН ЭТОТ ИМПОРТ
from datetime import datetime, timezone, date, time
from calendar import monthrange
from typing import Optional
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__) # Теперь logging известен

# Паттерн для GMT/UTC смещений
TZ_OFFSET_PATTERN = re.compile(r"^(?:GMT|UTC)\s?([+-])(?:0?(\d{1,2}))(?::(00|30|45))?$", re.IGNORECASE)

def parse_gmt_offset_to_iana(offset_str: str) -> Optional[str]:
    """
    Пытается преобразовать строку смещения (GMT+3, UTC-5:00) в IANA формат (Etc/GMT-3, Etc/GMT+5).
    Возвращает валидное IANA имя или None.
    """
    match = TZ_OFFSET_PATTERN.match(offset_str)
    if not match:
        return None

    sign = match.group(1)
    hours = int(match.group(2))
    minutes_str = match.group(3) # Может быть None '00', '30', '45'

    # Проверка допустимости часов (стандартные Etc/GMT* от -14 до +12)
    if not (0 <= hours <= 14):
        return None

    # Проверка минут: стандартные Etc/GMT* не поддерживают минуты
    if minutes_str and minutes_str != "00":
        logger.warning(f"Дробные смещения GMT/UTC ('{offset_str}') пока не поддерживаются напрямую через Etc/GMT*. Используется ближайшее целое.")
        # Игнорируем минуты для Etc/GMT*

    # Преобразование знака (Etc/GMT* использует ОБРАТНЫЙ знак)
    iana_sign = "-" if sign == "+" else "+"
    # Формирование IANA строки
    iana_tz = f"Etc/GMT{iana_sign}{hours}"

    try:
        # Проверяем, существует ли такая стандартная зона
        _ = ZoneInfo(iana_tz)
        return iana_tz
    except ZoneInfoNotFoundError:
        logger.error(f"Не удалось найти стандартную IANA зону для смещения: {iana_tz} (исходное: '{offset_str}')")
        return None
    except Exception as e: # Ловим другие возможные ошибки ZoneInfo
        logger.error(f"Ошибка при проверке ZoneInfo для {iana_tz}: {e}")
        return None


def get_now_utc() -> datetime:
    """Возвращает текущее время в UTC с информацией о часовом поясе."""
    return datetime.now(timezone.utc)

def get_today_utc() -> date:
    """Возвращает текущую дату в UTC."""
    return datetime.now(timezone.utc).date()

# Функция remaining_days_in_month остается без изменений
def remaining_days_in_month() -> int:
    """Возвращает количество оставшихся дней в текущем месяце."""
    today = get_now_utc().date() # Используем get_now_utc
    try:
        _, last_day = monthrange(today.year, today.month)
        return last_day - today.day
    except ValueError: # На случай некорректной даты (маловероятно)
        logger.error(f"Некорректная дата для monthrange: {today}")
        return 0
