import os
import logging
import csv
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

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
    
    file_path = f"data/{user_id}.csv"
    file_exists = os.path.isfile(file_path)
    
    with open(file_path, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Дата и время", "Команда"])
        writer.writerow([timestamp, user_choice])
    
    await update.message.reply_text("Функционал ещё в разработке")

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
