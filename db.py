# db.py
import asyncpg
import os
from datetime import date, time, datetime # Добавлен datetime
from typing import List, Dict, Any, Optional
import logging
import json # Добавлен json для работы с JSONB

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
        # Можно добавить проверку/создание таблиц здесь при желании,
        # но мы уже сделали это вручную.
        # await setup_database(pool)
        return pool
    except Exception as e:
        logger.exception("Ошибка при создании пула соединений с БД.")
        return None

# --- Настройки Пользователя (Часовой пояс) ---
# Эти функции остаются без изменений, т.к. таблица user_settings подходит

async def set_user_timezone(pool: asyncpg.pool.Pool, user_id: int, timezone: str) -> None:
    """Сохраняет или обновляет часовой пояс пользователя."""
    async with pool.acquire() as conn:
        try:
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
    async with pool.acquire() as conn:
        try:
            result = await conn.fetchval(
                "SELECT timezone FROM user_settings WHERE user_id = $1",
                user_id
            )
            return result
        except Exception as e:
            logger.exception(f"Ошибка при получении часового пояса для {user_id}")
            return None

# --- Ежедневные напоминания ---
# Эти функции остаются без изменений, т.к. таблица daily_reminders подходит

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
    """Получает список активных ежедневных напоминаний."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, target_local_time, timezone FROM daily_reminders WHERE active = true"
        )

# --- Запланированные ретроспективы ---
# Эти функции остаются без изменений, т.к. таблица scheduled_retrospectives подходит

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
    """Получает список активных запланированных ретроспектив."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT user_id, scheduled_day, target_local_time, timezone, retrospective_type
            FROM scheduled_retrospectives WHERE active = true
            """
        )

# --- НОВОЕ: Функции для сохранения/чтения данных тестов ---

async def save_test_result(
    pool: asyncpg.pool.Pool,
    user_id: int,
    timestamp: datetime,
    weekday: int,
    fixed_answers: Dict[str, Any],
    open_answers: Dict[str, Any],
    interpretation: Optional[str]
) -> Optional[int]:
    """Сохраняет результаты одного теста в БД и возвращает test_id."""
    # Преобразуем словари в JSON строки для записи в JSONB
    fixed_answers_json = json.dumps(fixed_answers, ensure_ascii=False)
    open_answers_json = json.dumps(open_answers, ensure_ascii=False)

    async with pool.acquire() as conn:
        try:
            # Используем RETURNING test_id для получения ID вставленной записи
            test_id = await conn.fetchval(
                """
                INSERT INTO tests (user_id, timestamp, weekday, fixed_answers, open_answers, interpretation)
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
                RETURNING test_id
                """,
                user_id, timestamp, weekday, fixed_answers_json, open_answers_json, interpretation
            )
            logger.info(f"Результат теста для user {user_id} сохранен в БД с test_id={test_id}")
            return test_id
        except Exception as e:
            logger.exception(f"Ошибка при сохранении результата теста для user {user_id} в БД")
            return None

async def update_test_interpretation(pool: asyncpg.pool.Pool, test_id: int, interpretation: str) -> None:
    """Обновляет поле interpretation для существующего теста."""
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "UPDATE tests SET interpretation = $1 WHERE test_id = $2",
                interpretation, test_id
            )
            logger.info(f"Интерпретация для test_id={test_id} обновлена в БД.")
        except Exception as e:
            logger.exception(f"Ошибка при обновлении интерпретации для test_id={test_id} в БД")
            # Не пробрасываем ошибку дальше, т.к. это не критично для пользователя

async def get_test_results_for_period(pool: asyncpg.pool.Pool, user_id: int, start_date: datetime, end_date: datetime) -> List[asyncpg.Record]:
    """Получает результаты тестов пользователя за указанный период UTC."""
    async with pool.acquire() as conn:
        try:
            # Выбираем только нужные поля для анализа ретроспективы
            # asyncpg автоматически десериализует JSONB в словари Python
            results = await conn.fetch(
                """
                SELECT timestamp, fixed_answers, open_answers
                FROM tests
                WHERE user_id = $1 AND timestamp >= $2 AND timestamp <= $3
                ORDER BY timestamp ASC
                """,
                user_id, start_date, end_date
            )
            logger.info(f"Найдено {len(results)} тестов для user {user_id} в БД за период {start_date.date()} - {end_date.date()}")
            return results
        except Exception as e:
            logger.exception(f"Ошибка при получении результатов тестов для user {user_id} из БД")
            return [] # Возвращаем пустой список при ошибке

# --- НОВОЕ: Функции для сохранения/чтения данных ретроспектив ---

async def save_retrospective_result(
    pool: asyncpg.pool.Pool,
    user_id: int,
    timestamp: datetime,
    period_days: int,
    test_count: int,
    averages: Dict[str, Optional[float]],
    open_answers: Dict[str, Any],
    interpretation: Optional[str]
) -> Optional[int]:
    """Сохраняет результаты ретроспективы в БД."""
    averages_json = json.dumps(averages, ensure_ascii=False, default=str) # default=str для None
    open_answers_json = json.dumps(open_answers, ensure_ascii=False)

    async with pool.acquire() as conn:
        try:
            retro_id = await conn.fetchval(
                """
                INSERT INTO retrospectives (user_id, timestamp, period_days, test_count, averages, open_answers, interpretation)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                RETURNING retro_id
                """,
                user_id, timestamp, period_days, test_count, averages_json, open_answers_json, interpretation
            )
            logger.info(f"Результат ретроспективы для user {user_id} сохранен в БД с retro_id={retro_id}")
            return retro_id
        except Exception as e:
            logger.exception(f"Ошибка при сохранении результата ретроспективы для user {user_id} в БД")
            return None

# (Функции чтения ретроспектив пока не требуются, но можно добавить по аналогии, если нужно будет их где-то показывать)
