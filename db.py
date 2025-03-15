import asyncpg
import os
from datetime import date

DATABASE_URL = os.getenv("DATABASE_URL")

async def create_db_pool():
    """Создаёт пул соединений с PostgreSQL."""
    return await asyncpg.create_pool(DATABASE_URL)

# ----------------------- Ежедневные напоминания -----------------------

async def upsert_daily_reminder(pool, user_id: int, reminder_time):
    """Добавляет или обновляет напоминание о ежедневном тесте.
    
    Параметр reminder_time приводится к типу TIME с помощью $2::time.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO daily_reminders (user_id, reminder_time, last_sent, active)
            VALUES ($1, $2::time, NULL, true)
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

# ----------------------- Запланированные ретроспективы -----------------------

async def upsert_scheduled_retrospective(pool, user_id: int, scheduled_day: int, local_time, server_time, retrospective_type: str):
    """
    Добавляет или обновляет запланированную ретроспективу.

    Параметры:
      - scheduled_day: целое число от 0 до 6, соответствующее дню недели (0 – Понедельник, 6 – Воскресенье).
      - local_time: локальное время ретроспективы, введённое пользователем (объект time).
      - server_time: вычисленное серверное время для проведения ретроспективы (объект time).
      - retrospective_type: тип ретроспективы, например, 'weekly' для еженедельной или 'biweekly' для двухнедельной.
    
    В SQL‑запросе local_time и server_time приводятся к типу TIME с помощью $3::time и $4::time.
    """
    async with pool.acquire() as conn:
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

async def get_active_scheduled_retrospectives(pool):
    """Получает список активных запланированных ретроспектив."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, scheduled_day, local_time, server_time, retrospective_type, last_sent FROM scheduled_retrospectives WHERE active = true"
        )

async def update_last_sent_scheduled_retrospective(pool, user_id: int):
    """Обновляет дату последней отправки запланированной ретроспективы."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scheduled_retrospectives SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )

# ----------------------- Еженедельные ретроспективы (старый функционал) -----------------------

async def upsert_weekly_retrospective(pool, user_id: int, retrospective_day: int):
    """Добавляет или обновляет напоминание о ретроспективе (старый функционал)."""
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
    """Получает список активных ретроспектив (старый функционал)."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT id, user_id, retrospective_day, last_sent FROM weekly_retrospectives WHERE active = true"
        )

async def update_last_sent_weekly(pool, user_id: int):
    """Обновляет дату последней отправки ретроспективного напоминания (старый функционал)."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE weekly_retrospectives SET last_sent = $1 WHERE user_id = $2",
            date.today(), user_id
        )
