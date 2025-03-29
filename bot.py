import asyncio
import logging
import os

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    PicklePersistence # Рекомендуется для сохранения user_data/chat_data между перезапусками
)

# Импортируем наши модули
from db import create_db_pool # или db_utils
from constants import State, MAIN_MENU_KEYBOARD # и другие нужные константы/клавиатуры
import handlers.common
import handlers.test
import handlers.retrospective
import handlers.reminder
import handlers.schedule
import scheduler # Для функции post_init
# Добавьте хендлер для установки таймзоны
import handlers.timezone # Нужно будет создать handlers/timezone.py

# Настройка логирования (оставить как есть)
logging.basicConfig(...)
logger = logging.getLogger(__name__)

async def post_init(application: Application):
    """Выполняется после инициализации бота."""
    await scheduler.schedule_active_reminders(application) # Передаем application
    await scheduler.schedule_active_retrospectives(application) # Передаем application
    logger.info("Активные напоминания и ретроспективы загружены.")

def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан.")
        return

    # PicklePersistence для сохранения данных между перезапусками (опционально, но полезно)
    persistence = PicklePersistence(filepath="bot_persistence")

    app = Application.builder().token(TOKEN).persistence(persistence).post_init(post_init).build()

    pool = loop.run_until_complete(create_db_pool())
    app.bot_data["db_pool"] = pool
    # Если используете PicklePersistence, можно удалить ручное добавление pool в bot_data,
    # т.к. он сохранится автоматически, но явное добавление надежнее при первом запуске.

    # --- Регистрация обработчиков ---
    # Используем функции или переменные, импортированные из модулей handlers

    # Общие команды
    app.add_handler(CommandHandler("start", handlers.common.start))
    app.add_handler(CommandHandler("help", handlers.common.help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), handlers.common.help_command))
    # Добавляем обработчик для новой команды установки таймзоны
    app.add_handler(CommandHandler("set_timezone", handlers.timezone.set_timezone_start)) # Пример

    # Обработчик для кнопки "Главное меню" (может быть в common.py)
    app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), handlers.common.exit_to_main))

    # --- Conversation Handlers ---
    # Тест
    test_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тест$"), handlers.test.test_start)],
        states={
            State.TEST_FIXED_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.test.test_fixed_handler)],
            # ... остальные состояния теста из handlers.test ...
            State.GEMINI_CHAT_TEST: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.test.gemini_chat_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", handlers.common.cancel), # Общий cancel
            MessageHandler(filters.Regex("^(?i)главное меню$"), handlers.common.exit_to_main) # Общий выход
        ],
        persistent=True, name="test_conversation" # Для PicklePersistence
    )
    app.add_handler(test_conv)

    # Ретроспектива (мгновенная)
    retro_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), handlers.retrospective.retrospective_start)],
         states={
             State.RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.retrospective.retrospective_choice_handler)],
             # ... остальные состояния ретро из handlers.retrospective ...
             State.GEMINI_CHAT_RETRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.retrospective.retrospective_chat_handler)],
         },
         fallbacks=[
             CommandHandler("cancel", handlers.common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), handlers.common.exit_to_main)
         ],
         persistent=True, name="retro_conversation"
    )
    app.add_handler(retro_conv)

    # Планирование ретроспективы
    schedule_conv = ConversationHandler(
         # Точка входа может измениться, если retrospective_choice_handler теперь в другом файле
         # Возможно, понадобится отдельная кнопка/команда для планирования
         entry_points=[MessageHandler(filters.Regex("^Запланировать ретроспективу$"), handlers.schedule.schedule_start)], # Пример новой точки входа
         states={
             State.RETRO_SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.schedule.retro_schedule_day_handler)],
             # ... остальные состояния планирования из handlers.schedule ...
         },
         fallbacks=[
             CommandHandler("cancel", handlers.common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), handlers.common.exit_to_main)
         ],
         persistent=True, name="schedule_conversation"
    )
    app.add_handler(schedule_conv)

    # Напоминания
    reminder_conv = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Напоминание$"), handlers.reminder.reminder_start)],
         states={
             State.REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handlers.reminder.reminder_daily_test)],
             # ... остальные состояния напоминаний из handlers.reminder ...
         },
         fallbacks=[
             CommandHandler("cancel", handlers.common.cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), handlers.common.exit_to_main)
         ],
         persistent=True, name="reminder_conversation"
    )
    app.add_handler(reminder_conv)

    # Диалог установки часового пояса (добавить позже, когда будет handlers/timezone.py)
    # timezone_conv = ConversationHandler(...)
    # app.add_handler(timezone_conv)

    # Обработчик ошибок
    app.add_error_handler(handlers.common.error_handler) # Переместить error_handler в common.py

    # Запуск бота
    app.run_polling()

if __name__ == "__main__":
    main()
