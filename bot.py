import os
import logging
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    update.message.reply_text("Выберите действие:", reply_markup=reply_markup)

def handle_message(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    user_choice = update.message.text
    
    os.makedirs("data/test", exist_ok=True)
    with open(f"data/test/{user_id}.txt", "w") as file:
        file.write(f"User ID: {user_id}\nВыбор: {user_choice}")
    
    update.message.reply_text("Функционал ещё в разработке")

def main() -> None:
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    app.run_polling()

if __name__ == "__main__":
    main()
