# db.py
import asyncpg
import os
from datetime import date, time
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

async def create_db_pool() -> Optional[asyncpg.pool.Pool]:
    """Создаёт пул соединений с PostgreSQL."""
    if not DATABASE_URL:
        logger.error("DATABASE_URL не задан в переменных окружения!")
        return None
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("Пул соединений с БД успешно создан.")
        # TODO (DB Schema): Добавить проверку и создание таблиц, если их нет
        # await setup_database(pool)
        return pool
    except Exception as e:
        logger.exception("Ошибка при создании пула соединений с БД.")
        return None

# TODO (DB Schema): Пересмотреть схему БД и обновить функции ниже
# для поддержки хранения часовых поясов и локального времени пользователя.

# --- Настройки Пользователя (Часовой пояс) ---

async def set_user_timezone(pool: asyncpg.pool.Pool, user_id: int, timezone: str) -> None:
    """Сохраняет или обновляет часовой пояс пользователя."""
    # TODO (DB Schema): Нужна таблица user_settings(user_id PK, timezone VARCHAR)
    # или добавить столбец timezone в существующую таблицу пользователей.
    async with pool.acquire() as conn:
        try:
            # Пример запроса (нужно адаптировать под вашу схему)
            await conn.execute(
                 """
                 INSERT INTO user_settings (user_id, timezone) VALUES ($1, $2)
                 ON CONFLICT (user_id) DO UPDATE SET timezone = EXCLUDED.timezone
                 """,
                 user_id, timezone
            )
            logger.info(f"Часовой пояс '{timezone}' установлен для пользователя {user_id}")
        except Exception as e:
            logger.exception(f"Ошибка при установке часового пояса для {user_id}")
            raise

async def get_user_timezone(pool: asyncpg.pool.Pool, user_id: int) -> Optional[str]:
    """Получает часовой пояс пользователя."""
    # TODO (DB Schema): Адаптировать запрос под вашу схему.
    async with pool.acquire() as conn:
        try:
            result = await conn.fetchval(
                "SELECT timezone FROM user_settings WHERE user_id = $1",
                user_id
            )
            return result
        except Exception as e:
            logger.exception(f"Ошибка при получении часового пояса для {user_id}")
            return None # Важно возвращать None при ошибке

# --- Ежедневные напоминания ---
# TODO (DB Schema): Изменить схему daily_reminders:
# user_id PK, target_local_time TIME, timezone VARCHAR, active BOOLEAN
# Удалить last_sent, reminder_time (старое)

async def upsert_daily_reminder_settings(
    pool: asyncpg.pool.Pool, user_id: int, target_local_time: time, timezone: str, active: bool = True
) -> None:
    """Сохраняет настройки ежедневного напоминания."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO daily_reminders (user_id, target_local_time, timezone, active)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id) DO UPDATE
                SET target_local_time = EXCLUDED.target_local_time,
                    timezone = EXCLUDED.timezone,
                    active = EXCLUDED.active
                """,
                user_id, target_local_time, timezone, active
            )
            logger.info(f"Настройки ежедневного напоминания обновлены для {user_id}")
        except Exception as e:
            logger.exception(f"Ошибка в upsert_daily_reminder_settings для {user_id}")
            raise

async def get_active_daily_reminders(pool: asyncpg.pool.Pool) -> List[asyncpg.Record]:
    """Получает список активных ежедневных напоминаний (данные для пересчета)."""
    async with pool.acquire() as conn:
        # Возвращаем данные, нужные для расчета следующего запуска в scheduler.py
        return await conn.fetch(
            "SELECT user_id, target_local_time, timezone FROM daily_reminders WHERE active = true"
        )

# Функции update_last_sent_daily больше не нужны для планирования

# --- Запланированные ретроспективы ---
# TODO (DB Schema): Изменить схему scheduled_retrospectives:
# user_id PK, scheduled_day SMALLINT, target_local_time TIME, timezone VARCHAR,
# retrospective_type VARCHAR, active BOOLEAN
# Удалить local_time, server_time, last_sent

async def upsert_scheduled_retrospective_settings(
    pool: asyncpg.pool.Pool, user_id: int, scheduled_day: int, target_local_time: time,
    timezone: str, retrospective_type: str, active: bool = True
) -> None:
    """Сохраняет настройки запланированной ретроспективы."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO scheduled_retrospectives
                    (user_id, scheduled_day, target_local_time, timezone, retrospective_type, active)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (user_id) DO UPDATE
                SET scheduled_day = EXCLUDED.scheduled_day,
                    target_local_time = EXCLUDED.target_local_time,
                    timezone = EXCLUDED.timezone,
                    retrospective_type = EXCLUDED.retrospective_type,
                    active = EXCLUDED.active
                """,
                user_id, scheduled_day, target_local_time, timezone, retrospective_type, active
            )
            logger.info(f"Настройки запланированной ретроспективы обновлены для {user_id}")
        except Exception as e:
            logger.exception(f"Ошибка в upsert_scheduled_retrospective_settings для {user_id}")
            raise

async def get_active_scheduled_retrospectives(pool: asyncpg.pool.Pool) -> List[asyncpg.Record]:
    """Получает список активных запланированных ретроспектив (данные для пересчета)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT user_id, scheduled_day, target_local_time, timezone, retrospective_type
            FROM scheduled_retrospectives WHERE active = true
            """
        )

# Функции update_last_sent_scheduled_retrospective больше не нужны для планирования

# Старые функции weekly_retrospectives можно удалить, если они больше не используются.
# async def upsert_weekly_retrospective(...)
# async def get_active_weekly_retrospectives(...)
# async def update_last_sent_weekly(...)

# --- Функции для сохранения/чтения данных тестов/ретроспектив ---
# TODO (Data Storage): Эти функции нужно будет добавить/изменить при переносе
# данных из JSON в БД. Пока оставляем как есть (работа с файлами будет в хендлерах).
# async def save_test_results(pool, user_id, timestamp, answers, interpretation): ...
# async def get_test_results_for_period(pool, user_id, start_date, end_date): ...
# async def save_retrospective_results(pool, user_id, timestamp, period_days, averages, open_answers, interpretation): ...
