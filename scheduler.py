# scheduler.py
import logging
import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Dict, Any, Coroutine, Optional # Добавлен Optional

from telegram.ext import Application, CallbackContext, Job

# --- ИЗМЕНЕНО: Абсолютный импорт ---
import db # Импортируем модуль db
from utils import get_now_utc # Импортируем из utils
# from constants import CANCEL_KEYBOARD # Этот импорт здесь больше не нужен

logger = logging.getLogger(__name__)

# --- Функции отправки уведомлений ---

async def send_daily_reminder(context: CallbackContext) -> None:
    """Отправляет напоминание о ежедневном тесте."""
    job = context.job
    if not job or not isinstance(job.data, dict):
         logger.error("Не удалось получить данные из job в send_daily_reminder")
         return
    user_id = job.data.get('user_id')
    if not user_id:
        logger.error("user_id не найден в job.data в send_daily_reminder")
        return

    logger.info(f"Отправка ежедневного напоминания пользователю {user_id}")
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="👋 Напоминание: пора пройти ваш ежедневный тест!",
            # Можно добавить кнопку для быстрого старта теста
            # reply_markup=ReplyKeyboardMarkup([["Пройти тест"], ["Главное меню"]], resize_keyboard=True)
        )
        # Обновлять last_sent в БД больше не нужно
    except Exception as e:
        # Логируем ошибку, но не останавливаем работу планировщика
        logger.exception(f"Ошибка при отправке ежедневного напоминания пользователю {user_id}")


async def send_retrospective_notification(context: CallbackContext) -> None:
    """Отправляет напоминание о запланированной ретроспективе и обновляет last_sent."""
    job = context.job
    if not job or not isinstance(job.data, dict):
         logger.error("Не удалось получить данные из job в send_retrospective_notification")
         return

    user_id = job.data.get('user_id')
    mode = job.data.get('mode', 'неизвестный') # 'weekly' or 'biweekly'

    if not user_id:
        logger.error("user_id не найден в job.data в send_retrospective_notification")
        return

    logger.info(f"Отправка {mode} уведомления о ретроспективе пользователю {user_id}")
    try:
        # Определяем текст в зависимости от режима
        freq_text = "еженедельной" if mode == "weekly" else "двухнедельной"
        await context.bot.send_message(
            chat_id=user_id,
            text=f"📅 Напоминание: пришло время для вашей {freq_text} ретроспективы!",
            # Можно добавить кнопку для быстрого старта
            # reply_markup=ReplyKeyboardMarkup([["Начать ретроспективу"], ["Главное меню"]], resize_keyboard=True)
        )

        # --- ИЗМЕНЕНО: Обновляем last_sent_timestamp в БД ---
        pool = context.bot_data.get("db_pool")
        if pool and user_id:
             try:
                  await db.update_last_sent_scheduled_retrospective(pool, user_id, get_now_utc())
             except Exception as e:
                  # Ошибка уже залогирована в db.py, здесь просто пишем для контекста
                  logger.error(f"Не удалось обновить last_sent_timestamp для ретроспективы user {user_id} после отправки уведомления.")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    except Exception as e:
        logger.exception(f"Ошибка при отправке уведомления о ретроспективе пользователю {user_id}")


# --- Функции планирования задач ---

def _calculate_next_run_utc(target_local_time: time, user_zone: ZoneInfo) -> datetime:
    """Рассчитывает следующее время запуска задачи в UTC для ежедневных задач."""
    now_utc = get_now_utc()
    now_local = now_utc.astimezone(user_zone)

    # Составляем сегодняшнюю дату и целевое время в ЛОКАЛЬНОЙ зоне
    next_run_local = datetime.combine(
        now_local.date(),
        target_local_time,
        tzinfo=user_zone
    )

    # Если сегодня это время уже прошло, берем завтра
    if next_run_local <= now_local:
        next_run_local += timedelta(days=1)

    # Конвертируем обратно в UTC для планировщика
    return next_run_local.astimezone(timezone.utc)

# --- ИЗМЕНЕНО: Добавлен last_sent_timestamp и логика для biweekly ---
def _calculate_next_retro_run_utc(
    scheduled_day: int, target_local_time: time, user_zone: ZoneInfo, mode: str, last_sent_timestamp: Optional[datetime] = None
) -> datetime:
    """Рассчитывает следующее время запуска для еженедельных/двухнедельных задач."""
    now_utc = get_now_utc()
    now_local = now_utc.astimezone(user_zone)

    # Создаем datetime для сегодняшнего целевого времени в локальной зоне
    target_dt_local_today = datetime.combine(
        now_local.date(),
        target_local_time,
        tzinfo=user_zone
    )

    days_ahead = scheduled_day - target_dt_local_today.weekday()
    if days_ahead < 0 or (days_ahead == 0 and target_dt_local_today <= now_local):
        days_ahead += 7 # Переносим на следующую неделю

    # Дата и время следующего запуска в локальной зоне
    next_run_local = target_dt_local_today + timedelta(days=days_ahead)

    # --- НОВОЕ: Коррекция для biweekly ---
    if mode == "biweekly" and last_sent_timestamp:
        # Убедимся, что last_sent_timestamp имеет tzinfo (должен быть UTC из БД)
        if last_sent_timestamp.tzinfo is None:
           try:
               # Попытка установить UTC, если из БД пришло без TZ (маловероятно с TIMESTAMPTZ)
               last_sent_timestamp = last_sent_timestamp.replace(tzinfo=timezone.utc)
               logger.warning("last_sent_timestamp из БД не имел таймзоны, установлен UTC.")
           except Exception: # На случай, если replace не сработает для какого-то типа
                logger.error("Не удалось установить UTC для last_sent_timestamp, точность biweekly может быть нарушена.")
                last_sent_timestamp = None # Сбрасываем, чтобы не использовать некорректное значение

        if last_sent_timestamp: # Проверяем снова после возможной установки TZ
            # Целевое время + 14 дней от последней отправки (в UTC)
            target_next_run_utc = last_sent_timestamp + timedelta(weeks=2)
            # Рассчитанное время запуска (в UTC)
            current_calculated_run_utc = next_run_local.astimezone(timezone.utc)

            # Если рассчитанное время раньше, чем целевое + 14 дней (т.е. мы на "неправильной" неделе)
            # Добавляем допуск в несколько минут на случай небольших расхождений
            if current_calculated_run_utc < target_next_run_utc - timedelta(minutes=5):
                logger.info(f"Коррекция biweekly: {current_calculated_run_utc.strftime('%Y-%m-%d %H:%M')} < {target_next_run_utc.strftime('%Y-%m-%d %H:%M')}. Добавляем 7 дней.")
                next_run_local += timedelta(days=7)
    # --- КОНЕЦ КОРРЕКЦИИ ---

    # Конвертируем в UTC
    return next_run_local.astimezone(timezone.utc)


async def schedule_user_daily_reminder(
    app: Application, user_id: int, target_local_time: time, user_zone: ZoneInfo
):
    """Планирует или перепланирует ежедневное напоминание для пользователя."""
    job_name = f"daily_{user_id}"
    job_queue = app.job_queue
    if not job_queue:
        logger.error(f"JobQueue не найден в application при планировании для {user_id}")
        return

    # Удаляем старую задачу, если она есть
    current_jobs = job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Удалена старая задача {job_name}")

    try:
        next_run_utc = _calculate_next_run_utc(target_local_time, user_zone)
        job_queue.run_repeating(
            send_daily_reminder,
            interval=timedelta(days=1),
            first=next_run_utc,
            data={'user_id': user_id},
            name=job_name
        )
        logger.info(f"Запланировано ежедневное напоминание для {user_id} на {next_run_utc.strftime('%Y-%m-%d %H:%M')} (UTC)")
    except Exception as e:
        logger.exception(f"Ошибка планирования ежедневного напоминания для {user_id}")
        raise


async def schedule_user_retrospective(
    app: Application, user_id: int, scheduled_day: int, target_local_time: time,
    user_zone: ZoneInfo, mode: str # 'weekly' or 'biweekly'
    # last_sent_timestamp не нужен как аргумент, функция расчета сама его получит из БД при необходимости,
    # если вызывать ее из load_and_schedule_retrospectives
):
    """Планирует или перепланирует запланированную ретроспективу."""
    job_name = f"retro_{mode}_{user_id}"
    job_queue = app.job_queue
    if not job_queue:
        logger.error(f"JobQueue не найден в application при планировании ретроспективы для {user_id}")
        return

    # Удаляем старые задачи (на всякий случай, если режим изменился или задача дублируется)
    # Важно удалять задачи и для weekly, и для biweekly, если пользователь сменил режим
    for old_mode in ["weekly", "biweekly"]:
        old_job_name = f"retro_{old_mode}_{user_id}"
        current_jobs = job_queue.get_jobs_by_name(old_job_name)
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"Удалена старая задача {old_job_name} при планировании {mode} для {user_id}")

    try:
        # --- ИЗМЕНЕНО: Получаем last_sent_timestamp из БД перед расчетом ---
        last_sent_ts = None
        pool = app.bot_data.get("db_pool")
        if pool and mode == "biweekly": # Получаем только если нужно для biweekly
             settings = await db.get_user_retrospective_settings(pool, user_id)
             if settings:
                 last_sent_ts = settings.get("last_sent_timestamp") # asyncpg Record работает как dict
                 logger.debug(f"Получен last_sent_timestamp={last_sent_ts} для user {user_id} при планировании.")

        next_run_utc = _calculate_next_retro_run_utc(scheduled_day, target_local_time, user_zone, mode, last_sent_ts)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        interval_weeks = 1 if mode == "weekly" else 2
        job_queue.run_repeating(
            send_retrospective_notification,
            interval=timedelta(weeks=interval_weeks),
            first=next_run_utc,
            data={'user_id': user_id, 'mode': mode},
            name=job_name
        )
        logger.info(f"Запланирована {mode} ретроспектива для {user_id} на {next_run_utc.strftime('%Y-%m-%d %H:%M')} (UTC)")
    except Exception as e:
        logger.exception(f"Ошибка планирования {mode} ретроспективы для {user_id}")
        raise


# --- Функции загрузки при старте ---

async def load_and_schedule_reminders(app: Application) -> None:
    """Загружает активные напоминания из БД и планирует их."""
    pool = app.bot_data.get("db_pool")
    if not pool:
        logger.error("Пул БД не найден в bot_data при загрузке напоминаний.")
        return

    try:
        reminders = await db.get_active_daily_reminders(pool)
        logger.info(f"Загружено {len(reminders)} активных ежедневных напоминаний из БД.")
        count = 0
        for r in reminders:
            user_id = r["user_id"]
            target_local_time = r["target_local_time"] # Ожидаем тип time
            tz_str = r["timezone"]

            if not isinstance(target_local_time, time):
                 logger.warning(f"Неверный формат target_local_time для user {user_id}: {target_local_time} (тип: {type(target_local_time)})")
                 continue
            if not tz_str:
                 logger.warning(f"Отсутствует часовой пояс для user {user_id} при загрузке напоминания.")
                 continue

            try:
                user_zone = ZoneInfo(tz_str)
                await schedule_user_daily_reminder(app, user_id, target_local_time, user_zone)
                count += 1
            except ZoneInfoNotFoundError:
                logger.error(f"Неверный часовой пояс '{tz_str}' для user {user_id} в БД при загрузке напоминания.")
            except Exception as e:
                logger.error(f"Не удалось запланировать напоминание для user {user_id} при старте.", exc_info=True)
        logger.info(f"Успешно запланировано {count} из {len(reminders)} ежедневных напоминаний.")

    except Exception as e:
        logger.exception("Общая ошибка при загрузке и планировании ежедневных напоминаний из БД.")


async def load_and_schedule_retrospectives(app: Application) -> None:
    """Загружает активные запланированные ретроспективы из БД и планирует их."""
    pool = app.bot_data.get("db_pool")
    if not pool:
        logger.error("Пул БД не найден в bot_data при загрузке ретроспектив.")
        return

    try:
        # Используем обновленную функцию DB, которая возвращает last_sent_timestamp
        retrospectives = await db.get_active_scheduled_retrospectives(pool)
        logger.info(f"Загружено {len(retrospectives)} активных запланированных ретроспектив из БД.")
        count = 0
        for r in retrospectives:
            user_id = r["user_id"]
            scheduled_day = r["scheduled_day"]
            target_local_time = r["target_local_time"]
            tz_str = r["timezone"]
            mode = r["retrospective_type"] # 'weekly' or 'biweekly'
            # ИЗМЕНЕНО: Получаем last_sent_timestamp
            last_sent_ts = r["last_sent_timestamp"]

            # Проверки (как были)
            if not isinstance(target_local_time, time):
                 logger.warning(f"Неверный формат target_local_time для ретроспективы user {user_id}: {target_local_time} (тип: {type(target_local_time)})")
                 continue
            if scheduled_day is None or not (0 <= scheduled_day <= 6):
                 logger.warning(f"Неверный scheduled_day для ретроспективы user {user_id}: {scheduled_day}")
                 continue
            if not tz_str:
                 logger.warning(f"Отсутствует часовой пояс для ретроспективы user {user_id} при загрузке.")
                 continue
            if mode not in ["weekly", "biweekly"]:
                 logger.warning(f"Неверный retrospective_type для user {user_id}: {mode}")
                 continue

            try:
                user_zone = ZoneInfo(tz_str)
                # --- ИЗМЕНЕНО: Вызов schedule_user_retrospective теперь не требует last_sent_timestamp,
                # так как он сам получит его из БД перед вызовом _calculate_next_retro_run_utc ---
                await schedule_user_retrospective(
                    app, user_id, scheduled_day, target_local_time, user_zone, mode
                )
                count += 1
            except ZoneInfoNotFoundError:
                logger.error(f"Неверный часовой пояс '{tz_str}' для ретроспективы user {user_id} в БД при загрузке.")
            except Exception as e:
                logger.error(f"Не удалось запланировать ретроспективу для user {user_id} при старте.", exc_info=True)
        logger.info(f"Успешно запланировано {count} из {len(retrospectives)} ретроспектив.")

    except Exception as e:
        logger.exception("Общая ошибка при загрузке и планировании ретроспектив из БД.")
