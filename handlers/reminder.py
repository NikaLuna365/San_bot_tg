# handlers/reminder.py
import logging
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

# Импорты проекта
from constants import State, REMINDER_TYPE_KEYBOARD, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db
import scheduler
# --- УБРАНО: Импорт schedule_handlers ---
# import handlers.schedule as schedule_handlers

from .common import exit_to_main

logger = logging.getLogger(__name__)

async def reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог настройки напоминаний."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} вошел в меню напоминаний.")
    await update.message.reply_text(
        "О чем бы Вы хотели получать напоминания?",
        reply_markup=REMINDER_TYPE_KEYBOARD
    )
    return State.REMINDER_CHOICE

async def reminder_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Обрабатывает выбор типа напоминания."""
    choice = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")

    if not pool:
        logger.error(f"DB pool not found in context for user {user_id} in reminder_choice_handler")
        await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    if choice == "Ежедневный тест":
        logger.info(f"Пользователь {user_id} выбрал настройку ежедневного напоминания.")
        user_tz_str = await db.get_user_timezone(pool, user_id)
        if not user_tz_str:
            await update.message.reply_text(
                "Для настройки напоминаний сначала нужно указать ваш часовой пояс.\n"
                "Используйте команду /set_timezone или кнопку 'Настроить часовой пояс' в главном меню.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                f"Ваш часовой пояс: {user_tz_str}.\n"
                "Во сколько вам удобно проходить ежедневный тест? (укажите время в формате ЧЧ:ММ, например, 09:00 или 21:30)",
                reply_markup=CANCEL_KEYBOARD
            )
            return State.REMINDER_DAILY_TIME

    # --- ИЗМЕНЕНО: Убран прямой переход, дается инструкция ---
    elif choice == "Запланированная ретроспектива":
        logger.info(f"Пользователь {user_id} выбрал настройку напоминания о ретроспективе.")
        await update.message.reply_text(
            "Чтобы настроить или изменить расписание ретроспективы, пожалуйста, вернитесь в главное меню "
            "и выберите 'Ретроспектива', а затем 'Запланировать'.",
            # Можно добавить команду: "Или используйте команду /schedule.",
            reply_markup=MAIN_MENU_KEYBOARD # Сразу возвращаем в главное меню
        )
        return ConversationHandler.END # Завершаем диалог напоминаний

    elif choice == "Главное меню":
        return await exit_to_main(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите один из предложенных вариантов.",
            reply_markup=REMINDER_TYPE_KEYBOARD
        )
        return State.REMINDER_CHOICE

# Функция reminder_set_daily_time остается без изменений
async def reminder_set_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    # ... (код без изменений) ...
    target_time_str = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")

    if not pool:
        logger.error(f"DB pool not found in context for user {user_id} in reminder_set_daily_time")
        await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    if target_time_str == "Главное меню":
        return await exit_to_main(update, context)

    try:
        target_local_time = time.fromisoformat(target_time_str)
    except ValueError:
        await update.message.reply_text(
            "Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ (например, 08:00).",
            reply_markup=CANCEL_KEYBOARD
        )
        return State.REMINDER_DAILY_TIME

    user_tz_str = await db.get_user_timezone(pool, user_id)
    if not user_tz_str:
        logger.error(f"Не найден часовой пояс для {user_id} при установке напоминания.")
        await update.message.reply_text(
            "Произошла ошибка: не найден ваш часовой пояс. Пожалуйста, установите его через /set_timezone.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END

    try:
        user_zone = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError:
        logger.error(f"Неверный часовой пояс '{user_tz_str}' для {user_id} в БД.")
        await update.message.reply_text(
            f"Произошла ошибка: ваш сохраненный часовой пояс '{user_tz_str}' некорректен. Пожалуйста, установите его заново через /set_timezone.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END

    try:
        await db.upsert_daily_reminder_settings(pool, user_id, target_local_time, user_tz_str, active=True)
        logger.info(f"Настройки ежедневного напоминания для {user_id} сохранены: {target_local_time} {user_tz_str}")
    except Exception as e:
        logger.exception(f"Ошибка сохранения настроек напоминания для {user_id} в БД:")
        await update.message.reply_text("Произошла ошибка при сохранении настроек. Попробуйте позже.")
        return ConversationHandler.END

    try:
        await scheduler.schedule_user_daily_reminder(context.application, user_id, target_local_time, user_zone)
        await update.message.reply_text(
            f"Отлично! Ежедневное напоминание установлено на {target_time_str} по вашему времени ({user_tz_str}).",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(
            "Произошла ошибка при установке напоминания. Настройки сохранены, но напоминание может не работать. Попробуйте настроить позже.",
             reply_markup=MAIN_MENU_KEYBOARD
             )
        return ConversationHandler.END
