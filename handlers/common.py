# handlers/common.py
import logging
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

# --- ИЗМЕНЕНО: Абсолютный импорт ---
from constants import MAIN_MENU_KEYBOARD

logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и главное меню."""
    user = update.effective_user
    logger.info(f"Пользователь {user.id} ({user.username}) запустил бота.")
    await update.message.reply_html(
        f"Привет, {user.mention_html()}!\n\n"
        "Я помогу тебе отслеживать твое состояние и проводить рефлексию. "
        "Выбери действие:",
        reply_markup=MAIN_MENU_KEYBOARD
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет справочное сообщение."""
    logger.info(f"Пользователь {update.effective_user.id} запросил помощь.")
    help_text = (
        "<b>Основные команды:</b>\n\n"
        "🗓 <b>Тест</b> – пройди короткий ежедневный опрос для оценки твоего состояния.\n\n"
        "📊 <b>Ретроспектива</b> – проанализируй динамику состояния за 1 или 2 недели или настрой регулярную ретроспективу.\n\n"
        "⏰ <b>Напоминание</b> – настрой ежедневное напоминание о прохождении теста.\n\n"
        "⚙️ <b>Настроить часовой пояс</b> (/set_timezone) – установи свой часовой пояс для корректной работы напоминаний.\n\n"
        "🆘 <b>Помощь</b> – отобразить это сообщение.\n\n"
        "В любой момент доступна кнопка «Главное меню» для возврата."
    )
    await update.message.reply_html(help_text, reply_markup=MAIN_MENU_KEYBOARD)

async def exit_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершает текущий диалог и возвращает в главное меню."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} возвращается в главное меню.")
    # Очищаем user_data, если нужно (но с PicklePersistence это может быть нежелательно)
    # context.user_data.clear()
    await update.message.reply_text(
        "Хорошо, возвращаемся в главное меню.",
        reply_markup=MAIN_MENU_KEYBOARD
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет текущую операцию (диалог)."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} отменил текущую операцию.")
    await update.message.reply_text(
        "Операция отменена.",
        reply_markup=MAIN_MENU_KEYBOARD # Возвращаем главное меню
    )
    # context.user_data.clear() # Опционально
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует ошибки, вызванные обновлениями."""
    logger.error(f"Исключение при обработке обновления {update}:", exc_info=context.error)
    # Можно добавить отправку уведомления администратору или пользователю
    # if isinstance(update, Update) and update.effective_chat:
    #     await context.bot.send_message(
    #         chat_id=update.effective_chat.id,
    #         text="Произошла внутренняя ошибка. Попробуйте позже."
    #     )
