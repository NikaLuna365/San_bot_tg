# utils.py
from datetime import datetime, timezone, date
from calendar import monthrange
# --- ДОБАВЛЕНО: Импорт Optional ---
from typing import Optional
import re # Импорт re, если parse_gmt_offset его использует

# --- ПРЕДПОЛОЖЕНИЕ: У вас есть эта функция, вызвавшая ошибку ---
# Если этой функции у вас нет, просто добавьте импорт Optional выше.
# Эта функция сама по себе не используется в коде, что я давал,
# но если она есть у вас, импорт Optional нужен.
# Я скопирую реализацию парсинга из handlers/timezone.py для примера,
# если вы хотите вынести это в utils.
TZ_OFFSET_PATTERN = re.compile(r"^(?:GMT|UTC)\s?([+-])(?:0?(\d{1,2}))(?::(00|30|45))?$", re.IGNORECASE)

def parse_gmt_offset_to_iana(offset_str: str) -> Optional[str]:
    """Пытается преобразовать строку смещения (GMT+3) в IANA формат (Etc/GMT-3)."""
    match = TZ_OFFSET_PATTERN.match(offset_str)
    if not match:
        return None

    sign = match.group(1)
    hours = int(match.group(2))
    minutes_str = match.group(3) # Может быть None

    # Проверка допустимости часов
    if not (0 <= hours <= 14): # Стандартные Etc/GMT* от -14 до +12
        return None

    # Преобразование знака (Etc/GMT* использует обратный знак)
    iana_sign = "-" if sign == "+" else "+"

    # Формирование IANA строки
    iana_tz = f"Etc/GMT{iana_sign}{hours}"

    # Проверка на существование (на всякий случай) и на дробные минуты
    if minutes_str and minutes_str != "00":
        # logger.warning(f"Дробные смещения GMT/UTC ('{offset_str}') пока не поддерживаются, используется ближайшее целое.")
        # Пока не поддерживаем Etc/GMT-5:30, используем только целые часы
        pass # Можно просто игнорировать минуты для Etc/GMT*

    try:
        # Проверяем через ZoneInfo, чтобы убедиться, что такая зона валидна
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError # Локальный импорт
        _ = ZoneInfo(iana_tz)
        return iana_tz
    except (NameError, ImportError, ZoneInfoNotFoundError): # Обработка ошибки импорта или ненайденной зоны
        # logger.error(f"Не удалось найти IANA зону для смещения: {iana_tz}")
        return None
# --- КОНЕЦ ПРЕДПОЛАГАЕМОЙ ФУНКЦИИ ---


def get_now_utc() -> datetime:
    """Возвращает текущее время в UTC с информацией о часовом поясе."""
    return datetime.now(timezone.utc)

def get_today_utc() -> date:
    """Возвращает текущую дату в UTC."""
    return datetime.now(timezone.utc).date()

def remaining_days_in_month() -> int:
    """Возвращает количество оставшихся дней в текущем месяце."""
    today = get_now_utc().date() # Используем get_now_utc
    try:
        _, last_day = monthrange(today.year, today.month)
        return last_day - today.day
    except ValueError: # На случай некорректной даты (маловероятно)
        return 0
