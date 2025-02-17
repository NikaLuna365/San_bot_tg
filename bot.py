import os
import sys
import logging
import random  # <-- для генерации вероятности
from dotenv import load_dotenv
from charset_normalizer import from_path
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import google.generativeai as genai

# 1. Загрузка переменных окружения
load_dotenv()

api_key = os.getenv("API_KEY")
telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")

if not api_key or not telegram_token:
    print("Не удалось загрузить ключи API из .env. Проверьте содержимое файла!")
    sys.exit(1)

# 2. Настройка логирования
if not os.path.exists("logs"):
    os.makedirs("logs")

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

file_handler = logging.FileHandler('logs/bot.log', encoding='utf-8')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 3. Настройка API
genai.configure(api_key=api_key)
logger.info("API настроен успешно.")

# 4. Чтение файла data.txt (если есть дополнительные материалы)
file_path = "./data.txt"
try:
    result = from_path(file_path).best()
    if result:
        combined_text = str(result)
        logger.info("Текст из файла data.txt успешно прочитан.")
    else:
        combined_text = ""
        logger.warning("Не удалось прочитать файл data.txt. Возможно, файл пуст.")
except FileNotFoundError:
    combined_text = ""
    logger.warning("Файл data.txt не найден.")
except Exception as e:
    combined_text = ""
    logger.critical(f"Ошибка при чтении файла data.txt: {e}")

# 5. Основной «русский» контекст для ИИ
# Здесь мы описываем всю логику поведения, стиль общения и т.д.
russian_lushok_context = f"""
Ты выступаешь в роли «Лушок» (Nikolai Lu). Твоя задача — общаться в характерном стиле:
1) Самоирония и неформальный юмор (шутки, ирония над повседневными вещами).
2) Философские отступления (глубокие размышления на бытовые и общественные темы).
3) Лёгкая саркастичность и критика (но без чрезмерной грубости).
4) Эмоциональная открытость (говоришь о чувствах, но без лишней пошлости).
5) Упоминание реальных примеров из жизни, науки, культуры.
6) Логичные, разнообразные ответы, без навязчивых повторов.

ВНИМАНИЕ: В коде бота заложена логика:
- Если пользователь отвечает (reply) на сообщение бота, бот отвечает с вероятностью 100%.
- Если пользователь просто пишет новое сообщение (не reply), бот отвечает с вероятностью 20%.

То есть, если тебе всё же пришёл запрос на генерацию ответа, значит проверка «вероятности» в коде уже пройдена. Твоя задача — всегда формировать осмысленный ответ, соответствующий стилю «Лушок».

Также у нас есть дополнительный контент из файла data.txt, который может обогащать ответы:
{combined_text}
"""

# 6. Хранение последних 5 сообщений в каждом чате
# Ключ: chat_id, значение: список { "user": user_id, "text": "..." }
chat_context = {}

# 7. Формирование промпта с учётом последних 5 сообщений чата
def build_prompt(chat_id: int) -> str:
    messages = chat_context.get(chat_id, [])
    # Берём максимум 5 последних
    last_five = messages[-5:]
    
    # Собираем «контекст» из последних сообщений
    conversation_part = ""
    for msg in last_five:
        conversation_part += f"Пользователь {msg['user']}: {msg['text']}\n"
    
    # Общий промпт
    prompt = (
        f"{russian_lushok_context}\n\n"
        "Вот последние сообщения в чате (не более 5):\n"
        f"{conversation_part}\n"
        "Продолжай диалог в стиле Лушок, учитывая всю логику выше."
    )
    return prompt

# 8. Обработчики
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    reply_keyboard = [
        ["Философия", "Политика"],
        ["Критика общества", "Личные истории"],
    ]
    await update.message.reply_text(
        "Привет! Я бот, пытающийся общаться в стиле Николая Лу.\n\n"
        "Можешь выбрать тему или просто напиши свой вопрос.",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text_received = update.message.text.strip()
    
    # Сохраняем сообщение в истории чата
    if chat_id not in chat_context:
        chat_context[chat_id] = []
    chat_context[chat_id].append({"user": user_id, "text": text_received})
    # Обрезаем, чтобы хранилось не более 5 сообщений
    if len(chat_context[chat_id]) > 5:
        chat_context[chat_id].pop(0)

    # Проверяем, является ли сообщение ответом на сообщение бота
    is_reply_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
        and update.message.reply_to_message.from_user.id == context.bot.id
    )

    # Логика вероятности: если не reply, то 20%, иначе 100%
    if not is_reply_to_bot:
        # Случайное число от 0 до 1
        chance = random.random()
        # Если больше 0.2 => не отвечаем
        if chance > 0.2:
            return

    # Если дошли сюда — бот генерирует ответ
    prompt = build_prompt(chat_id)
    
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        gen_response = model.generate_content(prompt)
        response = gen_response.text.strip() if gen_response and gen_response.text else "Извините, у меня нет ответа."
    except Exception as e:
        logger.error(f"Ошибка при генерации ответа: {e}", exc_info=True)
        response = "Произошла ошибка. Попробуйте ещё раз."
    
    # Отправляем ответ
    await update.message.reply_text(response)

# 9. Запуск бота
def main() -> None:
    try:
        application = Application.builder().token(telegram_token).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        logger.info("Бот запущен и готов к работе через polling.")
        application.run_polling()
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
