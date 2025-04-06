# handlers/timezone.py
import logging
from zoneinfo import available_timezones, ZoneInfo, ZoneInfoNotFoundError

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, Application # Добавлен Application

# Импорты проекта
from constants import State, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
import db
import scheduler
from utils import parse_gmt_offset_to_iana
from .common import exit_to_main

logger = logging.getLogger(__name__)

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
    app: Optional[Application] = context.application # Получаем Application

    if not pool:
         logger.error(f"DB pool not found in context for user {user_id} in set_timezone_receive")
         await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    if not app:
         logger.error(f"Application object not found in context for user {user_id} in set_timezone_receive")
         # Ошибка сохранения вряд ли поможет, так как планировщик тоже не сработает
         await update.message.reply_text("Внутренняя ошибка: не удалось получить доступ к планировщику.", reply_markup=MAIN_MENU_KEYBOARD)
         return ConversationHandler.END

    if user_input_tz == "Главное меню":
        current_tz = await db.get_user_timezone(pool, user_id)
        if current_tz:
             await update.message.reply_text(f"Ваш часовой пояс остался прежним: {current_tz}.", reply_markup=MAIN_MENU_KEYBOARD)
        else:
             await update.message.reply_text("Часовой пояс не установлен.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    # Валидация и конвертация (без изменений)
    if user_input_tz in AVAILABLE_TIMEZONES:
        final_tz_name = user_input_tz
    else:
        iana_from_offset = parse_gmt_offset_to_iana(user_input_tz)
        if iana_from_offset:
            final_tz_name = iana_from_offset
            logger.info(f"Смещение '{user_input_tz}' преобразовано в IANA: '{final_tz_name}' для user {user_id}")
        else:
            try:
                _ = ZoneInfo(user_input_tz)
                final_tz_name = user_input_tz
                AVAILABLE_TIMEZONES.add(final_tz_name)
                logger.info(f"Нестандартное IANA имя '{final_tz_name}' принято для user {user_id}")
            except ZoneInfoNotFoundError:
                logger.warning(f"Пользователь {user_id} ввел неверный часовой пояс: {user_input_tz}")
                await update.message.reply_text(
                    f"Часовой пояс '{user_input_tz}' не распознан. \n"
                    "Пожалуйста, проверьте правильность написания (например, <code>Europe/Moscow</code> или <code>GMT+3</code>) и попробуйте снова, "
                    "или нажмите 'Главное меню'.",
                    reply_markup=CANCEL_KEYBOARD,
                    parse_mode='HTML'
                )
                return State.SET_TIMEZONE_ASK
            except Exception as e:
                logger.error(f"Ошибка при проверке ZoneInfo для '{user_input_tz}': {e}")
                await update.message.reply_text("Произошла ошибка при проверке часового пояса. Попробуйте позже.", reply_markup=CANCEL_KEYBOARD)
                return State.SET_TIMEZONE_ASK

    if final_tz_name:
        try:
            # 1. Сохранение в БД
            await db.set_user_timezone(pool, user_id, final_tz_name)
            logger.info(f"Пользователь {user_id} установил часовой пояс: {final_tz_name}")

            # --- 2. Блок перепланировки задач ---
            logger.info(f"Запуск перепланировки задач для user {user_id} из-за смены TZ на {final_tz_name}")
            reschedule_error_occurred = False
            error_details = "" # Для хранения деталей ошибки

            try:
                # Проверяем наличие JobQueue (важно!)
                if not app.job_queue:
                     logger.error(f"JobQueue is None, rescheduling skipped for user {user_id}.")
                     raise RuntimeError("JobQueue is not available.") # Поднимаем ошибку, чтобы попасть в except

                new_zone = ZoneInfo(final_tz_name)

                # Перепланировка ежедневного напоминания
                reminder_settings = await db.get_user_reminder_settings(pool, user_id)
                if reminder_settings: # Проверяем, что настройки есть
                    if reminder_settings.get('active'):
                        logger.info(f"Перепланировка ежедневного напоминания для {user_id}...")
                        await scheduler.schedule_user_daily_reminder(
                            app, user_id, reminder_settings['target_local_time'], new_zone
                        )
                    else:
                        # Если напоминание неактивно, нужно удалить старую задачу, если она была
                        logger.info(f"Ежедневное напоминание для {user_id} неактивно, удаляем старую задачу (если есть)...")
                        job_name = f"daily_{user_id}"
                        current_jobs = app.job_queue.get_jobs_by_name(job_name)
                        for job in current_jobs:
                            job.schedule_removal()
                            logger.info(f"Удалена неактивная задача {job_name}")
                else:
                    logger.info(f"Настройки ежедневного напоминания для {user_id} не найдены, перепланировка не требуется.")

                # Перепланировка ретроспективы
                retro_settings = await db.get_user_retrospective_settings(pool, user_id)
                if retro_settings: # Проверяем, что настройки есть
                    if retro_settings.get('active'):
                         logger.info(f"Перепланировка {retro_settings['retrospective_type']} ретроспективы для {user_id}...")
                         await scheduler.schedule_user_retrospective(
                             app, user_id, retro_settings['scheduled_day'],
                             retro_settings['target_local_time'], new_zone,
                             retro_settings['retrospective_type']
                         )
                    else:
                        # Если ретроспектива неактивна, удаляем старую задачу
                        logger.info(f"Запланированная ретроспектива для {user_id} неактивна, удаляем старую задачу (если есть)...")
                        # Удаляем и weekly и biweekly на всякий случай
                        for mode in ["weekly", "biweekly"]:
                            job_name = f"retro_{mode}_{user_id}"
                            current_jobs = app.job_queue.get_jobs_by_name(job_name)
                            for job in current_jobs:
                                job.schedule_removal()
                                logger.info(f"Удалена неактивная задача {job_name}")
                else:
                    logger.info(f"Настройки запланированной ретроспективы для {user_id} не найдены, перепланировка не требуется.")

                logger.info(f"Перепланировка задач для user {user_id} успешно завершена.")

            except Exception as e: # Ловим ЛЮБОЕ исключение во время перепланировки
                reschedule_error_occurred = True
                # <<< ИСПРАВЛЕНО: Логируем само исключение! >>>
                logger.exception(f"Ошибка при перепланировке задач для user {user_id} после смены часового пояса.")
                error_details = str(e) # Сохраняем текст ошибки для возможного показа

            # --- Конец блока перепланировки ---

            # 3. Ответ пользователю
            success_message = f"✅ Ваш часовой пояс успешно установлен на: <b>{final_tz_name}</b>"
            if reschedule_error_occurred:
                 success_message += (
                     "\n\n⚠️ Однако произошла ошибка при обновлении расписания напоминаний/ретроспектив. "
                     "Они могут сработать по старому времени до следующего перезапуска бота."
                 )
                 # Можно добавить детали ошибки, если это безопасно (не содержит чувствительной информации)
                 # logger.debug(f"Детали ошибки перепланировки: {error_details}") # Логируем детали

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
        # Сюда не должны попасть
         logger.error(f"Не удалось определить валидный часовой пояс из ввода '{user_input_tz}' для user {user_id} после всех проверок.")
         await update.message.reply_text(
             "Произошла внутренняя ошибка при обработке часового пояса. Попробуйте позже.",
             reply_markup=MAIN_MENU_KEYBOARD
         )
         return ConversationHandler.END
