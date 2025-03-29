# scheduler.py
import logging
import asyncio
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Dict, Any, Coroutine

from telegram.ext import Application, CallbackContext, Job

# --- ИЗМЕНЕНО: Абсолютный импорт ---
import db # Импортируем модуль db
from utils import get_now_utc # Импортируем из utils
from constants import CANCEL_KEYBOARD # Импортируем из constants

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
        # TODO (DB Schema): Обновлять last_sent в БД не обязательно, но можно для статистики
        # pool = context.bot_data.get("db_pool")
        # if pool: await db.update_last_sent_daily(pool, user_id, get_today_utc())
    except Exception as e:
        # Логируем ошибку, но не останавливаем работу планировщика
        logger.exception(f"Ошибка при отправке ежедневного напоминания пользователю {user_id}")


async def send_retrospective_notification(context: CallbackContext) -> None:
    """Отправляет напоминание о запланированной ретроспективе."""
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
        # TODO (DB Schema): Обновлять last_sent в БД не обязательно
        # pool = context.bot_data.get("db_pool")
        # if pool: await db.update_last_sent_scheduled_retrospective(pool, user_id, get_today_utc())
    except Exception as e:
        logger.exception(f"Ошибка при отправке уведомления о ретроспективе пользователю {user_id}")


# --- Функции планирования задач ---

def _calculate_next_run_utc(target_local_time: time, user_zone: ZoneInfo) -> datetime:
    """Рассчитывает следующее время запуска задачи в UTC для ежедневных задач."""
    now_utc = get_now_utc()
    now_local = now_utc.astimezone(user_zone)

    # Составляем сегодняшнюю дату и целевое время в ЛОКАЛЬНОЙ зоне
    next_run_local = datetime.combine(
        now_local.date(), # Берем сегодняшнюю дату
        target_local_time, # Берем целевое время
        tzinfo=user_zone   # Устанавливаем часовой пояс
    )

    # Если сегодня это время уже прошло, берем завтра
    if next_run_local <= now_local:
        next_run_local += timedelta(days=1)

    # Конвертируем обратно в UTC для планировщика
    return next_run_local.astimezone(timezone.utc)

def _calculate_next_retro_run_utc(
    scheduled_day: int, target_local_time: time, user_zone: ZoneInfo, mode: str
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
    # Если нужный день недели уже прошел на этой неделе ИЛИ
    # если сегодня нужный день недели, но время уже прошло
    if days_ahead < 0 or (days_ahead == 0 and target_dt_local_today <= now_local):
        days_ahead += 7 # Переносим на следующую неделю

    # Дата и время следующего запуска в локальной зоне
    next_run_local = target_dt_local_today + timedelta(days=days_ahead)

    # Для двухнедельной ретроспективы нужно убедиться, что мы не пропускаем неделю.
    # Это требует хранения даты последнего запуска или эталонной даты.
    # Пока оставляем простой вариант - планирование на ближайший подходящий день.
    # Если требуется строгая двухнедельная периодичность, логику нужно усложнить.

    # Конвертируем в UTC
    return next_run_local.astimezone(timezone.utc)


async def schedule_user_daily_reminder(
    app: Application, user_id: int, target_local_time: time, user_zone: ZoneInfo
):
    """Планирует или перепланирует ежедневное напоминание для пользователя."""
    job_name = f"daily_{user_id}"
    # Получаем JobQueue из application
    job_queue = app.job_queue
    if not job_queue:
        logger.error(f"JobQueue не найден в application при планировании для {user_id}")
        return # Не можем планировать без job_queue

    # Удаляем старую задачу, если она есть
    current_jobs = job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
        logger.info(f"Удалена старая задача {job_name}")

    try:
        next_run_utc = _calculate_next_run_utc(target_local_time, user_zone)
        # Планируем через job_queue
        job_queue.run_repeating(
            send_daily_reminder,
            interval=timedelta(days=1),
            first=next_run_utc, # Используем рассчитанное UTC время
            data={'user_id': user_id},
            name=job_name
        )
        logger.info(f"Запланировано ежедневное напоминание для {user_id} на {next_run_utc} (UTC)")
    except Exception as e:
        logger.exception(f"Ошибка планирования ежедневного напоминания для {user_id}")
        # Перевыбрасываем исключение, чтобы вызывающая функция могла его обработать
        raise


async def schedule_user_retrospective(
    app: Application, user_id: int, scheduled_day: int, target_local_time: time,
    user_zone: ZoneInfo, mode: str # 'weekly' or 'biweekly'
):
    """Планирует или перепланирует запланированную ретроспективу."""
    job_name = f"retro_{mode}_{user_id}"
    job_queue = app.job_queue
    if not job_queue:
        logger.error(f"JobQueue не найден в application при планировании ретроспективы для {user_id}")
        return

    # Удаляем старые задачи (на всякий случай, если режим изменился или задача дублируется)
    for old_mode in ["weekly", "biweekly"]:
        old_job_name = f"retro_{old_mode}_{user_id}"
        current_jobs = job_queue.get_jobs_by_name(old_job_name)
        for job in current_jobs:
            job.schedule_removal()
            logger.info(f"Удалена старая задача {old_job_name}")

    try:
        next_run_utc = _calculate_next_retro_run_utc(scheduled_day, target_local_time, user_zone, mode)
        interval_weeks = 1 if mode == "weekly" else 2
        job_queue.run_repeating(
            send_retrospective_notification,
            interval=timedelta(weeks=interval_weeks),
            first=next_run_utc,
            data={'user_id': user_id, 'mode': mode},
            name=job_name
        )
        logger.info(f"Запланирована {mode} ретроспектива для {user_id} на {next_run_utc} (UTC)")
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

            # Проверки типов и значений
            if not isinstance(target_local_time, time):
                 logger.warning(f"Неверный формат target_local_time для user {user_id}: {target_local_time} (тип: {type(target_local_time)})")
                 continue
            if not tz_str:
                 logger.warning(f"Отсутствует часовой пояс для user {user_id} при загрузке напоминания.")
                 continue

            try:
                user_zone = ZoneInfo(tz_str)
                # Планируем задачу
                await schedule_user_daily_reminder(app, user_id, target_local_time, user_zone)
                count += 1
            except ZoneInfoNotFoundError:
                logger.error(f"Неверный часовой пояс '{tz_str}' для user {user_id} в БД при загрузке напоминания.")
            except Exception as e:
                # Ошибка уже залогирована внутри schedule_user_daily_reminder
                logger.error(f"Не удалось запланировать напоминание для user {user_id} при старте.")
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
        retrospectives = await db.get_active_scheduled_retrospectives(pool)
        logger.info(f"Загружено {len(retrospectives)} активных запланированных ретроспектив из БД.")
        count = 0
        for r in retrospectives:
            user_id = r["user_id"]
            scheduled_day = r["scheduled_day"]
            target_local_time = r["target_local_time"]
            tz_str = r["timezone"]
            mode = r["retrospective_type"] # 'weekly' or 'biweekly'

            # Проверки
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
                await schedule_user_retrospective(app, user_id, scheduled_day, target_local_time, user_zone, mode)
                count += 1
            except ZoneInfoNotFoundError:
                logger.error(f"Неверный часовой пояс '{tz_str}' для ретроспективы user {user_id} в БД при загрузке.")
            except Exception as e:
                logger.error(f"Не удалось запланировать ретроспективу для user {user_id} при старте.")
        logger.info(f"Успешно запланировано {count} из {len(retrospectives)} ретроспектив.")

    except Exception as e:
        logger.exception("Общая ошибка при загрузке и планировании ретроспектив из БД.")
