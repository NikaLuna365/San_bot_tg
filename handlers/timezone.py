# handlers/timezone.py
import logging
# import re # Больше не нужен здесь
from zoneinfo import available_timezones, ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

# Импорты проекта
from constants import State, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db
import scheduler # Добавлен импорт scheduler
from utils import parse_gmt_offset_to_iana # Импортируем из utils
from .common import exit_to_main

logger = logging.getLogger(__name__)

# Получаем список доступных часовых поясов для валидации
AVAILABLE_TIMEZONES = set(available_timezones())

# --- Удалена локальная функция convert_offset_to_iana ---

async def set_timezone_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Начинает диалог установки часового пояса."""
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_start")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    current_tz = await db.get_user_timezone(pool, user_id)
    logger.info(f"Пользователь {user_id} начал установку часового пояса. Текущий: {current_tz}")

    message = "Пожалуйста, укажите ваш часовой пояс.\n\n"
    if current_tz:
        message += f"Ваш текущий пояс: <b>{current_tz}</b>\n"
    message += ("Вы можете ввести его в одном из форматов:\n"
                "1. Название из базы данных IANA (например, <code>Europe/Moscow</code>, <code>Asia/Yekaterinburg</code>). Найти можно <a href='https://en.wikipedia.org/wiki/List_of_tz_database_time_zones'>здесь</a>.\n"
                "2. Смещение относительно GMT или UTC (например, <code>GMT+3</code>, <code>UTC-5</code>, <code>GMT-10:00</code>).\n\n"
                "Введите название или смещение:")

    await update.message.reply_html(message, reply_markup=CANCEL_KEYBOARD, disable_web_page_preview=True)
    return State.SET_TIMEZONE_ASK

async def set_timezone_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает часовой пояс от пользователя, валидирует, сохраняет и перепланирует задачи."""
    user_input_tz = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")
    final_tz_name = None

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

    # Валидация и конвертация
    # 1. Пробуем как IANA
    if user_input_tz in AVAILABLE_TIMEZONES:
        final_tz_name = user_input_tz
    else:
        # 2. Пробуем как GMT/UTC смещение, используя функцию из utils
        iana_from_offset = parse_gmt_offset_to_iana(user_input_tz)
        if iana_from_offset:
            final_tz_name = iana_from_offset
            logger.info(f"Смещение '{user_input_tz}' преобразовано в IANA: '{final_tz_name}' для user {user_id}")
        else:
            # 3. Пробуем как возможное, но отсутствующее в кеше IANA имя
            try:
                _ = ZoneInfo(user_input_tz)
                # Если ZoneInfo не вызвал исключение, имя валидно
                final_tz_name = user_input_tz
                # Добавляем в кеш для будущих проверок (опционально, но может ускорить)
                AVAILABLE_TIMEZONES.add(final_tz_name)
                logger.info(f"Нестандартное IANA имя '{final_tz_name}' принято для user {user_id}")
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
            except Exception as e: # Ловим другие ошибки ZoneInfo
                logger.error(f"Ошибка при проверке ZoneInfo для '{user_input_tz}': {e}")
                await update.message.reply_text("Произошла ошибка при проверке часового пояса. Попробуйте позже.", reply_markup=CANCEL_KEYBOARD)
                return State.SET_TIMEZONE_ASK


    # Если мы здесь, final_tz_name содержит валидное IANA имя
    if final_tz_name:
        try:
            # 1. Сохранение в БД
            await db.set_user_timezone(pool, user_id, final_tz_name)
            logger.info(f"Пользователь {user_id} установил часовой пояс: {final_tz_name}")

            # --- 2. Блок перепланировки задач ---
            logger.info(f"Запуск перепланировки задач для user {user_id} из-за смены TZ на {final_tz_name}")
            reschedule_error_occurred = False
            try:
                app = context.application
                new_zone = ZoneInfo(final_tz_name) # ZoneInfo здесь точно сработает

                # Перепланировка ежедневного напоминания
                reminder_settings = await db.get_user_reminder_settings(pool, user_id)
                if reminder_settings and reminder_settings.get('active'): # Проверяем ключ 'active'
                    logger.info(f"Перепланировка ежедневного напоминания для {user_id}...")
                    await scheduler.schedule_user_daily_reminder(
                        app, user_id, reminder_settings['target_local_time'], new_zone
                    )
                else:
                    logger.info(f"Ежедневное напоминание для {user_id} неактивно или не настроено, перепланировка не требуется.")

                # Перепланировка ретроспективы
                retro_settings = await db.get_user_retrospective_settings(pool, user_id)
                if retro_settings and retro_settings.get('active'): # Проверяем ключ 'active'
                    logger.info(f"Перепланировка {retro_settings['retrospective_type']} ретроспективы для {user_id}...")
                    await scheduler.schedule_user_retrospective(
                        app,
                        user_id,
                        retro_settings['scheduled_day'],
                        retro_settings['target_local_time'],
                        new_zone,
                        retro_settings['retrospective_type']
                    )
                else:
                    logger.info(f"Запланированная ретроспектива для {user_id} неактивна или не настроена, перепланировка не требуется.")

                logger.info(f"Перепланировка задач для user {user_id} завершена.")

            except Exception as e:
                reschedule_error_occurred = True
                logger.exception(f"Ошибка при перепланировке задач для user {user_id} после смены часового пояса.")

            # --- Конец блока перепланировки ---

            # 3. Ответ пользователю
            success_message = f"✅ Ваш часовой пояс успешно установлен на: <b>{final_tz_name}</b>"
            if reschedule_error_occurred:
                 success_message += (
                     "\n\n⚠️ Однако произошла ошибка при обновлении расписания напоминаний/ретроспектив. "
                     "Они могут сработать по старому времени до следующего перезапуска бота."
                 )

            await update.message.reply_html(
                success_message,
                reply_markup=MAIN_MENU_KEYBOARD,
            )
            return ConversationHandler.END

        except Exception as e: # Ошибка сохранения в БД
            logger.exception(f"Ошибка сохранения часового пояса {final_tz_name} для {user_id}:")
            await update.message.reply_text(
                "Произошла ошибка при сохранении часового пояса. Попробуйте позже.",
                reply_markup=MAIN_MENU_KEYBOARD
            )
            return ConversationHandler.END
    else:
        # Сюда не должны попасть, если логика валидации верна
         logger.error(f"Не удалось определить валидный часовой пояс из ввода '{user_input_tz}' для user {user_id} после всех проверок.")
         await update.message.reply_text(
             "Произошла внутренняя ошибка при обработке часового пояса. Попробуйте позже.",
             reply_markup=MAIN_MENU_KEYBOARD
         )
         return ConversationHandler.END
