import asyncpg
import os
from datetime import date
from typing import List
import logging

logger = logging.getLogger(__name__)

DATABASE_URL: str = os.getenv("DATABASE_URL", "")

async def create_db_pool() -> asyncpg.pool.Pool:
    """Создаёт пул соединений с PostgreSQL."""
    try:
        pool = await asyncpg.create_pool(DATABASE_URL)
        return pool
    except Exception as e:
        logger.exception("Ошибка при создании пула соединений.")
        raise

# ----------------------- Ежедневные напоминания -----------------------

async def upsert_daily_reminder(pool: asyncpg.pool.Pool, user_id: int, reminder_time: str) -> None:
    """Добавляет или обновляет напоминание о ежедневном тесте.
    
    Параметр reminder_time приводится к типу TIME с помощью $2::time.
    """
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO daily_reminders (user_id, reminder_time, last_sent, active)
                VALUES ($1, $2::time, NULL, true)
                ON CONFLICT (user_id) DO UPDATE 
                SET reminder_time = EXCLUDED.reminder_time, active = true
                """,
                user_id, reminder_time
            )
        except Exception as e:
            logger.exception("Ошибка в upsert_daily_reminder")
            raise

async def get_active_daily_reminders(pool: asyncpg.pool.Pool) -> List[asyncpg.Record]:
    """Получает список активных ежедневных напоминаний."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, reminder_time, last_sent FROM daily_reminders WHERE active = true"
        )

async def update_last_sent_daily(pool: asyncpg.pool.Pool, user_id: int) -> None:
    """Обновляет дату последней отправки ежедневного напоминания."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE daily_reminders SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )

# ----------------------- Запланированные ретроспективы -----------------------

async def upsert_scheduled_retrospective(
    pool: asyncpg.pool.Pool,
    user_id: int,
    scheduled_day: int,
    local_time: str,
    server_time: str,
    retrospective_type: str
) -> None:
    """
    Добавляет или обновляет запланированную ретроспективу.
    """
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO scheduled_retrospectives (user_id, scheduled_day, local_time, server_time, retrospective_type, last_sent, active)
                VALUES ($1, $2, $3::time, $4::time, $5, NULL, true)
                ON CONFLICT (user_id) DO UPDATE 
                SET scheduled_day = EXCLUDED.scheduled_day,
                    local_time = EXCLUDED.local_time,
                    server_time = EXCLUDED.server_time,
                    retrospective_type = EXCLUDED.retrospective_type,
                    active = true
                """,
                user_id, scheduled_day, local_time, server_time, retrospective_type
            )
        except Exception as e:
            logger.exception("Ошибка в upsert_scheduled_retrospective")
            raise

async def get_active_scheduled_retrospectives(pool: asyncpg.pool.Pool) -> List[asyncpg.Record]:
    """Получает список активных запланированных ретроспектив."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, scheduled_day, local_time, server_time, retrospective_type, last_sent FROM scheduled_retrospectives WHERE active = true"
        )

async def update_last_sent_scheduled_retrospective(pool: asyncpg.pool.Pool, user_id: int) -> None:
    """Обновляет дату последней отправки запланированной ретроспективы."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_retrospectives SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )

# ----------------------- Еженедельные ретроспективы (старый функционал) -----------------------

async def upsert_weekly_retrospective(pool: asyncpg.pool.Pool, user_id: int, retrospective_day: int) -> None:
    """Добавляет или обновляет напоминание о ретроспективе (старый функционал)."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """
                INSERT INTO weekly_retrospectives (user_id, retrospective_day, last_sent, active)
                VALUES ($1, $2, NULL, true)
                ON CONFLICT (user_id) DO UPDATE 
                SET retrospective_day = EXCLUDED.retrospective_day, active = true
                """,
                user_id, retrospective_day
            )
        except Exception as e:
            logger.exception("Ошибка в upsert_weekly_retrospective")
            raise

async def get_active_weekly_retrospectives(pool: asyncpg.pool.Pool) -> List[asyncpg.Record]:
    """Получает список активных ретроспектив (старый функционал)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, retrospective_day, last_sent FROM weekly_retrospectives WHERE active = true"
        )

async def update_last_sent_weekly(pool: asyncpg.pool.Pool, user_id: int) -> None:
    """Обновляет дату последней отправки ретроспективного напоминания (старый функционал)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE weekly_retrospectives SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )
