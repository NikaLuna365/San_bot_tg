import os
import json
import logging
import asyncio
from calendar import monthrange
from datetime import datetime, timedelta, time, date

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    filters, CallbackContext
)

# Импорт SDK для Gemini от Google
from google.generativeai import GenerativeModel, configure, types

# Импорт функций для работы с БД
from db import (
    create_db_pool,
    upsert_daily_reminder,
    get_active_daily_reminders,
    update_last_sent_daily,
    upsert_scheduled_retrospective,
    get_active_scheduled_retrospectives,
    update_last_sent_scheduled_retrospective,
    upsert_weekly_retrospective,
    get_active_weekly_retrospectives,
    update_last_sent_weekly
)

# ----------------------- Настройка логирования -----------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------------- Константы для вопросов -----------------------
WEEKDAY_FIXED_QUESTIONS = {
    0: [
        "Оцените, насколько ваше самочувствие сегодня ближе к хорошему или плохому (при 1 – крайне плохое самочувствие, а 7 – превосходное самочувствие)",
        "Оцените, чувствуете ли вы себя сильным или слабым (при 1 – чрезвычайно слабым, а 7 – исключительно сильным)",
        "Оцените свою активность: насколько вы ощущаете себя пассивным или активным (при 1 – крайне пассивным, а 7 – исключительно активным)",
        "Оцените вашу подвижность: насколько вы ощущаете себя малоподвижным или подвижным (при 1 – крайне малоподвижным, а 7 – чрезвычайно подвижным)",
        "Оцените ваше эмоциональное состояние: насколько вы чувствуете себя весёлым или грустным (при 1 – крайне грустным, а 7 – исключительно весёлым)",
        "Оцените ваше настроение: насколько оно ближе к хорошему или плохому (при 1 – очень плохое настроение, а 7 – прекрасное настроение)"
    ]
}
OPEN_QUESTIONS = [
    "7. Какие три слова лучше всего описывают ваше текущее состояние?",
    "8. Что больше всего повлияло на ваше состояние сегодня?"
]
RETRO_OPEN_QUESTIONS = [
    "Какие события на этой неделе больше всего повлияли на ваше общее состояние?",
    "Какие факторы способствовали вашей продуктивности, а какие, наоборот, мешали?",
    "Какие у вас были ожидания от этой недели, и насколько они оправдались?",
    "Какие уроки вы вынесли из прошедшей недели, и как вы планируете использовать этот опыт в будущем?"
]

# ----------------------- Состояния -----------------------
# Для теста
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
# Для немедленной ретроспективы (после теста)
RETRO_CHOICE = 8
RETRO_PERIOD_CHOICE = 9
RETRO_OPEN_1 = 10
RETRO_OPEN_2 = 11
RETRO_OPEN_3 = 12
RETRO_OPEN_4 = 13
RETRO_CHAT = 14
AFTER_TEST_CHOICE, GEMINI_CHAT = range(15, 17)
# Для ежедневных напоминаний
REMINDER_CHOICE, REMINDER_DAILY_TIME, REMINDER_DAILY_REMIND = range(100, 103)
# Для запланированной ретроспективы (новый диалог)
RETRO_SCHEDULE_DAY_NEW = 200
RETRO_SCHEDULE_CURRENT = 201
RETRO_SCHEDULE_TARGET = 202
RETRO_SCHEDULE_MODE = 203

# ----------------------- Глобальные словари -----------------------
scheduled_reminders = {}         # Ежедневные тесты
scheduled_retrospectives = {}      # Запланированные ретроспективы

# ----------------------- Вспомогательные функции -----------------------
def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[str(i) for i in range(1, 8)], ["Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

async def exit_to_main(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    main_menu = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
    await update.message.reply_text("Возвращаемся в главное меню.\n\nДобро пожаловать! Выберите действие:",
                                      reply_markup=ReplyKeyboardMarkup(main_menu, resize_keyboard=True, one_time_keyboard=True))
    return ConversationHandler.END

async def start(update: Update, context: CallbackContext) -> None:
    main_menu = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
    await update.message.reply_text("Добро пожаловать! Выберите действие:",
                                    reply_markup=ReplyKeyboardMarkup(main_menu, resize_keyboard=True, one_time_keyboard=True))

def remaining_days_in_month() -> int:
    today = datetime.now()
    _, last_day = monthrange(today.year, today.month)
    return last_day - today.day

def build_gemini_prompt_for_test(fixed_questions: list, test_answers: dict) -> str:
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
              "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, а 7 – исключительно позитивное состояние.\n"
              "Каждая шкала состоит из 2 вопросов (итоговый балл = сумма двух оценок, диапазон 2–14: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).\n"
              "Пожалуйста, выполните все вычисления итоговых баллов в уме без вывода промежуточных данных. Сформируйте один абзац общего анализа итоговых баллов и динамики состояния клиента, а затем сразу кратко опишите анализ открытых вопросов.\n"
              "Запрещается использование символа \"*\" для форматирования результатов.\n\n")
    for i, q in enumerate(fixed_questions, start=1):
        prompt += f"{i}. {q}\n   Ответ: {test_answers.get(f'fixed_{i}', 'не указано')}\n"
    for j, q in enumerate(OPEN_QUESTIONS, start=1):
        prompt += f"{len(fixed_questions)+j}. {q}\n   Ответ: {test_answers.get(f'open_{j}', 'не указано')}\n"
    logger.info(f"Промпт для теста:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro(averages: dict, test_count: int, open_answers: dict, period_days: int) -> str:
    prompt = f"Ретроспектива: за последние {period_days} дней проведено {test_count} тестов.\nСредние показатели:\n"
    for k, v in averages.items():
        prompt += f"{k}: {v if v is not None else 'не указано'}\n"
    prompt += "\nКачественный анализ:\n"
    for idx, q in enumerate(RETRO_OPEN_QUESTIONS, start=1):
        prompt += f"{idx}. {q}\n   Ответ: {open_answers.get(f'retro_open_{idx}', 'не указано')}\n"
    prompt += "\nПожалуйста, сформируйте аналитический отчет по динамике состояния клиента за указанный период."
    return prompt

def build_followup_chat_prompt(user_message: str, chat_context: str) -> str:
    return (
        f"Вы — высококвалифицированный психолог с более чем десятилетним стажем. Обращайтесь к пользователю на «Вы». "
        f"Контекст теста: {chat_context}\n\nВопрос пользователя: {user_message}"
    )

def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
    return (
        f"Вы — высококвалифицированный психолог с более чем десятилетним стажем. Обращайтесь к пользователю на «Вы». "
        f"Контекст анализа: {week_overview}\n\nВопрос пользователя: {user_message}"
    )

async def call_gemini_api(prompt: str, max_tokens: int = 600) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY не задан.")
        return {"interpretation": "Ошибка: API ключ не задан."}
    try:
        configure(api_key=api_key)
        model = GenerativeModel("gemini-2.0-flash")
        logger.info(f"Отправка запроса к Gemini API:\n{prompt}")
        gen_config = types.GenerationConfig(
            candidate_count=1, max_output_tokens=max_tokens, temperature=0.4, top_p=1.0, top_k=40
        )
        response = await asyncio.to_thread(lambda: model.generate_content([prompt], generation_config=gen_config))
        if hasattr(response, "text") and response.text:
            interpretation = response.text
        elif hasattr(response, "content") and response.content:
            interpretation = response.content
        else:
            interpretation = vars(response).get("content", "Нет ответа от Gemini.")
        logger.info(f"Ответ от Gemini: {interpretation}")
        return {"interpretation": interpretation}
    except Exception as e:
        logger.exception("Ошибка при вызове Gemini API:")
        return {"interpretation": "Ошибка при обращении к Gemini API."}

# ----------------------- Обработчики теста -----------------------
async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def test_start(update: Update, context: CallbackContext) -> int:
    context.user_data['test_answers'] = {}
    context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data['question_index'] = 0
    current_day = datetime.now().weekday()
    fixed = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
    context.user_data['fixed_questions'] = fixed
    await update.message.reply_text(fixed[0], reply_markup=build_fixed_keyboard())
    return TEST_FIXED_1

async def test_fixed_handler(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    idx = context.user_data.get('question_index', 0)
    if user_input not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text("Выберите вариант от 1 до 7.", reply_markup=build_fixed_keyboard())
        return TEST_FIXED_1 + idx
    context.user_data[f"fixed_{idx+1}"] = user_input
    idx += 1
    context.user_data['question_index'] = idx
    fixed = context.user_data.get('fixed_questions', [])
    if idx < len(fixed):
        await update.message.reply_text(fixed[idx], reply_markup=build_fixed_keyboard())
        return TEST_FIXED_1 + idx
    else:
        await update.message.reply_text(OPEN_QUESTIONS[0],
                                        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
        return TEST_OPEN_1

async def test_open_1(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data['open_1'] = inp
    await update.message.reply_text(OPEN_QUESTIONS[1],
                                    reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return TEST_OPEN_2

async def test_open_2(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data['open_2'] = inp
    user_id = update.message.from_user.id
    ts = context.user_data.get("test_start_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
    filename = os.path.join("data", f"{user_id}_{ts}.json")
    test_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_answers": {k: v for k, v in context.user_data.items() if k.startswith("fixed_") or k.startswith("open_")}
    }
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(test_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Тестовые данные сохранены в {filename}")
    except Exception as e:
        logger.exception("Ошибка сохранения теста:")
        await update.message.reply_text("Ошибка при сохранении теста.")
        return ConversationHandler.END
    prompt = build_gemini_prompt_for_test(context.user_data.get("fixed_questions", []), test_data["test_answers"])
    gemini_resp = await call_gemini_api(prompt)
    interp = gemini_resp.get("interpretation", "Нет интерпретации.")
    try:
        sf = (int(context.user_data.get("fixed_1")) + int(context.user_data.get("fixed_2"))) / 2
        act = (int(context.user_data.get("fixed_3")) + int(context.user_data.get("fixed_4"))) / 2
        md = (int(context.user_data.get("fixed_5")) + int(context.user_data.get("fixed_6"))) / 2
        chat_ctx = f"Самочувствие: {sf}, Активность: {act}, Настроение: {md}. Открытые ответы учтены."
    except Exception as e:
        logger.exception("Ошибка формирования контекста:")
        chat_ctx = "Данные теста учтены."
    context.user_data["chat_context"] = chat_ctx
    await update.message.reply_text(f"Результат:\n{interp}\n\nТеперь можете общаться с ИИ-психологом.",
                                    reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return GEMINI_CHAT

async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
    if update.message.text.strip().lower() == "главное меню":
        return await exit_to_main(update, context)
    await update.message.reply_text("Переход в чат с ИИ...", reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return GEMINI_CHAT

async def gemini_chat_handler(update: Update, context: CallbackContext) -> int:
    if update.message.text.strip().lower() == "главное меню":
        return await exit_to_main(update, context)
    chat_ctx = context.user_data.get("chat_context", "")
    prompt = build_followup_chat_prompt(update.message.text.strip(), chat_ctx)
    gemini_resp = await call_gemini_api(prompt)
    ans = gemini_resp.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(ans, reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return GEMINI_CHAT

# ----------------------- Обработчики немедленной ретроспективы -----------------------
async def retrospective_start(update: Update, context: CallbackContext) -> int:
    kb = [["Ретроспектива сейчас", "Главное меню"]]
    await update.message.reply_text("Выберите вариант ретроспективы:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))
    return RETRO_CHOICE

async def retrospective_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "главное меню":
        return await exit_to_main(update, context)
    elif choice == "ретроспектива сейчас":
        kb = [["Ретроспектива за 1 неделю", "Ретроспектива за 2 недели"], ["Главное меню"]]
        await update.message.reply_text("Выберите период ретроспективы:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))
        return RETRO_PERIOD_CHOICE
    else:
        await update.message.reply_text("Пожалуйста, выберите корректный вариант.")
        return RETRO_CHOICE

async def retrospective_period_choice(update: Update, context: CallbackContext) -> int:
    pc = update.message.text.strip().lower()
    if pc in ["ретроспектива за 1 неделю", "1 неделя", "1"]:
        await update.message.reply_text("Запускается ретроспектива за 7 дней...")
        await run_retrospective_now(update, context, period_days=7)
    elif pc in ["ретроспектива за 2 недели", "2 недели", "2"]:
        await update.message.reply_text("Запускается ретроспектива за 14 дней...")
        await run_retrospective_now(update, context, period_days=14)
    else:
        await update.message.reply_text("Выберите один из предложенных вариантов.")
        return RETRO_PERIOD_CHOICE
    return RETRO_CHAT

async def retro_open_1(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data["retro_open_1"] = inp
    await update.message.reply_text(RETRO_OPEN_QUESTIONS[1], reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_OPEN_2

async def retro_open_2(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data["retro_open_2"] = inp
    await update.message.reply_text(RETRO_OPEN_QUESTIONS[2], reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_OPEN_3

async def retro_open_3(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data["retro_open_3"] = inp
    await update.message.reply_text(RETRO_OPEN_QUESTIONS[3], reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_OPEN_4

async def retro_open_4(update: Update, context: CallbackContext) -> int:
    inp = update.message.text.strip()
    if inp.lower() == "главное меню":
        return await exit_to_main(update, context)
    context.user_data["retro_open_4"] = inp
    await update.message.reply_text("Запускается ретроспектива по результатам теста...", reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    await run_retrospective_now(update, context, period_days=7)
    return RETRO_CHAT

async def run_retrospective_now(update: Update, context: CallbackContext, period_days: int = 7):
    user_id = update.message.from_user.id
    now = datetime.now()
    period_start = now - timedelta(days=period_days)
    files = [f for f in os.listdir("data") if f.startswith(f"{user_id}_") and f.endswith(".json")]
    tests = []
    for f in files:
        path = os.path.join("data", f)
        try:
            with open(path, "r", encoding="utf-8") as file:
                data = json.load(file)
                ts = datetime.strptime(data.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
                if period_start <= ts <= now:
                    tests.append(data)
        except Exception as e:
            logger.exception(f"Ошибка чтения файла {path}:")
    if len(tests) < 4:
        await update.message.reply_text(f"Недостаточно данных для ретроспективы за последние {period_days} дней. Пройдите тест минимум 4 раза.",
                                        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
        return
    sums = {f"fixed_{i}": 0 for i in range(1, 7)}
    counts = {f"fixed_{i}": 0 for i in range(1, 7)}
    for test in tests:
        answers = test.get("test_answers", {})
        for i in range(1, 7):
            try:
                val = int(answers.get(f"fixed_{i}", 0))
                sums[f"fixed_{i}"] += val
                counts[f"fixed_{i}"] += 1
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

    open_ans = {
        "retro_open_1": context.user_data.get("retro_open_1", "не указано"),
        "retro_open_2": context.user_data.get("retro_open_2", "не указано"),
        "retro_open_3": context.user_data.get("retro_open_3", "не указано"),
        "retro_open_4": context.user_data.get("retro_open_4", "не указано")
    }
    prompt = build_gemini_prompt_for_retro(averages, len(tests), open_ans, period_days)
    gemini_resp = await call_gemini_api(prompt)
    interp = gemini_resp.get("interpretation", "Нет интерпретации.")
    context.user_data["last_retrospective_week"] = now.isocalendar()[1]
    retro_file = os.path.join("data", f"{user_id}_retro_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    retro_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_count": len(tests),
        "averages": averages,
        "open_answers": open_ans,
        "interpretation": interp,
        "period_days": period_days
    }
    try:
        with open(retro_file, "w", encoding="utf-8") as f:
            json.dump(retro_data, f, ensure_ascii=False, indent=4)
        logger.info(f"Данные ретроспективы сохранены в {retro_file}")
    except Exception as e:
        logger.exception("Ошибка сохранения ретроспективы:")
    week_overview = (f"Самочувствие: {averages.get('Самочувствие', 'не указано')}, "
                     f"Активность: {averages.get('Активность', 'не указано')}, "
                     f"Настроение: {averages.get('Настроение', 'не указано')}. Ответы на качественные вопросы учтены.")
    context.user_data["week_overview"] = week_overview
    await update.message.reply_text(f"Ретроспектива за {period_days} дней:\n{interp}\n\nЗадайте вопрос для обсуждения или нажмите «Главное меню».",
                                    reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_CHAT

async def retrospective_chat_handler(update: Update, context: CallbackContext) -> int:
    if update.message.text.strip().lower() == "главное меню":
        return await exit_to_main(update, context)
    week_overview = context.user_data.get("week_overview", "")
    prompt = build_gemini_prompt_for_retro_chat(update.message.text.strip(), week_overview)
    gemini_resp = await call_gemini_api(prompt, max_tokens=600)
    ans = gemini_resp.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(ans, reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_CHAT

# ----------------------- Обработчики запланированной ретроспективы -----------------------
async def retrospective_schedule_choice_handler(update: Update, context: CallbackContext) -> int:
    # Этот обработчик вызывается, когда пользователь вводит "Запланировать ретроспективу"
    await update.message.reply_text("Введите день недели для запланированной ретроспективы (например, 'Понедельник'):",
                                      reply_markup=ReplyKeyboardMarkup([["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье", "Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_SCHEDULE_DAY_NEW

async def retro_schedule_day_handler(update: Update, context: CallbackContext) -> int:
    day_text = update.message.text.strip().lower()
    days = {"понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3, "пятница": 4, "суббота": 5, "воскресенье": 6}
    if day_text == "главное меню" or day_text not in days:
        await update.message.reply_text("Неверный ввод. Выберите день недели или 'Главное меню'.")
        return RETRO_SCHEDULE_DAY_NEW
    context.user_data["retro_schedule_day"] = days[day_text]
    await update.message.reply_text("Введите ваше текущее время (например, 15:30):",
                                      reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_SCHEDULE_CURRENT

async def retro_schedule_current_handler(update: Update, context: CallbackContext) -> int:
    cur_time = update.message.text.strip()
    if cur_time.lower() == "главное меню":
        return await exit_to_main(update, context)
    try:
        datetime.strptime(cur_time, "%H:%M")
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Введите время в формате ЧЧ:ММ.")
        return RETRO_SCHEDULE_CURRENT
    context.user_data["retro_current_time"] = cur_time
    await update.message.reply_text("Введите желаемое время ретроспективы (например, 08:00):",
                                      reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_SCHEDULE_TARGET

async def retro_schedule_target_handler(update: Update, context: CallbackContext) -> int:
    tgt_time = update.message.text.strip()
    if tgt_time.lower() == "главное меню":
        return await exit_to_main(update, context)
    try:
        datetime.strptime(tgt_time, "%H:%M")
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Введите время в формате ЧЧ:ММ.")
        return RETRO_SCHEDULE_TARGET
    context.user_data["retro_target_time"] = tgt_time
    await update.message.reply_text("Выберите режим ретроспективы:", 
                                      reply_markup=ReplyKeyboardMarkup([["Еженедельная", "Двухнедельная", "Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return RETRO_SCHEDULE_MODE

async def retro_schedule_mode_handler(update: Update, context: CallbackContext) -> int:
    mode = update.message.text.strip().lower()
    if mode == "главное меню":
        return await exit_to_main(update, context)
    if mode in ["еженедельная", "1"]:
        retro_mode = "weekly"
    elif mode in ["двухнедельная", "2"]:
        retro_mode = "biweekly"
    else:
        await update.message.reply_text("Выберите 'Еженедельная' или 'Двухнедельная'.")
        return RETRO_SCHEDULE_MODE
    context.user_data["retro_mode"] = retro_mode

    # Расчет серверного времени ретроспективы
    try:
        user_cur = datetime.strptime(context.user_data["retro_current_time"], "%H:%M").time()
        user_tgt = datetime.strptime(context.user_data["retro_target_time"], "%H:%M").time()
    except Exception as e:
        logger.exception("Ошибка разбора времени:")
        await update.message.reply_text("Ошибка формата времени. Повторите ввод.")
        return ConversationHandler.END

    server_now = datetime.utcnow()
    server_date = server_now.date()
    user_cur_dt = datetime.combine(server_date, user_cur)
    offset = server_now - user_cur_dt
    logger.info(f"Текущее время пользователя: {user_cur}, серверное: {server_now.time()}, смещение: {offset}")
    user_tgt_dt = datetime.combine(server_date, user_tgt)
    computed_dt = user_tgt_dt + offset

    def next_occurrence(target_weekday, target_dt, current_dt):
        days_diff = context.user_data["retro_schedule_day"] - target_dt.weekday()
        if days_diff < 0 or (days_diff == 0 and target_dt <= current_dt):
            days_diff += 7
        return target_dt + timedelta(days=days_diff)

    scheduled_dt = next_occurrence(context.user_data["retro_schedule_day"], computed_dt, server_now)
    scheduled_time = scheduled_dt.time()
    logger.info(f"Запланированное серверное время ретроспективы: {scheduled_time}")

    pool = context.bot_data.get("db_pool")
    try:
        await upsert_scheduled_retrospective(
            pool,
            update.message.from_user.id,
            context.user_data["retro_schedule_day"],
            user_tgt,         # локальное время
            scheduled_time,   # серверное время
            retro_mode
        )
    except Exception as e:
        logger.exception("Ошибка сохранения ретроспективы в БД:")
        await update.message.reply_text("Ошибка сохранения ретроспективы. Попробуйте позже.")
        return ConversationHandler.END

    initial_delay = (scheduled_dt - server_now).total_seconds()
    interval = 7 * 24 * 3600 if retro_mode == "weekly" else 14 * 24 * 3600

    job = context.job_queue.run_repeating(
        send_daily_reminder,  # Здесь можно создать отдельный callback для ретроспективы
        interval=interval,
        first=initial_delay,
        data={'user_id': update.message.from_user.id, 'mode': retro_mode},
        name=str(update.message.from_user.id) + "_retro"
    )
    scheduled_retrospectives[update.message.from_user.id] = job

    await update.message.reply_text("Запланированная ретроспектива установлена!",
                                    reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return ConversationHandler.END

# ----------------------- Функция загрузки запланированных ретроспектив при старте -----------------------
async def schedule_active_retrospectives(app: Application):
    pool = app.bot_data.get("db_pool")
    try:
        retros = await get_active_scheduled_retrospectives(pool)
        for r in retros:
            try:
                if isinstance(r["local_time"], str):
                    local_time_obj = datetime.strptime(r["local_time"], "%H:%M:%S").time()
                else:
                    local_time_obj = r["local_time"]
                if isinstance(r["server_time"], str):
                    server_time_obj = datetime.strptime(r["server_time"], "%H:%M:%S").time()
                else:
                    server_time_obj = r["server_time"]
            except Exception as e:
                logger.exception(f"Ошибка преобразования времени (local: {r['local_time']}, server: {r['server_time']}):")
                continue
            user_id = r["user_id"]
            server_now = datetime.utcnow()
            server_target_dt = datetime.combine(server_now.date(), server_time_obj)
            def next_occurrence(target_weekday, target_dt, current_dt):
                days_diff = r["scheduled_day"] - target_dt.weekday()
                if days_diff < 0 or (days_diff == 0 and target_dt <= current_dt):
                    days_diff += 7
                return target_dt + timedelta(days=days_diff)
            scheduled_dt = next_occurrence(r["scheduled_day"], server_target_dt, server_now)
            initial_delay = (scheduled_dt - server_now).total_seconds()
            mode = r["retrospective_type"]
            interval = 7 * 24 * 3600 if mode == "weekly" else 14 * 24 * 3600
            if user_id in scheduled_retrospectives:
                scheduled_retrospectives[user_id].schedule_removal()
            job = app.job_queue.run_repeating(
                send_daily_reminder,
                interval=interval,
                first=initial_delay,
                data={'user_id': user_id, 'mode': mode},
                name=str(user_id) + "_retro"
            )
            scheduled_retrospectives[user_id] = job
            logger.info(f"Запланированная ретроспектива для пользователя {user_id} загружена, серверное время: {server_time_obj}")
    except Exception as e:
        logger.exception("Ошибка загрузки запланированных ретроспектив из БД:")

# ----------------------- Обработчики ежедневных напоминаний -----------------------
async def reminder_start(update: Update, context: CallbackContext) -> int:
    kb = [["Ежедневный тест", "Ретроспектива"], ["Главное меню"]]
    await update.message.reply_text("Выберите тип напоминания:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True))
    return REMINDER_CHOICE

async def reminder_daily_test(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "ежедневный тест":
        await update.message.reply_text("Сколько у вас сейчас времени? (например, 15:30)")
        return REMINDER_DAILY_TIME
    elif choice == "ретроспектива":
        await update.message.reply_text("Функция ретроспективы в разработке.")
        return ConversationHandler.END
    else:
        return await exit_to_main(update, context)

async def reminder_receive_current_time(update: Update, context: CallbackContext) -> int:
    current_time = update.message.text.strip()
    context.user_data["current_time"] = current_time
    await update.message.reply_text("Во сколько напоминать о ежедневном тесте? (например, 08:00)")
    return REMINDER_DAILY_REMIND

async def send_daily_reminder(context: CallbackContext):
    job_data = context.job.data
    user_id = job_data['user_id']
    await context.bot.send_message(chat_id=user_id, text="Напоминание: пришло время пройти запланированную ретроспективу!")

async def reminder_set_daily(update: Update, context: CallbackContext) -> int:
    rem_str = update.message.text.strip()
    user_id = update.message.from_user.id
    try:
        rem_obj = datetime.strptime(rem_str, "%H:%M").time()
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Введите время в формате ЧЧ:ММ.")
        return REMINDER_DAILY_REMIND
    if user_id in scheduled_reminders:
        scheduled_reminders[user_id].schedule_removal()
    pool = context.bot_data.get("db_pool")
    try:
        await upsert_daily_reminder(pool, user_id, rem_obj)
    except Exception as e:
        logger.exception("Ошибка сохранения напоминания в БД:")
        await update.message.reply_text("Ошибка сохранения. Попробуйте позже.")
        return ConversationHandler.END
    job = context.job_queue.run_daily(
        send_daily_reminder,
        rem_obj,
        data={'user_id': user_id},
        name=str(user_id)
    )
    scheduled_reminders[user_id] = job
    await update.message.reply_text("Напоминание установлено!", reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
    return ConversationHandler.END

# ----------------------- Дополнительные команды -----------------------
async def help_command(update: Update, context: CallbackContext) -> None:
    text = ("Наш бот для оценки состояния. Команды:\n"
            "• Тест – пройти тест.\n"
            "• Ретроспектива – анализ изменений за период.\n"
            "• Напоминание – установить напоминание.\n"
            "• Помощь – справка.\n\n"
            "Нажмите «Главное меню» для возврата.")
    await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.exception(f"Ошибка обновления {update}:")

# ----------------------- Основная функция -----------------------
def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан.")
        return
    app = Application.builder().token(TOKEN).post_init(schedule_active_retrospectives).build()
    pool = loop.run_until_complete(create_db_pool())
    app.bot_data["db_pool"] = pool

    test_handler = ConversationHandler(
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
        fallbacks=[CommandHandler("cancel", test_cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(test_handler)

    retro_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective_start)],
        states={
            RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_choice_handler)],
            RETRO_PERIOD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_period_choice)],
            RETRO_OPEN_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_1)],
            RETRO_OPEN_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_2)],
            RETRO_OPEN_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_3)],
            RETRO_OPEN_4: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_4)],
            RETRO_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, gemini_chat_handler)]
        },
        fallbacks=[CommandHandler("cancel", test_cancel), MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(retro_handler)

    retro_schedule_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Запланировать ретроспектива$"), retrospective_schedule_choice_handler)],
        states={
            RETRO_SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_day_handler)],
            RETRO_SCHEDULE_CURRENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_current_handler)],
            RETRO_SCHEDULE_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_target_handler)],
            RETRO_SCHEDULE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_mode_handler)]
        },
        fallbacks=[MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(retro_schedule_handler)

    reminder_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Напоминание$"), reminder_start)],
        states={
            REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_daily_test)],
            REMINDER_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_receive_current_time)],
            REMINDER_DAILY_REMIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_set_daily)]
        },
        fallbacks=[MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(reminder_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))
    app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main))
    app.add_error_handler(error_handler)

    app.run_polling()

if __name__ == "__main__":
    main()
