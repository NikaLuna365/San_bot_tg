import os
import json
import logging
import asyncio
from calendar import monthrange
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
# Тест: 6 фиксированных вопросов + 2 открытых
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
# Ретроспектива: выбор варианта и планирование
RETRO_CHOICE, RETRO_SCHEDULE_DAY = range(8, 10)
# Ретроспектива для обсуждения
RETRO_CHAT = 10
# После теста: режим общения с Gemini по тесту
AFTER_TEST_CHOICE, GEMINI_CHAT = range(11, 13)

# Глобальный словарь для запланированных ретроспектив (user_id -> weekday)
scheduled_retrospectives = {}

# ----------------------- Вспомогательные функции -----------------------

def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[str(i) for i in range(1, 8)], ["Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

async def exit_to_main(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Возвращаемся в главное меню.", reply_markup=ReplyKeyboardRemove())
    await start(update, context)
    return ConversationHandler.END

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива"], ["Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

def remaining_days_in_month() -> int:
    """Возвращает количество дней, оставшихся до конца текущего месяца."""
    today = datetime.now()
    _, last_day = monthrange(today.year, today.month)
    return last_day - today.day

def build_gemini_prompt_for_test(test_answers: dict) -> str:
    """
    Формирует промпт для анализа результатов теста.
    Инструкция: Пользователь проходил опросы каждый день. Дай общий обзор состояния клиента за неделю,
    не вдаваясь в подробности (не более 120 слов) и в конце предложи обсудить результаты.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. Клиент проходил опросы каждый день, и ниже приведены его результаты за прошедшую неделю. "
        "Проанализируй динамику изменений и дай краткий общий обзор итогов недели (не более 120 слов), не вдаваясь в подробности. "
        "В конце ответа строго предложи обсудить итоги недели."
    )
    prompt = standard + "\n\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    logger.info(f"Промпт для теста:\n{prompt}")
    return prompt

def build_gemini_prompt_for_followup_chat(user_message: str, test_answers: dict) -> str:
    """
    Формирует промпт для общения по тесту.
    Инструкция: Используй данные сегодняшнего теста. Ответ должен быть конкретным и кратким (не более 120 слов),
    не повторять общий обзор, а отвечать только на вопрос клиента. В конце ответа предложи обсудить итоги недели.
    Запрещено использовать символы форматирования.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. Клиент уже получил общий обзор своего состояния по сегодняшнему тесту. "
        "Теперь ответь на следующий вопрос, используя только данные теста. Не повторяй общий обзор, а дай конкретные рекомендации. "
        "Ответ должен быть кратким (не более 120 слов) и не содержать символов форматирования. "
        "В конце ответа строго предложи обсудить итоги недели."
    )
    prompt = standard + "\n\nДанные теста:\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nВопрос клиента: " + user_message + "\n"
    logger.info(f"Промпт для общения по тесту:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro(averages: dict, test_count: int) -> str:
    """
    Формирует промпт для ретроспективного анализа.
    Инструкция: Проведи краткий обзор ключевых событий, произошедших за прошедшую неделю, 
    а также кратко освети динамику состояния клиента за эту неделю. Ответ не должен превышать 120 слов.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. Клиент проходил опросы каждый день. "
        "Пожалуйста, проведите краткий обзор ключевых событий, произошедших за прошедшую неделю, "
        "а также кратко осветите динамику состояния клиента за эту неделю. Ответ не должен превышать 120 слов."
    )
    prompt = standard + "\n\n"
    prompt += f"Количество тестов: {test_count}\n"
    for category, avg in averages.items():
        prompt += f"{category}: {avg}\n"
    logger.info(f"Промпт для ретроспективы:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
    """
    Формирует промпт для обсуждения итогов недели.
    Инструкция: Используй ранее полученный общий обзор итогов недели (не повторяя его) как контекст.
    Ответ должен быть конкретным и кратким (не более 120 слов) и содержать рекомендации по заданному вопросу.
    В конце ответа строго предложи обсудить итоги недели. Запрещено повторять общий обзор.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. Ниже приведен общий обзор итогов недели, полученный ранее. "
        "Не повторяйте этот обзор, а используйте его как контекст для ответа на следующий вопрос клиента. "
        "Ответ должен быть конкретным, кратким (не более 120 слов) и содержать рекомендации по заданному вопросу. "
        "В конце ответа строго предложите обсудить итоги недели. "
        "Запрещено использовать символы форматирования."
    )
    prompt = standard + "\n\nОбзор итогов недели: " + week_overview + "\n\nВопрос клиента: " + user_message + "\n"
    logger.info(f"Промпт для обсуждения недели:\n{prompt}")
    return prompt

async def call_gemini_api(prompt: str, max_tokens: int = 150) -> dict:
    """
    Отправляет запрос к Gemini API.
    Параметры генерации:
      - candidate_count: 1
      - max_output_tokens: задается через параметр max_tokens (150 по умолчанию)
      - temperature: 0.4 (низкая для детерминированного ответа)
      - top_p: 1.0, top_k: 40
    """
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
            max_output_tokens=max_tokens,
            temperature=0.4,
            top_p=1.0,
            top_k=40
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
            interpretation = vars(response).get("content", "Нет ответа от Gemini.")
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
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    index = context.user_data.get('question_index', 0)
    answer = user_input
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
        await update.message.reply_text(OPEN_QUESTIONS[0], reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
        return TEST_OPEN_1

async def test_open_1(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data['test_answers']['open_1'] = user_input
    await update.message.reply_text(OPEN_QUESTIONS[1], reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return TEST_OPEN_2

async def test_open_2(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data['test_answers']['open_2'] = user_input

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
    gemini_response = await call_gemini_api(prompt)  # используется дефолтное значение max_tokens
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
    message = (
        f"Результат анализа:\n{interpretation}\n\n"
        "Теперь вы можете общаться с ИИ-психологом по результатам теста. Отправляйте свои сообщения, "
        "и они будут учитываться в контексте анализа вашего дня.\n"
        "Для выхода в главное меню нажмите кнопку «Главное меню»."
    )
    await update.message.reply_text(
        message,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )

    # Автоматический запуск запланированной ретроспективы:
    if user_id in scheduled_retrospectives:
        scheduled_day = scheduled_retrospectives[user_id]
        today = datetime.now()
        current_week = today.isocalendar()[1]
        last_retro_week = context.user_data.get("last_retrospective_week")
        if today.weekday() >= scheduled_day and last_retro_week != current_week:
            await update.message.reply_text("Запущена запланированная ретроспектива:")
            await run_retrospective_now(update, context)
    return GEMINI_CHAT

async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "главное меню":
        return await exit_to_main(update, context)
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

async def gemini_chat_handler(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    test_answers = context.user_data.get("last_test_answers", {})
    prompt = build_gemini_prompt_for_followup_chat(user_input, test_answers)
    gemini_response = await call_gemini_api(prompt)  # используется дефолтное значение max_tokens
    answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

# ----------------------- Обработчики ретроспективы -----------------------

async def retrospective_start(update: Update, context: CallbackContext) -> int:
    keyboard = [["Ретроспектива сейчас", "Запланировать ретроспективу", "Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите вариант ретроспективы:", reply_markup=reply_markup)
    return RETRO_CHOICE

async def retrospective_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "главное меню":
        return await exit_to_main(update, context)
    elif choice == "ретроспектива сейчас":
        await run_retrospective_now(update, context)
        return RETRO_CHAT
    elif choice == "запланировать ретроспективу":
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье", "Главное меню"]
        reply_markup = ReplyKeyboardMarkup([days], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Выберите день недели для ретроспективы:", reply_markup=reply_markup)
        return RETRO_SCHEDULE_DAY
    else:
        await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.")
        return RETRO_CHOICE

async def retrospective_schedule_day(update: Update, context: CallbackContext) -> int:
    day_text = update.message.text.strip().lower()
    if day_text == "главное меню":
        return await exit_to_main(update, context)
    days_mapping = {
        "понедельник": 0,
        "вторник": 1,
        "среда": 2,
        "четверг": 3,
        "пятница": 4,
        "суббота": 5,
        "воскресенье": 6
    }
    if day_text not in days_mapping:
        await update.message.reply_text("Неверный ввод. Пожалуйста, выберите день недели или 'Главное меню'.")
        return RETRO_SCHEDULE_DAY
    day_number = days_mapping[day_text]
    user_id = update.message.from_user.id
    scheduled_retrospectives[user_id] = day_number
    await update.message.reply_text(
        f"Ретроспектива запланирована на {update.message.text.strip()}. После нового теста в этот день ретроспектива будет выполнена автоматически.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def run_retrospective_now(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    now = datetime.now()
    one_week_ago = now - timedelta(days=7)
    user_files = [f for f in os.listdir(DATA_DIR) if f.startswith(f"{user_id}_") and f.endswith(".json")]
    tests = []
    for file in user_files:
        file_path = os.path.join(DATA_DIR, file)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ts = datetime.strptime(data.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
                if one_week_ago <= ts <= now:
                    tests.append(data)
        except Exception as e:
            logger.error(f"Ошибка чтения файла {file_path}: {e}")
    if len(tests) < 4:
        await update.message.reply_text("Недостаточно данных для ретроспективы. Пройдите тест минимум 4 раза за 7 дней.", reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
        return
    sums = {f"fixed_{i}": 0 for i in range(1, 7)}
    counts = {f"fixed_{i}": 0 for i in range(1, 7)}
    for test in tests:
        answers = test.get("test_answers", {})
        for i in range(1, 7):
            key = f"fixed_{i}"
            try:
                val = int(answers.get(key))
                sums[key] += val
                counts[key] += 1
            except (ValueError, TypeError):
                continue
    averages = {}
    if counts["fixed_1"] and counts["fixed_2"]:
        averages["Самочувствие"] = round((sums["fixed_1"]/counts["fixed_1"] + sums["fixed_2"]/counts["fixed_2"]) / 2, 2)
    else:
        averages["Самочувствие"] = None
    if counts["fixed_3"] and counts["fixed_4"]:
        averages["Активность"] = round((sums["fixed_3"]/counts["fixed_3"] + sums["fixed_4"]/counts["fixed_4"]) / 2, 2)
    else:
        averages["Активность"] = None
    if counts["fixed_5"] and counts["fixed_6"]:
        averages["Настроение"] = round((sums["fixed_5"]/counts["fixed_5"] + sums["fixed_6"]/counts["fixed_6"]) / 2, 2)
    else:
        averages["Настроение"] = None

    prompt = build_gemini_prompt_for_retro(averages, len(tests))
    # В ретроспективе больше не указываем max_tokens – используем дефолтное значение
    gemini_response = await call_gemini_api(prompt)
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
    # Сохраняем номер недели, в которой уже была запущена ретроспектива
    current_week = now.isocalendar()[1]
    context.user_data["last_retrospective_week"] = current_week
    message = (
        f"Ретроспектива за последнюю неделю:\n{interpretation}\n\n"
        "Если хотите обсудить итоги недели, задайте свой вопрос.\n"
        "Для выхода в главное меню нажмите кнопку «Главное меню»."
    )
    await update.message.reply_text(message, reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_CHAT

async def retrospective_chat_handler(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    week_overview = context.user_data.get("week_overview", "")
    prompt = build_gemini_prompt_for_retro_chat(user_input, week_overview)
    gemini_response = await call_gemini_api(prompt, max_tokens=150)
    answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return RETRO_CHAT

# ----------------------- Дополнительные команды -----------------------

async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "Наш бот предназначен для оценки вашего состояния с помощью короткого теста.\n\n"
        "Команды:\n"
        "• Тест – пройти тест (6 фиксированных вопросов и 2 открытых).\n"
        "• Ретроспектива – анализ изменений за последнюю неделю и обсуждение итогов.\n"
        "• Помощь – справочная информация.\n\n"
        "Во всех этапах работы доступна кнопка «Главное меню» для возврата в стартовое меню."
    )
    await update.message.reply_text(
        help_text,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Ошибка при обработке обновления {update}: {context.error}")

# ----------------------- Основная функция -----------------------

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
        fallbacks=[CommandHandler("cancel", test_cancel), MessageHandler(filters.Regex("^Главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(test_conv_handler)

    # ConversationHandler для ретроспективы
    retro_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective_start)],
        states={
            RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_choice_handler)],
            RETRO_SCHEDULE_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_schedule_day)],
            RETRO_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_chat_handler)]
        },
        fallbacks=[CommandHandler("cancel", test_cancel), MessageHandler(filters.Regex("^Главное меню$"), exit_to_main)],
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
