# handlers/timezone.py
import logging
from zoneinfo import available_timezones, ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

# --- ИЗМЕНЕНО: Абсолютный импорт ---
from constants import State, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db

# --- НЕ ИЗМЕНЕНО: Относительный импорт из той же папки ---
from .common import exit_to_main

logger = logging.getLogger(__name__)

# Получаем список доступных часовых поясов для валидации
AVAILABLE_TIMEZONES = set(available_timezones())

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

    message = "Пожалуйста, укажите ваш часовой пояс.\n\n"
    if current_tz:
        message += f"Ваш текущий пояс: <b>{current_tz}</b>\n"
    message += ("Вы можете найти свой пояс на <a href='https://en.wikipedia.org/wiki/List_of_tz_database_time_zones'>этой странице</a> "
                "(ищите значение в колонке 'TZ database name', например, <code>Europe/Moscow</code> или <code>Asia/Yekaterinburg</code>).\n\n"
                "Введите название вашего часового пояса:")

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
        # Если пользователь передумал, сообщаем, что пояс не изменен (если он был)
        current_tz = await db.get_user_timezone(pool, user_id)
        if current_tz:
             await update.message.reply_text(f"Ваш часовой пояс остался прежним: {current_tz}.", reply_markup=MAIN_MENU_KEYBOARD)
        else:
             await update.message.reply_text("Часовой пояс не установлен.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    # Валидация
    # Сначала проверяем по известному списку (быстрее)
    is_valid = user_input_tz in AVAILABLE_TIMEZONES
    if not is_valid:
        # Если нет в списке, проверяем через ZoneInfo (на случай неполного списка)
        try:
            _ = ZoneInfo(user_input_tz)
            is_valid = True
        except ZoneInfoNotFoundError:
             is_valid = False # Пояс точно не найден

    if not is_valid:
        logger.warning(f"Пользователь {user_id} ввел неверный часовой пояс: {user_input_tz}")
        await update.message.reply_text(
            f"Часовой пояс '{user_input_tz}' не найден. \n"
            "Пожалуйста, проверьте правильность написания (например, <code>Europe/Moscow</code>) и попробуйте снова, "
            "или нажмите 'Главное меню'.",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='HTML'
        )
        return State.SET_TIMEZONE_ASK # Остаемся в том же состоянии

    # Сохранение в БД
    try:
        await db.set_user_timezone(pool, user_id, user_input_tz)
        logger.info(f"Пользователь {user_id} установил часовой пояс: {user_input_tz}")
        await update.message.reply_text(
            f"✅ Ваш часовой пояс успешно установлен на: <b>{user_input_tz}</b>",
            reply_markup=MAIN_MENU_KEYBOARD,
            parse_mode='HTML'
        )
        # TODO: Перепланировать существующие задачи пользователя (напоминания, ретроспективы)
        # Нужно получить настройки из БД и вызвать scheduler.schedule_user...
        # Это более сложная логика, можно добавить позже.
        # await scheduler.reschedule_user_jobs(context.application, user_id)
        return ConversationHandler.END
    except Exception as e:
        logger.exception(f"Ошибка сохранения часового пояса {user_input_tz} для {user_id}:")
        await update.message.reply_text(
            "Произошла ошибка при сохранении часового пояса. Попробуйте позже.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END
