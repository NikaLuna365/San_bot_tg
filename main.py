# main.py
import asyncio
import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    PicklePersistence,
    ContextTypes
)

# Импортируем наши модули
from db import create_db_pool
from constants import State, MAIN_MENU_KEYBOARD # Импортируем обновленную клавиатуру
from utils import get_now_utc

# Импортируем хендлеры
from handlers import common, test, retrospective, reminder, schedule, timezone

# Импортируем планировщик
import scheduler

# --- Настройка логирования ---
# (остается без изменений)
LOGS_DIR = "logs"
# ... (остальной код логирования) ...
logging.basicConfig(...)
logger = logging.getLogger(__name__)


# --- Функция пост-инициализации ---
# (остается без изменений)
async def post_init(application: Application):
    # ... (код post_init как в предыдущем ответе) ...
    logger.info("Запуск post_init...")
    # ... (инициализация пула БД) ...
    try:
        pool = await create_db_pool()
        if pool: application.bot_data["db_pool"] = pool; logger.info("Пул БД успешно инициализирован...")
        else: logger.critical("Не удалось создать пул БД в post_init..."); return
    except Exception as e: logger.critical(f"Критическая ошибка при инициализации пула БД в post_init: {e}", exc_info=True); return

    pool = application.bot_data.get("db_pool")
    if not pool: logger.error("Не удалось получить db_pool в post_init перед загрузкой задач..."); logger.info("Завершение post_init (без загрузки задач)."); return

    logger.info("Загрузка напоминаний и ретроспектив...")
    await asyncio.gather( scheduler.load_and_schedule_reminders(application), scheduler.load_and_schedule_retrospectives(application))
    logger.info("Завершение post_init.")


# --- Основная функция ---
def main() -> None:
    """Запускает бота."""
    logger.info("Запуск функции main...")
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN: logger.critical("Переменная окружения TELEGRAM_BOT_TOKEN не установлена!"); return

    # --- Persistence ---
    # (остается без изменений)
    PERSISTENCE_FILE = os.path.join("persistence", "bot_persistence.pickle")
    # ... (код создания папки и persistence) ...
    try: os.makedirs(os.path.dirname(PERSISTENCE_FILE), exist_ok=True); persistence = PicklePersistence(filepath=PERSISTENCE_FILE); logger.info(f"Используется PicklePersistence: {PERSISTENCE_FILE}")
    except OSError as e: logger.error(f"Не удалось создать директорию для persistence '{os.path.dirname(PERSISTENCE_FILE)}': {e}"); persistence = None; logger.warning(f"Запуск без сохранения состояний.")

    # --- Создание Application ---
    # (остается без изменений)
    builder = Application.builder().token(TOKEN)
    if persistence: builder.persistence(persistence)
    builder.connect_timeout(30).read_timeout(30).write_timeout(30)
    builder.post_init(post_init)
    app = builder.build()

    # --- Регистрация обработчиков ---
    logger.info("Регистрация обработчиков...")

    # --- Conversation Handlers ---
    # 1. Тест (без изменений)
    test_conv = ConversationHandler(...) # Как в пред. ответе
    # 2. Ретроспектива (мгновенная) - !!! ИЗМЕНЕНА ТОЧКА ВХОДА И STATES !!!
    retro_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective.retrospective_start)], # Запускается сразу выбор периода
         states={
             State.RETRO_PERIOD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_period_choice)],
             **retrospective.retro_open_states, # Состояния для открытых вопросов
             State.GEMINI_CHAT_RETRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_chat_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
         ],
         persistent=True, name="retro_conversation", allow_reentry=True
    )
    # 3. Планирование ретроспективы - !!! ИЗМЕНЕНА ТОЧКА ВХОДА !!!
    schedule_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Запланировать Ретро$"), schedule.schedule_start)], # Новый текст кнопки
         states={
             State.SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_day_handler)],
             State.SCHEDULE_TARGET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_target_time_handler)],
             State.SCHEDULE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_mode_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
         ],
         persistent=True, name="schedule_conversation", allow_reentry=True
    )
    # 4. Напоминания (без изменений, т.к. переход убран)
    reminder_conv = ConversationHandler(...) # Как в пред. ответе
    # 5. Установка часового пояса (без изменений)
    timezone_conv = ConversationHandler(...) # Как в пред. ответе

    # Добавляем все Conversation Handlers
    app.add_handler(test_conv)
    app.add_handler(retro_conv)
    app.add_handler(schedule_conv) # Точка входа теперь кнопка "Запланировать Ретро"
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
