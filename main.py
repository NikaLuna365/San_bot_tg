# main.py
import asyncio
import logging
import os

from telegram import Update
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, MessageHandler,
    ConversationHandler, filters, PicklePersistence, ContextTypes
)

# Импорты проекта
from db import create_db_pool
# !!! ИЗМЕНЕНО: Добавлена новая кнопка в MAIN_MENU_KEYBOARD !!!
from constants import State, MAIN_MENU_KEYBOARD
from utils import get_now_utc
from handlers import common, test, retrospective, reminder, schedule, timezone
import scheduler

# --- Настройка логирования ---
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILENAME = os.path.join(LOGS_DIR, "bot.log")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILENAME, encoding='utf-8')]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# --- Функция пост-инициализации ---
# (остается без изменений)
async def post_init(application: Application):
    logger.info("Запуск post_init...")
    try:
        pool = await create_db_pool()
        if pool: application.bot_data["db_pool"] = pool; logger.info("Пул БД инициализирован.")
        else: logger.critical("Не удалось создать пул БД в post_init."); return
    except Exception as e: logger.critical(f"Критическая ошибка инициализации пула БД: {e}", exc_info=True); return

    pool = application.bot_data.get("db_pool")
    if not pool: logger.error("Пул БД не найден перед загрузкой задач."); logger.info("Завершение post_init (без загрузки задач)."); return

    logger.info("Загрузка напоминаний и ретроспектив...")
    await asyncio.gather(
        scheduler.load_and_schedule_reminders(application),
        scheduler.load_and_schedule_retrospectives(application)
    )
    logger.info("Завершение post_init.")

# --- Основная функция ---
def main() -> None:
    logger.info("Запуск функции main...")
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN: logger.critical("TELEGRAM_BOT_TOKEN не установлен!"); return

    # --- Persistence ---
    PERSISTENCE_FILE = os.path.join("persistence", "bot_persistence.pickle")
    persistence = None # Инициализируем как None
    try:
        os.makedirs(os.path.dirname(PERSISTENCE_FILE), exist_ok=True)
        persistence = PicklePersistence(filepath=PERSISTENCE_FILE)
        logger.info(f"Используется PicklePersistence: {PERSISTENCE_FILE}")
    except OSError as e:
        logger.error(f"Не удалось создать директорию для persistence '{os.path.dirname(PERSISTENCE_FILE)}': {e}")
        logger.warning(f"Запуск без сохранения состояний.")

    # --- Создание Application ---
    builder = Application.builder().token(TOKEN)
    if persistence: builder.persistence(persistence) # Добавляем, только если создан
    builder.connect_timeout(30).read_timeout(30).write_timeout(30)
    builder.post_init(post_init)
    app = builder.build()

    # --- Регистрация обработчиков ---
    logger.info("Регистрация обработчиков...")

    # --- Conversation Handlers ---
    # 1. Тест (без изменений)
    test_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тест$"), test.test_start)],
        states={
            **{State(State.TEST_FIXED_1.value + i): [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_fixed_handler)] for i in range(6)},
            State.TEST_OPEN_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_open_1)],
            State.TEST_OPEN_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_open_2)],
            State.GEMINI_CHAT_TEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.gemini_chat_handler)]
        },
        fallbacks=[ CommandHandler("cancel", common.cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)],
        persistent=True, name="test_conversation", allow_reentry=True
    )

    # 2. Ретроспектива (мгновенная)
    # !!! ИЗМЕНЕНО: Точка входа и начальное состояние !!!
    retro_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective.retrospective_start)], # Точка входа та же
         states={
             # !!! Убрано State.RETRO_CHOICE !!!
             State.RETRO_PERIOD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_period_choice)], # Теперь это первое состояние
             **retrospective.retro_open_states,
             State.GEMINI_CHAT_RETRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_chat_handler)]
         },
         fallbacks=[ CommandHandler("cancel", common.cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)],
         persistent=True, name="retro_conversation", allow_reentry=True
    )

    # 3. Планирование ретроспективы
    # !!! ИЗМЕНЕНО: Точка входа по кнопке "Настроить Расписание" !!!
    schedule_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Настроить Расписание$"), schedule.schedule_start)],
         states={
             # State.SCHEDULE_START больше не нужен как отдельное состояние, т.к. schedule_start сразу проверяет TZ и переходит
             State.SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_day_handler)],
             State.SCHEDULE_TARGET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_target_time_handler)],
             State.SCHEDULE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_mode_handler)]
         },
         fallbacks=[ CommandHandler("cancel", common.cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)],
         persistent=True, name="schedule_conversation", allow_reentry=True
    )

    # 4. Напоминания (ежедневный тест)
    # !!! ИЗМЕНЕНО: Упрощен, т.к. только один тип напоминания !!!
    reminder_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Напоминание$"), reminder.reminder_start)],
         states={
             State.REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder.reminder_choice_handler)],
             State.REMINDER_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder.reminder_set_daily_time)],
         },
         fallbacks=[ CommandHandler("cancel", common.cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)],
         persistent=True, name="reminder_conversation", allow_reentry=True
    )

    # 5. Установка часового пояса (без изменений)
    timezone_conv = ConversationHandler(
        entry_points=[ CommandHandler("set_timezone", timezone.set_timezone_start), MessageHandler(filters.Regex("^Настроить часовой пояс$"), timezone.set_timezone_start)],
        states={ State.SET_TIMEZONE_ASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, timezone.set_timezone_receive)] },
        fallbacks=[ CommandHandler("cancel", common.cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)],
        persistent=True, name="timezone_conversation"
    )

    # Добавляем все Conversation Handlers
    app.add_handler(test_conv)
    app.add_handler(retro_conv)
    app.add_handler(schedule_conv) # Добавляем новый хендлер планирования
    app.add_handler(reminder_conv)
    app.add_handler(timezone_conv)

    # --- Обычные обработчики ---
    # (остаются без изменений)
    app.add_handler(CommandHandler("start", common.start))
    app.add_handler(CommandHandler("help", common.help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), common.help_command))
    app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main))
    # --- Обработчик ошибок ---
    app.add_error_handler(common.error_handler)

    # --- Запуск бота ---
    logger.info("Запуск бота (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Бот остановлен.")

if __name__ == "__main__":
    main()
