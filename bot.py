import os
import json
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    filters, CallbackContext
)

# Импорт официального SDK для Gemini от Google
from google.generativeai import GenerativeModel, configure, types

# ----------------------- Настройка логирования -----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------------- Константы для вопросов -----------------------
FIXED_QUESTIONS = [
    "1. Как вы оцениваете свое физическое состояние сейчас? (1 – очень плохое, 7 – отличное)",
    "2. Чувствуете ли вы себя бодрым/здоровым? (1 – ощущаю сильную усталость/болезнь, 7 – полностью бодрый и здоровый)",
    "3. Чувствуете ли вы себя энергичным? (1 – совсем нет сил, 7 – полон энергии)",
    "4. Чувствуете ли вы усталость или необходимость отдохнуть? (1 – крайне утомлен, 7 – полностью отдохнувший)",
    "5. Как вы оцениваете свое настроение сейчас? (1 – очень плохое, 7 – отличное)",
    "6. Чувствуете ли вы себя позитивно или негативно настроенным? (1 – крайне негативно, 7 – исключительно позитивно)"
]
OPEN_QUESTIONS = [
    "7. Какие три слова лучше всего описывают ваше текущее состояние?",
    "8. Что больше всего повлияло на ваше состояние сегодня?"
]

# ----------------------- Настройка директорий -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ----------------------- Состояния для ConversationHandler -----------------------
# Состояния прохождения теста (6 фиксированных вопросов + 2 открытых)
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
# Состояния для ретроспективы
RETRO_CHOICE, RETRO_SCHEDULE_DAY = range(8, 10)
# Состояния после теста: выбор дальнейших действий и режим общения с Gemini
AFTER_TEST_CHOICE, GEMINI_CHAT = range(10, 12)

# Глобальный словарь для запланированных ретроспектив (user_id -> weekday)
scheduled_retrospectives = {}

# ----------------------- Вспомогательные функции -----------------------

def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[str(i) for i in range(1, 8)]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

def build_gemini_prompt_for_test(test_answers: dict) -> str:
    """
    Формирует промпт для первоначального анализа теста.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент прошёл психологический тест (ежедневный тест). "
        "Проанализируйте его ответы и дайте развернутую интерпретацию его состояния, "
        "используя научные термины и принципы когнитивной психологии. "
        "Обратите внимание на открытые вопросы и, если необходимо, задайте уточняющие вопросы."
    )
    prompt = standard + "\n\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nСформируй развернутый анализ состояния клиента."
    logger.info(f"Промпт для теста:\n{prompt}")
    return prompt

# Новая функция для последующих вопросов в чате
def build_gemini_prompt_for_followup_chat(user_message: str, test_answers: dict) -> str:
    """
    Формирует промпт для последующих сообщений в режиме общения с Gemini.
    Здесь ответ должен базироваться на результатах сегодняшнего теста, не повторяя общий анализ.
    Также ИИ должен использовать HTML-разметку (например, <b>жирный</b>, <i>курсив</i>) для форматирования,
    а не Markdown со звёздочками – это соответствует рекомендациям Telegram Bot API :contentReference[oaicite:2]{index=2}, :contentReference[oaicite:3]{index=3}.
    Если форматирование невозможно, не применяйте его.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. Клиент уже получил анализ своего состояния на основе сегодняшнего теста. "
        "Используя только результаты теста, ответьте на следующий вопрос клиента, давая конкретные рекомендации и не повторяя общий обзор."
    )
    prompt = standard + "\n\nТекущие данные теста:\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nВопрос клиента: " + user_message + "\n"
    prompt += ("\nПожалуйста, форматируй ответ, используя HTML-теги (например, <b>жирный</b>, <i>курсив</i>), "
               "а не Markdown со звёздочками. Если форматирование невозможно, не выделяй текст.")
    logger.info(f"Промпт для последующих вопросов:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro(averages: dict, test_count: int) -> str:
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент прислал недельный тест. "
        "Проанализируйте изменения в его состоянии за неделю, учитывая открытые ответы, "
        "и дайте развернутую интерпретацию его эмоционального состояния с использованием научных терминов."
    )
    prompt = standard + "\n\n"
    prompt += f"Количество тестов: {test_count}\n"
    for category, avg in averages.items():
        prompt += f"{category}: {avg}\n"
    prompt += "\nСформируй подробный анализ с рекомендациями."
    logger.info(f"Промпт для ретроспективы:\n{prompt}")
    return prompt

def build_gemini_prompt_for_chat(user_message: str, test_answers: dict) -> str:
    """
    Первоначальный вариант промпта для чата (до внесения изменений).
    Сейчас не используется для последующих вопросов.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент недавно прошёл психологический тест. Ниже приведены его ответы. "
        "Проанализируйте их и ответьте на вопрос клиента, используя научные термины и принципы когнитивной психологии, "
        "при необходимости задайте уточняющие вопросы."
    )
    prompt = standard + "\n\nРезультаты последнего теста:\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nВопрос клиента: " + user_message + "\n"
    logger.info(f"Промпт для чата:\n{prompt}")
    return prompt

async def call_gemini_api(prompt: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY не задан в переменных окружения.")
        return {"interpretation": "Ошибка: API ключ не задан."}
    try:
        configure(api_key=api_key)
        model = GenerativeModel("gemini-2.0-flash")
        logger.info(f"Отправка запроса к Gemini API с промптом:\n{prompt}")
        
        gen_config = types.GenerationConfig(
            candidate_count=1,
            max_output_tokens=300,
            temperature=1.0
        )
        response = await asyncio.to_thread(
            lambda: model.generate_content([prompt], generation_config=gen_config)
        )
        logger.debug(f"Полный ответ от Gemini: {vars(response)}")
        
        if hasattr(response, "text") and response.text:
            interpretation = response.text
        elif hasattr(response, "content") and response.content:
            interpretation = response.content
        else:
            response_dict = vars(response)
            interpretation = response_dict.get("content", "Нет ответа от Gemini.")
        logger.info(f"Ответ от Gemini: {interpretation}")
        return {"interpretation": interpretation}
    except Exception as e:
        logger.error(f"Ошибка при вызове Gemini API: {e}")
        return {"interpretation": "Ошибка при обращении к Gemini API."}

# ----------------------- Обработчики команд и разговоров -----------------------

async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def test_start(update: Update, context: CallbackContext) -> int:
    context.user_data['test_answers'] = {}
    context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data['question_index'] = 0
    await update.message.reply_text(FIXED_QUESTIONS[0], reply_markup=build_fixed_keyboard())
    return TEST_FIXED_1

async def test_fixed_handler(update: Update, context: CallbackContext) -> int:
    index = context.user_data.get('question_index', 0)
    answer = update.message.text.strip()
    if answer not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text("Пожалуйста, выберите вариант от 1 до 7.", reply_markup=build_fixed_keyboard())
        return TEST_FIXED_1 + index
    context.user_data['test_answers'][f"fixed_{index+1}"] = answer
    index += 1
    context.user_data['question_index'] = index
    if index < len(FIXED_QUESTIONS):
        await update.message.reply_text(FIXED_QUESTIONS[index], reply_markup=build_fixed_keyboard())
        return TEST_FIXED_1 + index
    else:
        await update.message.reply_text(OPEN_QUESTIONS[0], reply_markup=ReplyKeyboardRemove())
        return TEST_OPEN_1

async def test_open_1(update: Update, context: CallbackContext) -> int:
    answer = update.message.text.strip()
    context.user_data['test_answers']['open_1'] = answer
    await update.message.reply_text(OPEN_QUESTIONS[1])
    return TEST_OPEN_2

async def test_open_2(update: Update, context: CallbackContext) -> int:
    answer = update.message.text.strip()
    context.user_data['test_answers']['open_2'] = answer

    user_id = update.message.from_user.id
    test_start_time = context.user_data.get("test_start_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
    filename = os.path.join(DATA_DIR, f"{user_id}_{test_start_time}.json")
    test_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_answers": context.user_data.get("test_answers", {})
    }
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(test_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Тестовые данные сохранены в {filename}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении теста: {e}")
        await update.message.reply_text("Произошла ошибка при сохранении данных теста.")
        return ConversationHandler.END

    context.user_data["last_test_answers"] = context.user_data.get("test_answers", {})
    prompt = build_gemini_prompt_for_test(context.user_data.get("test_answers", {}))
    gemini_response = await call_gemini_api(prompt)
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
    message = (
        f"Результат анализа:\n{interpretation}\n\n"
        "Теперь вы можете общаться с ИИ-психологом. Отправляйте свои сообщения, "
        "и они будут учитываться в контексте анализа вашего дня.\n"
        "Для выхода в главное меню нажмите кнопку «Главное меню»."
    )
    await update.message.reply_text(
        message,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

async def gemini_chat_handler(update: Update, context: CallbackContext) -> int:
    """
    Обработчик сообщений в режиме общения с ИИ.
    Теперь, если клиент задает дополнительный вопрос,
    формируется новый промпт, который основывается на результатах теста сегодняшнего дня,
    без повторного общего обзора, и с требованием использовать HTML-разметку.
    """
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        await update.message.reply_text("Возвращаемся в главное меню.", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return ConversationHandler.END
    test_answers = context.user_data.get("last_test_answers", {})
    # Используем новый промпт для последующих вопросов:
    prompt = build_gemini_prompt_for_followup_chat(user_input, test_answers)
    gemini_response = await call_gemini_api(prompt)
    answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "главное меню":
        await update.message.reply_text("Возвращаемся в главное меню.", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return ConversationHandler.END
    elif choice == "пообщаться с gemini":
        await update.message.reply_text(
            "Теперь вы можете общаться с ИИ-психологом. Отправляйте свои сообщения, "
            "и они будут учитываться в контексте анализа вашего дня.\n"
            "Для выхода в главное меню нажмите кнопку «Главное меню».",
            reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
        )
        return GEMINI_CHAT
    else:
        await update.message.reply_text("Пожалуйста, выберите: 'Главное меню' или 'Пообщаться с Gemini'.")
        return AFTER_TEST_CHOICE

# Обработчики ретроспективы и остальные функции остаются без изменений…
# (Код функций retrospective_start, retrospective_choice_handler, run_retrospective_now, help_command и error_handler не показан для краткости)

def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
        return

    app = Application.builder().token(TOKEN).build()

    # ConversationHandler для теста
    test_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тест$"), test_start)],
        states={
            TEST_FIXED_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_4: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_5: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_6: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_OPEN_1:  [MessageHandler(filters.TEXT & ~filters.COMMAND, test_open_1)],
            TEST_OPEN_2:  [MessageHandler(filters.TEXT & ~filters.COMMAND, test_open_2)],
            AFTER_TEST_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, after_test_choice_handler)],
            GEMINI_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, gemini_chat_handler)]
        },
        fallbacks=[CommandHandler("cancel", test_cancel)],
        allow_reentry=True
    )
    app.add_handler(test_conv_handler)

    # ConversationHandler для ретроспективы (без изменений)
    retro_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective_start)],
        states={
            RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_choice_handler)],
            RETRO_SCHEDULE_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_schedule_day)]
        },
        fallbacks=[CommandHandler("cancel", test_cancel)],
        allow_reentry=True
    )
    app.add_handler(retro_conv_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))

    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
