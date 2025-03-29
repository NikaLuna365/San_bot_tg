# handlers/reminder.py
import logging
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

# Импорты проекта
# !!! ИЗМЕНЕНО: schedule_handlers больше не нужен !!!
from constants import State, REMINDER_TYPE_KEYBOARD, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD, SCHEDULE_DAYS_KEYBOARD
import db
import scheduler
from .common import exit_to_main

logger = logging.getLogger(__name__)

async def reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог настройки напоминаний."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} вошел в меню напоминаний.")
    await update.message.reply_text(
        "Здесь Вы можете настроить напоминание о ежедневном тесте или запланировать регулярную ретроспективу.",
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
            await update.message.reply_text("Для настройки напоминаний сначала нужно указать ваш часовой пояс.\nИспользуйте команду /set_timezone или кнопку 'Настроить часовой пояс'.", reply_markup=MAIN_MENU_KEYBOARD)
            return ConversationHandler.END
        else:
            await update.message.reply_text(f"Ваш часовой пояс: {user_tz_str}.\nВо сколько вам удобно проходить ежедневный тест? (формат ЧЧ:ММ)", reply_markup=CANCEL_KEYBOARD)
            return State.REMINDER_DAILY_TIME

    # --- ИЗМЕНЕНО: Обработка выбора "Запланированная ретроспектива" ---
    elif choice == "Запланированная ретроспектива":
        logger.info(f"Пользователь {user_id} выбрал настройку запланированной ретроспективы из меню Напоминание.")
        # Проверяем часовой пояс перед началом настройки расписания
        user_tz_str = await db.get_user_timezone(pool, user_id)
        if not user_tz_str:
            await update.message.reply_text(
                "Для планирования ретроспективы сначала нужно указать ваш часовой пояс.\n"
                "Используйте команду /set_timezone или кнопку 'Настроить часовой пояс'.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
            return ConversationHandler.END
        else:
            # Сохраняем таймзону в user_data ДЛЯ ДИАЛОГА ПЛАНИРОВАНИЯ
            context.user_data["schedule_timezone"] = user_tz_str
            await update.message.reply_text(
                f"Хорошо, давайте настроим расписание.\nВаш часовой пояс: {user_tz_str}.\n"
                "В какой день недели вы бы хотели проводить ретроспективу?",
                # Сразу отправляем клавиатуру выбора дня недели
                reply_markup=SCHEDULE_DAYS_KEYBOARD
            )
            # Завершаем диалог напоминаний и ОЖИДАЕМ, что следующий ввод пользователя
            # будет обработан хендлером schedule_conv в состоянии SCHEDULE_DAY_NEW
            return ConversationHandler.END

    elif choice == "Главное меню":
        return await exit_to_main(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.", reply_markup=REMINDER_TYPE_KEYBOARD)
        return State.REMINDER_CHOICE

# Функция reminder_set_daily_time остается без изменений
async def reminder_set_daily_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    target_time_str = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    if not pool: logger.error(f"DB pool not found for user {user_id}"); await update.message.reply_text("Ошибка БД.", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
    if target_time_str == "Главное меню": return await exit_to_main(update, context)
    try: target_local_time = time.fromisoformat(target_time_str)
    except ValueError: await update.message.reply_text("Неверный формат времени ЧЧ:ММ.", reply_markup=CANCEL_KEYBOARD); return State.REMINDER_DAILY_TIME
    user_tz_str = await db.get_user_timezone(pool, user_id)
    if not user_tz_str: logger.error(f"Не найден TZ для {user_id}"); await update.message.reply_text("Ошибка: не найден часовой пояс. Установите через /set_timezone.", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
    try: user_zone = ZoneInfo(user_tz_str)
    except ZoneInfoNotFoundError: logger.error(f"Неверный TZ '{user_tz_str}' для {user_id}"); await update.message.reply_text(f"Ошибка: ваш пояс '{user_tz_str}' некорректен. Установите заново.", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
    try: await db.upsert_daily_reminder_settings(pool, user_id, target_local_time, user_tz_str, active=True); logger.info(f"Настройки напоминания для {user_id} сохранены: {target_local_time} {user_tz_str}")
    except Exception as e: logger.exception(f"Ошибка сохранения напоминания для {user_id}:"); await update.message.reply_text("Ошибка сохранения настроек."); return ConversationHandler.END
    try: await scheduler.schedule_user_daily_reminder(context.application, user_id, target_local_time, user_zone); await update.message.reply_text(f"Напоминание установлено на {target_time_str} ({user_tz_str}).", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
    except Exception as e: await update.message.reply_text("Ошибка установки напоминания.", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
