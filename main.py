# main.py
import asyncio
import logging
import os

from telegram.ext import (
    Application,
    ApplicationBuilder, # Используем ApplicationBuilder
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    PicklePersistence, # Для сохранения данных
    ContextTypes
)

# Импортируем наши модули
from db import create_db_pool
from constants import State, MAIN_MENU_KEYBOARD
from utils import get_now_utc # Пример использования утилиты

# Импортируем хендлеры
from handlers import common, test, retrospective, reminder, schedule, timezone

# Импортируем планировщик
import scheduler

# --- Настройка логирования ---
# Убедитесь, что папка logs существует
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)
LOG_FILENAME = os.path.join(LOGS_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO, # Уровень логирования (можно INFO или DEBUG)
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(), # Вывод в консоль
        logging.FileHandler(LOG_FILENAME, encoding='utf-8') # Вывод в файл
    ]
)
# Устанавливаем уровень для библиотеки telegram, чтобы не было слишком много спама
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)

logger = logging.getLogger(__name__)

# --- Функция пост-инициализации ---
async def post_init(application: Application):
    """Загружает и планирует задачи после инициализации бота."""
    logger.info("Запуск post_init...")
    pool = application.bot_data.get("db_pool")
    if not pool:
         logger.error("Не удалось получить db_pool в post_init. Задачи не будут загружены.")
         return
    # Запускаем загрузку параллельно
    await asyncio.gather(
        scheduler.load_and_schedule_reminders(application),
        scheduler.load_and_schedule_retrospectives(application)
    )
    logger.info("Завершение post_init.")

# --- Основная функция ---
def main() -> None:
    """Запускает бота."""
    logger.info("Запуск функции main...")
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.critical("Переменная окружения TELEGRAM_BOT_TOKEN не установлена!")
        return

    # --- Persistence ---
    # Используем PicklePersistence для сохранения user_data, chat_data, bot_data
    persistence = PicklePersistence(filepath="bot_persistence.pickle")
    logger.info(f"Используется PicklePersistence: bot_persistence.pickle")

    # --- Создание Application ---
    builder = Application.builder().token(TOKEN).persistence(persistence)
    # Увеличиваем таймауты для обработки длительных запросов Gemini
    builder.connect_timeout(30).read_timeout(30).write_timeout(30)
    builder.post_init(post_init)
    app = builder.build()

    # --- Инициализация пула БД и сохранение в bot_data ---
    # Запускаем в event loop, который будет использоваться приложением
    try:
        pool = asyncio.get_event_loop().run_until_complete(create_db_pool())
        if pool:
            app.bot_data["db_pool"] = pool
            logger.info("Пул БД успешно инициализирован и сохранен в bot_data.")
        else:
            logger.critical("Не удалось создать пул БД. Бот не может работать без БД.")
            return
    except Exception as e:
         logger.critical(f"Критическая ошибка при инициализации пула БД: {e}", exc_info=True)
         return


    # --- Регистрация обработчиков ---
    logger.info("Регистрация обработчиков...")

    # --- Conversation Handlers ---
    # 1. Тест
    test_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тест$"), test.test_start)],
        states={
            # Используем dict comprehension для состояний TEST_FIXED_1..6
            **{State(State.TEST_FIXED_1.value + i): [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_fixed_handler)] for i in range(6)},
            State.TEST_OPEN_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_open_1)],
            State.TEST_OPEN_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.test_open_2)],
            State.GEMINI_CHAT_TEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, test.gemini_chat_handler)]
        },
        fallbacks=[
            CommandHandler("cancel", common.cancel),
            MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
        ],
        persistent=True, name="test_conversation",
        allow_reentry=True # Позволяет войти в диалог снова по entry_point
    )

    # 2. Ретроспектива (мгновенная)
    retro_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective.retrospective_start)],
         states={
             State.RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_choice_handler)],
             State.RETRO_PERIOD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_period_choice)],
             # Добавляем состояния для открытых вопросов из retrospective.py
             **retrospective.retro_open_states,
             State.GEMINI_CHAT_RETRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective.retrospective_chat_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
         ],
         persistent=True, name="retro_conversation",
         allow_reentry=True
    )

    # 3. Планирование ретроспективы
    schedule_conv = ConversationHandler(
         # Используем отдельную точку входа, если кнопка "Запланировать" в другом меню
         entry_points=[MessageHandler(filters.Regex("^Запланировать$"), schedule.schedule_start)], # Пример, если кнопка называется так
         states={
             State.SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_day_handler)],
             State.SCHEDULE_TARGET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_target_time_handler)],
             State.SCHEDULE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule.schedule_mode_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
         ],
         persistent=True, name="schedule_conversation",
         allow_reentry=True
    )

    # 4. Напоминания
    reminder_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Напоминание$"), reminder.reminder_start)],
         states={
             State.REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder.reminder_choice_handler)],
             State.REMINDER_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder.reminder_set_daily_time)],
             # REMINDER_DAILY_REMIND больше не нужен, время устанавливается сразу
         },
         fallbacks=[
             CommandHandler("cancel", common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
         ],
         persistent=True, name="reminder_conversation",
         allow_reentry=True
    )

    # 5. Установка часового пояса
    timezone_conv = ConversationHandler(
        entry_points=[
            CommandHandler("set_timezone", timezone.set_timezone_start),
            MessageHandler(filters.Regex("^Настроить часовой пояс$"), timezone.set_timezone_start)
            ],
        states={
            State.SET_TIMEZONE_ASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, timezone.set_timezone_receive)]
        },
        fallbacks=[
             CommandHandler("cancel", common.cancel), # Можно использовать общий cancel
             MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main)
        ],
        persistent=True, name="timezone_conversation"
        # allow_reentry не так важен здесь
    )

    # Добавляем все Conversation Handlers
    app.add_handler(test_conv)
    app.add_handler(retro_conv)
    app.add_handler(schedule_conv) # Добавить, если используется отдельная точка входа
    app.add_handler(reminder_conv)
    app.add_handler(timezone_conv)

    # --- Обычные обработчики ---
    app.add_handler(CommandHandler("start", common.start))
    app.add_handler(CommandHandler("help", common.help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), common.help_command))

    # Обработчик "Главное меню" должен идти после Conversation Handlers,
    # чтобы не перехватывать ввод внутри диалогов, если там нет этого fallback
    app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), common.exit_to_main))

    # --- Обработчик ошибок ---
    app.add_error_handler(common.error_handler)

    # --- Запуск бота ---
    logger.info("Запуск бота (polling)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES) # Получаем все типы обновлений

    logger.info("Бот остановлен.")


if __name__ == "__main__":
    main()
