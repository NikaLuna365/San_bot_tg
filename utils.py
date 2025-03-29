# utils.py (добавить эту функцию)
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

def parse_gmt_offset(tz_string: str) -> Optional[str]:
    """Пытается распознать строку GMT/UTC смещения и вернуть каноническое имя Etc/GMT."""
    tz_string = tz_string.upper().replace(" ", "") # Нормализуем ввод
    # Ищем GMT+H, GMT-H, UTC+H, UTC-H (H - целое число от 0 до 14)
    match = re.fullmatch(r"(?:GMT|UTC)([+-])(\d{1,2})", tz_string)
    if match:
        sign = match.group(1)
        try:
            offset = int(match.group(2))
            if 0 <= offset <= 14:
                # ВАЖНО: Знак в Etc/GMT инвертирован! GMT-3 -> Etc/GMT+3
                etc_sign = "-" if sign == "+" else "+"
                # Формируем имя для zoneinfo (Etc/GMT без знака для 0)
                if offset == 0:
                    return "Etc/GMT" # Или можно "Etc/UTC"
                else:
                    etc_tz_name = f"Etc/GMT{etc_sign}{offset}"
                    # Проверяем, что такое имя существует
                    try:
                        _ = ZoneInfo(etc_tz_name)
                        return etc_tz_name
                    except ZoneInfoNotFoundError:
                        logger.warning(f"Сгенерированное имя Etc/GMT '{etc_tz_name}' не найдено.")
                        return None
            else:
                 # Смещение вне допустимого диапазона
                 return None
        except ValueError:
            # Ошибка конвертации числа
            return None
    return None # Не соответствует формату GMT/UTC смещения
