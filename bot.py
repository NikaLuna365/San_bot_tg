import os
import logging
import csv
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Загрузка переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Определение корневой директории проекта
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Определяет директорию, где находится скрипт
DATA_DIR = os.path.join(BASE_DIR, "data")  # Полный путь к папке data

async def start(update: Update, context: CallbackContext) -> None:
    if not update.message:
        return
    keyboard = [["Тест", "Ретроспектива", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

async def handle_message(update: Update, context: CallbackContext) -> None:
    if not update.message:
        return
    user_id = update.message.from_user.id
    user_choice = update.message.text
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Полный путь к файлу пользователя
    file_path = os.path.join(DATA_DIR, f"{user_id}.csv")

    try:
        # Проверка существования директории и ее создание при необходимости
        os.makedirs(DATA_DIR, exist_ok=True)
        
        # Проверка существования файла
        file_exists = os.path.isfile(file_path)

        # Запись данных в файл
        with open(file_path, mode="a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(["Дата и время", "Команда"])
            writer.writerow([timestamp, user_choice])

        await update.message.reply_text("Функционал ещё в разработке")

    except Exception as e:
        logger.error(f"Ошибка при работе с файлом {file_path}: {e}")
        await update.message.reply_text("Произошла ошибка при сохранении данных.")

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Exception while handling update {update}: {context.error}")

def main() -> None:
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
