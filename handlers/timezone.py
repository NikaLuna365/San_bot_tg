# handlers/timezone.py
import logging
import re # Добавляем модуль для регулярных выражений
from zoneinfo import available_timezones, ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

# Импорты проекта
from constants import State, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db
from .common import exit_to_main

logger = logging.getLogger(__name__)

# Получаем список доступных часовых поясов для валидации
AVAILABLE_TIMEZONES = set(available_timezones())

# --- ИЗМЕНЕНО: Регулярное выражение для парсинга GMT/UTC смещений ---
# Понимает GMT+3, UTC-06, GMT+5:30, UTC-10:00 и т.д.
TZ_OFFSET_PATTERN = re.compile(r"^(?:GMT|UTC)\s?([+-])(?:0?(\d{1,2}))(?::(00|30|45))?$", re.IGNORECASE)

def convert_offset_to_iana(offset_str: str) -> str | None:
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
        logger.warning(f"Дробные смещения GMT/UTC ('{offset_str}') пока не поддерживаются, используется ближайшее целое.")
        # Пока не поддерживаем Etc/GMT-5:30, используем только целые часы

    try:
        _ = ZoneInfo(iana_tz) # Проверяем, существует ли такой пояс
        return iana_tz
    except ZoneInfoNotFoundError:
        logger.error(f"Не удалось найти IANA зону для смещения: {iana_tz}")
        return None


async def set_timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Начинает диалог установки часового пояса."""
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_start")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    current_tz = await db.get_user_timezone(pool, user_id)

    logger.info(f"Пользователь {user_id} начал установку часового пояса.")

    # --- ИЗМЕНЕНО: Обновлен текст подсказки ---
    message = "Пожалуйста, укажите ваш часовой пояс.\n\n"
    if current_tz:
        message += f"Ваш текущий пояс: <b>{current_tz}</b>\n"
    message += ("Вы можете ввести его в одном из форматов:\n"
                "1. Название из базы данных IANA (например, <code>Europe/Moscow</code>, <code>Asia/Yekaterinburg</code>). Найти можно <a href='https://en.wikipedia.org/wiki/List_of_tz_database_time_zones'>здесь</a>.\n"
                "2. Смещение относительно GMT или UTC (например, <code>GMT+3</code>, <code>UTC-5</code>, <code>GMT-10</code>).\n\n"
                "Введите название или смещение:")

    await update.message.reply_html(message, reply_markup=CANCEL_KEYBOARD, disable_web_page_preview=True)
    return State.SET_TIMEZONE_ASK

async def set_timezone_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает часовой пояс от пользователя, валидирует и сохраняет."""
    user_input_tz = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    final_tz_name = None # Здесь будет валидное IANA имя

    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_receive")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END


    if user_input_tz == "Главное меню":
        # Если пользователь передумал, сообщаем, что пояс не изменен (если он был)
        current_tz = await db.get_user_timezone(pool, user_id)
        if current_tz:
             await update.message.reply_text(f"Ваш часовой пояс остался прежним: {current_tz}.", reply_markup=MAIN_MENU_KEYBOARD)
        else:
             await update.message.reply_text("Часовой пояс не установлен.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    # --- ИЗМЕНЕНО: Логика валидации и конвертации ---
    # 1. Пытаемся распознать как IANA имя
    if user_input_tz in AVAILABLE_TIMEZONES:
        final_tz_name = user_input_tz
    else:
        # 2. Если не IANA, пытаемся распознать как GMT/UTC смещение
        iana_from_offset = convert_offset_to_iana(user_input_tz)
        if iana_from_offset:
            final_tz_name = iana_from_offset
            logger.info(f"Смещение '{user_input_tz}' преобразовано в IANA: '{final_tz_name}' для user {user_id}")
        else:
            # 3. Если ни то, ни другое, возможно, это валидное IANA имя, но его нет в кэше
            try:
                _ = ZoneInfo(user_input_tz)
                # Если ZoneInfo не вызвал исключение, значит имя валидно
                final_tz_name = user_input_tz
            except ZoneInfoNotFoundError:
                # Имя точно невалидно
                logger.warning(f"Пользователь {user_id} ввел неверный часовой пояс: {user_input_tz}")
                await update.message.reply_text(
                    f"Часовой пояс '{user_input_tz}' не распознан. \n"
                    "Пожалуйста, проверьте правильность написания (например, <code>Europe/Moscow</code> или <code>GMT+3</code>) и попробуйте снова, "
                    "или нажмите 'Главное меню'.",
                    reply_markup=CANCEL_KEYBOARD,
                    parse_mode='HTML'
                )
                return State.SET_TIMEZONE_ASK # Остаемся в том же состоянии

    # Если мы здесь, final_tz_name содержит валидное IANA имя
    if final_tz_name:
        # Сохранение в БД
        try:
            await db.set_user_timezone(pool, user_id, final_tz_name)
            logger.info(f"Пользователь {user_id} установил часовой пояс: {final_tz_name}")
            await update.message.reply_text(
                f"✅ Ваш часовой пояс успешно установлен на: <b>{final_tz_name}</b>",
                reply_markup=MAIN_MENU_KEYBOARD,
                parse_mode='HTML'
            )
            # TODO: Перепланировать существующие задачи пользователя
            # await scheduler.reschedule_user_jobs(context.application, user_id)
            return ConversationHandler.END
        except Exception as e:
            logger.exception(f"Ошибка сохранения часового пояса {final_tz_name} для {user_id}:")
            await update.message.reply_text(
                "Произошла ошибка при сохранении часового пояса. Попробуйте позже.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
            return ConversationHandler.END
    else:
        # Сюда не должны попасть, если логика валидации верна, но на всякий случай
         logger.error(f"Не удалось определить валидный часовой пояс из ввода '{user_input_tz}' для user {user_id}")
         await update.message.reply_text(
             "Произошла внутренняя ошибка при обработке часового пояса. Попробуйте позже.",
             reply_markup=MAIN_MENU_KEYBOARD
         )
         return ConversationHandler.END
