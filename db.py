import asyncpg
import os
from datetime import date

DATABASE_URL = os.getenv("DATABASE_URL")

async def create_db_pool():
    """Создаёт пул соединений с PostgreSQL."""
    return await asyncpg.create_pool(DATABASE_URL)

# Работа с ежедневными напоминаниями
async def upsert_daily_reminder(pool, user_id: int, reminder_time: str):
    """Добавляет или обновляет напоминание о ежедневном тесте."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_reminders (user_id, reminder_time, last_sent, active)
            VALUES ($1, $2, NULL, true)
            ON CONFLICT (user_id) DO UPDATE 
            SET reminder_time = EXCLUDED.reminder_time, active = true
            """,
            user_id, reminder_time
        )

async def get_active_daily_reminders(pool):
    """Получает список активных ежедневных напоминаний."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, reminder_time, last_sent FROM daily_reminders WHERE active = true"
        )

async def update_last_sent_daily(pool, user_id: int):
    """Обновляет дату последней отправки ежедневного напоминания."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE daily_reminders SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )

# Работа с еженедельными ретроспективами
async def upsert_weekly_retrospective(pool, user_id: int, retrospective_day: int):
    """Добавляет или обновляет напоминание о ретроспективе."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO weekly_retrospectives (user_id, retrospective_day, last_sent, active)
            VALUES ($1, $2, NULL, true)
            ON CONFLICT (user_id) DO UPDATE 
            SET retrospective_day = EXCLUDED.retrospective_day, active = true
            """,
            user_id, retrospective_day
        )

async def get_active_weekly_retrospectives(pool):
    """Получает список активных напоминаний о ретроспективе."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, retrospective_day, last_sent FROM weekly_retrospectives WHERE active = true"
        )

async def update_last_sent_weekly(pool, user_id: int):
    """Обновляет дату последней отправки ретроспективного напоминания."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE weekly_retrospectives SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )
