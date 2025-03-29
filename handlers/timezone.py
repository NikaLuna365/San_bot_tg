# handlers/timezone.py (обновить функцию set_timezone_receive)
import logging
from zoneinfo import available_timezones, ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from constants import State, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db
# --- ИЗМЕНЕНО: Импорт utils ---
import utils
from .common import exit_to_main

logger = logging.getLogger(__name__)
AVAILABLE_TIMEZONES = set(available_timezones())

async def set_timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    # ... (код остается без изменений) ...
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_start")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    current_tz = await db.get_user_timezone(pool, user_id)

    logger.info(f"Пользователь {user_id} начал установку часового пояса.")

    message = "Пожалуйста, укажите ваш часовой пояс.\n\n"
    if current_tz:
        message += f"Ваш текущий пояс: <b>{current_tz}</b>\n"
    message += ("Вы можете найти свой пояс на <a href='https://en.wikipedia.org/wiki/List_of_tz_database_time_zones'>этой странице</a> "
                "(ищите значение 'TZ database name', например, <code>Europe/Moscow</code>) или ввести смещение UTC "
                "(например, <code>UTC+3</code>, <code>GMT-5</code>).\n\n" # --- ИЗМЕНЕНО: Добавлено пояснение про GMT/UTC ---
                "Введите название вашего часового пояса или смещение:")

    await update.message.reply_html(message, reply_markup=CANCEL_KEYBOARD, disable_web_page_preview=True)
    return State.SET_TIMEZONE_ASK


async def set_timezone_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает часовой пояс от пользователя, валидирует и сохраняет."""
    user_input_tz = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")

    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_receive")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    if user_input_tz == "Главное меню":
        current_tz = await db.get_user_timezone(pool, user_id)
        if current_tz:
             await update.message.reply_text(f"Ваш часовой пояс остался прежним: {current_tz}.", reply_markup=MAIN_MENU_KEYBOARD)
        else:
             await update.message.reply_text("Часовой пояс не установлен.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    # --- ИЗМЕНЕНО: Логика валидации ---
    final_tz_name = None
    # 1. Проверяем как каноническое имя (напр., Europe/Moscow)
    if user_input_tz in AVAILABLE_TIMEZONES:
        final_tz_name = user_input_tz
    else:
        # 2. Если не нашли, пробуем распознать как GMT/UTC смещение
        parsed_etc_name = utils.parse_gmt_offset(user_input_tz)
        if parsed_etc_name:
            final_tz_name = parsed_etc_name
        else:
            # 3. Последняя попытка - через ZoneInfo напрямую (на случай нестандартных имен)
            try:
                _ = ZoneInfo(user_input_tz)
                # ВАЖНО: Если пользователь ввел Etc/GMT+X, нужно сохранить именно это имя,
                # а не пытаться его снова парсить через parse_gmt_offset.
                # Поэтому проверяем еще раз после попытки ZoneInfo
                if user_input_tz.startswith("Etc/"):
                     final_tz_name = user_input_tz
                # Если это какое-то другое валидное, но не стандартное имя, тоже сохраняем
                elif final_tz_name is None: # Сохраняем только если не нашли через GMT/UTC
                     final_tz_name = user_input_tz
            except ZoneInfoNotFoundError:
                # Невалидное имя и не GMT/UTC формат
                pass # final_tz_name останется None

    # Если после всех проверок имя не найдено
    if final_tz_name is None:
        logger.warning(f"Пользователь {user_id} ввел неверный часовой пояс: {user_input_tz}")
        await update.message.reply_text(
            f"Часовой пояс '{user_input_tz}' не найден или указан некорректно. \n"
            "Пожалуйста, проверьте правильность написания (например, <code>Europe/Moscow</code>, <code>UTC+3</code>, <code>GMT-5</code>) и попробуйте снова, "
            "или нажмите 'Главное меню'.",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='HTML'
        )
        return State.SET_TIMEZONE_ASK

    # Сохранение валидного имени в БД
    try:
        await db.set_user_timezone(pool, user_id, final_tz_name)
        logger.info(f"Пользователь {user_id} установил часовой пояс: {final_tz_name} (введено: '{user_input_tz}')")
        await update.message.reply_text(
            f"✅ Ваш часовой пояс успешно установлен на: <b>{final_tz_name}</b>",
            reply_markup=MAIN_MENU_KEYBOARD,
            parse_mode='HTML'
        )
        # TODO: Перепланировать существующие задачи пользователя
        return ConversationHandler.END
    except Exception as e:
        logger.exception(f"Ошибка сохранения часового пояса {final_tz_name} для {user_id}:")
        await update.message.reply_text(
            "Произошла ошибка при сохранении часового пояса. Попробуйте позже.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END
