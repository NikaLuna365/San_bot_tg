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

# Словарь фиксированных вопросов по дням недели (0 – понедельник, 6 – воскресенье)
WEEKDAY_FIXED_QUESTIONS = {
    0: [  # Понедельник
        "Самочувствие хорошее — Самочувствие плохое",
        "Чувствую себя сильным — Чувствую себя слабым",
        "Пассивный — Активный",
        "Малоподвижный — Подвижный",
        "Весёлый — Грустный",
        "Хорошее настроение — Плохое настроение"
    ],
    1: [  # Вторник
        "Работоспособный — Разбитый",
        "Полный сил — Обессиленный",
        "Медлительный — Быстрый",
        "Бездеятельный — Деятельный",
        "Счастливый — Несчастный",
        "Жизнерадостный — Мрачный"
    ],
    2: [  # Среда
        "Напряжённый — Расслабленный",
        "Здоровый — Больной",
        "Безучастный — Увлечённый",
        "Равнодушный — Заинтересованный",
        "Восторженный — Унылый",
        "Радостный — Печальный"
    ],
    3: [  # Четверг
        "Отдохнувший — Усталый",
        "Свежий — Изнурённый",
        "Сонливый — Возбуждённый",
        "Желание отдохнуть — Желание работать",
        "Спокойный — Взволнованный",
        "Оптимистичный — Пессимистичный"
    ],
    4: [  # Пятница
        "Выносливый — Утомляемый",
        "Бодрый — Вялый",
        "Соображать трудно — Соображать легко",
        "Рассеянный — Внимательный",
        "Полный надежд — Разочарованный",
        "Довольный — Недовольный"
    ],
    5: [  # Суббота
        "Бодрствующий — Сонный",
        "Расслабленный — Напряжённый",
        "Свежий — Утомлённый",
        "Здоровый — Нездоровый",
        "Энергичный — Вялый",
        "Решительный — Колеблющийся"
    ],
    6: [  # Воскресенье
        "Сосредоточенный — Рассеянный",
        "Деятельный — Пассивный",
        "Оптимистичный — Пессимистичный",
        "Спокойный — Взволнованный",
        "Уверенный — Неуверенный",
        "Довольный — Недовольный"
    ]
}

# Открытые вопросы остаются одними и теми же для каждого дня
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

def build_gemini_prompt_for_test(fixed_questions: list, test_answers: dict) -> str:
    """
    Формирует промпт для анализа результатов теста.
    Инструкция: Клиент проходил ежедневный опрос, состоящий из фиксированных вопросов по 7-балльной шкале и 2 открытых вопросов.
    Для фиксированных вопросов: оценка 1 означает крайне негативное состояние (например, очень плохое самочувствие, сильная усталость, плохое настроение),
    а оценка 7 – исключительно позитивное состояние (например, отличное самочувствие, высокий уровень энергии, прекрасное настроение).
    Проанализируйте результаты и дайте краткий вывод, предложив обсудить итоги недели.
    """
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
              "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, 7 – исключительно позитивное состояние.\n"
              "Каждая шкала состоит из 2 вопросов, итоговый балл по шкале равен сумме двух оценок (диапазон от 2 до 14): 2–5 – низкий уровень, 6–10 – средний, 11–14 – высокий.\n\n")
    for i, question in enumerate(fixed_questions, start=1):
        key = f"fixed_{i}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    for j, question in enumerate(OPEN_QUESTIONS, start=1):
        key = f"open_{j}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{len(fixed_questions)+j}. {question}\n   Ответ: {answer}\n"
    logger.info(f"Промпт для теста:\n{prompt}")
    return prompt

def build_gemini_prompt_for_followup_chat(fixed_questions: list, user_message: str, test_answers: dict) -> str:
    """
    Формирует промпт для общения по результатам теста.
    Используйте данные текущего теста и инструкцию, аналогичную предыдущему промпту.
    """
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент уже получил общий вывод по сегодняшнему опросу.\n"
              "Используйте данные теста для ответа на следующий вопрос, не повторяя общий вывод.\n"
              "В конце ответа предложите обсудить итоги недели.\n\nДанные теста:\n")
    for i, question in enumerate(fixed_questions, start=1):
        key = f"fixed_{i}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    for j, question in enumerate(OPEN_QUESTIONS, start=1):
        key = f"open_{j}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{len(fixed_questions)+j}. {question}\n   Ответ: {answer}\n"
    prompt += "\nВопрос клиента: " + user_message + "\n"
    logger.info(f"Промпт для общения по тесту:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro(averages: dict, test_count: int) -> str:
    """
    Формирует промпт для ретроспективного анализа.
    Инструкция: Проведите краткий обзор ключевых событий за прошедшую неделю с учётом средних баллов по каждой шкале.
    Фиксированные вопросы оцениваются по 7-балльной шкале (сумма двух вопросов на шкалу: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).
    """
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент проходил ежедневные опросы.\n"
              f"Количество тестов: {test_count}\n"
              "Фиксированные вопросы оцениваются по 7-балльной шкале (итоговая оценка каждой шкалы равна сумме двух вопросов, диапазон 2–14: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).\n\n")
    for category, avg in averages.items():
        prompt += f"{category}: {avg}\n"
    logger.info(f"Промпт для ретроспективы:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
    """
    Формирует промпт для обсуждения итогов недели.
    """
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Ниже приведён общий обзор итогов недели.\n"
              "Используйте его как контекст для ответа на следующий вопрос клиента, давая конкретные рекомендации.\n"
              "В конце ответа предложите обсудить итоги недели. Запрещено использовать символы форматирования.\n\n"
              "Обзор итогов недели: " + week_overview + "\n\nВопрос клиента: " + user_message + "\n")
    logger.info(f"Промпт для обсуждения недели:\n{prompt}")
    return prompt

async def call_gemini_api(prompt: str, max_tokens: int = 300) -> dict:
    """
    Отправляет запрос к Gemini API.
    Параметры генерации:
      - candidate_count: 1
      - max_output_tokens: задается через параметр max_tokens (150 по умолчанию)
      - temperature: 0.4
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
    # Определяем текущий день недели (0 – понедельник, 6 – воскресенье)
    current_day = datetime.now().weekday()
    fixed_questions = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
    context.user_data['fixed_questions'] = fixed_questions
    await update.message.reply_text(fixed_questions[0], reply_markup=build_fixed_keyboard())
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
    fixed_questions = context.user_data.get('fixed_questions', [])
    if index < len(fixed_questions):
        await update.message.reply_text(fixed_questions[index], reply_markup=build_fixed_keyboard())
        return TEST_FIXED_1 + index
    else:
        await update.message.reply_text(OPEN_QUESTIONS[0],
                                        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
        return TEST_OPEN_1

async def test_open_1(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data['test_answers']['open_1'] = user_input
    await update.message.reply_text(OPEN_QUESTIONS[1],
                                    reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
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
    prompt = build_gemini_prompt_for_test(context.user_data.get("fixed_questions", []),
                                          context.user_data.get("test_answers", {}))
    gemini_response = await call_gemini_api(prompt)
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
    fixed_questions = context.user_data.get("fixed_questions", [])
    prompt = build_gemini_prompt_for_followup_chat(fixed_questions, user_input, test_answers)
    gemini_response = await call_gemini_api(prompt)
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
        await update.message.reply_text("Недостаточно данных для ретроспективы. Пройдите тест минимум 4 раза за 7 дней.",
                                        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
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
    gemini_response = await call_gemini_api(prompt)
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
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
    gemini_response = await call_gemini_api(prompt, max_tokens=300)
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
        "• Тест – пройти тест (фиксированные вопросы, зависящие от дня недели, и 2 открытых вопроса).\n"
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
