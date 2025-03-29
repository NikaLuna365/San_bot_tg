# handlers/schedule.py
import logging
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

# Импорты проекта
from ..constants import (
    State, SCHEDULE_DAYS_KEYBOARD, SCHEDULE_MODE_KEYBOARD,
    CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
)
from .. import db
from .. import scheduler

from .common import exit_to_main

logger = logging.getLogger(__name__)

# Словарь для преобразования дней недели
DAYS_MAP_RU_TO_INT = {
    "пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6,
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6
}
DAYS_MAP_INT_TO_RU = {
    0: "Понедельник", 1: "Вторник", 2: "Среда", 3: "Четверг",
    4: "Пятница", 5: "Суббота", 6: "Воскресенье"
}

async def schedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог планирования ретроспективы."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} начал планирование ретроспективы.")

    # Проверка часового пояса
    pool = context.bot_data["db_pool"]
    user_tz_str = await db.get_user_timezone(pool, user_id)
    if not user_tz_str:
        await update.message.reply_text(
            "Для планирования ретроспективы сначала нужно указать ваш часовой пояс.\n"
            "Используйте команду /set_timezone или кнопку 'Настроить часовой пояс'.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END
    else:
        context.user_data["schedule_timezone"] = user_tz_str
        await update.message.reply_text(
            f"Ваш часовой пояс: {user_tz_str}.\n"
            "В какой день недели вы бы хотели проводить ретроспективу?",
            reply_markup=SCHEDULE_DAYS_KEYBOARD
        )
        return State.SCHEDULE_DAY_NEW

async def schedule_day_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает выбор дня недели."""
    day_input = update.message.text.strip().lower()
    user_id = update.effective_user.id

    if day_input == "главное меню":
        return await exit_to_main(update, context)

    scheduled_day = DAYS_MAP_RU_TO_INT.get(day_input)
    if scheduled_day is None:
        await update.message.reply_text(
            "Пожалуйста, выберите день недели с помощью кнопок.",
            reply_markup=SCHEDULE_DAYS_KEYBOARD
        )
        return State.SCHEDULE_DAY_NEW

    context.user_data["schedule_day"] = scheduled_day
    logger.debug(f"User {user_id} выбрал день для ретро: {scheduled_day}")

    await update.message.reply_text(
        f"Вы выбрали: {DAYS_MAP_INT_TO_RU[scheduled_day]}. "
        "Теперь укажите желаемое время проведения ретроспективы в этот день "
        "(в формате ЧЧ:ММ, например, 10:00 или 19:45).",
        reply_markup=CANCEL_KEYBOARD
    )
    # Переименовали состояние для ясности
    return State.SCHEDULE_TARGET_TIME # Сразу спрашиваем целевое время

# Убрали состояния SCHEDULE_CURRENT_TIME, т.к. часовой пояс уже известен

async def schedule_target_time_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ввод целевого времени ретроспективы."""
    target_time_str = update.message.text.strip()
    user_id = update.effective_user.id

    if target_time_str == "Главное меню":
        return await exit_to_main(update, context)

    try:
        target_local_time = time.fromisoformat(target_time_str)
    except ValueError:
        await update.message.reply_text(
            "Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 10:00).",
            reply_markup=CANCEL_KEYBOARD
        )
        return State.SCHEDULE_TARGET_TIME # Остаемся здесь же

    context.user_data["schedule_target_time"] = target_local_time
    logger.debug(f"User {user_id} выбрал время для ретро: {target_local_time}")

    await update.message.reply_text(
        "Выберите, как часто проводить ретроспективу:",
        reply_markup=SCHEDULE_MODE_KEYBOARD
    )
    return State.SCHEDULE_MODE

async def schedule_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор режима (еженедельно/двухнедельно), сохраняет и планирует."""
    mode_input = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data["db_pool"]

    if mode_input == "Главное меню":
        return await exit_to_main(update, context)

    if mode_input == "Еженедельная":
        mode = "weekly"
    elif mode_input == "Двухнедельная":
        mode = "biweekly"
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите 'Еженедельная' или 'Двухнедельная'.",
            reply_markup=SCHEDULE_MODE_KEYBOARD
        )
        return State.SCHEDULE_MODE

    logger.debug(f"User {user_id} выбрал режим ретро: {mode}")

    # Получаем собранные данные из user_data
    scheduled_day = context.user_data.get("schedule_day")
    target_local_time = context.user_data.get("schedule_target_time")
    user_tz_str = context.user_data.get("schedule_timezone")

    # Проверки на наличие данных (хотя они должны быть на этом этапе)
    if scheduled_day is None or target_local_time is None or user_tz_str is None:
        logger.error(f"Отсутствуют данные в user_data при завершении планирования для {user_id}")
        await update.message.reply_text(
            "Произошла внутренняя ошибка при сборе данных. Попробуйте начать сначала.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END

    try:
        user_zone = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        logger.error(f"Неверный часовой пояс '{user_tz_str}' для {user_id} в user_data.")
        await update.message.reply_text(
            f"Произошла ошибка: ваш сохраненный часовой пояс '{user_tz_str}' некорректен. Пожалуйста, установите его заново через /set_timezone и попробуйте снова.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END

    # --- Сохранение настроек в БД ---
    # TODO (DB Schema): Убедиться, что upsert_scheduled_retrospective_settings соответствует схеме
    try:
        await db.upsert_scheduled_retrospective_settings(
            pool, user_id, scheduled_day, target_local_time, user_tz_str, mode, active=True
        )
        logger.info(f"Настройки {mode} ретроспективы для {user_id} сохранены.")
    except Exception as e:
        logger.exception(f"Ошибка сохранения настроек ретроспективы для {user_id} в БД:")
        await update.message.reply_text("Произошла ошибка при сохранении настроек. Попробуйте позже.")
        return ConversationHandler.END

    # --- Планирование задачи через scheduler ---
    try:
        await scheduler.schedule_user_retrospective(
            context.application, user_id, scheduled_day, target_local_time, user_zone, mode
        )
        day_name = DAYS_MAP_INT_TO_RU[scheduled_day]
        time_str = target_local_time.strftime("%H:%M")
        freq_str = "каждую неделю" if mode == "weekly" else "каждые две недели"
        await update.message.reply_text(
            f"Отлично! Ретроспектива запланирована на {day_name}, {time_str} ({user_tz_str}), {freq_str}.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(
            "Произошла ошибка при установке ретроспективы. Настройки сохранены, но она может не работать.",
            reply_markup=MAIN_MENU_KEYBOARD
            )
        return ConversationHandler.END
