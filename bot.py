import os
import json
import logging
import asyncio
from calendar import monthrange
from datetime import datetime, timedelta, date

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    filters, CallbackContext
)

# Импорт SDK для Gemini от Google
from google.generativeai import GenerativeModel, configure, types

# Импорт функций для работы с базой данных (файл db.py)
import db

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
    ],
    1: [
        "Оцените свою работоспособность: насколько вы чувствуете себя работоспособным или разбитым (при 1 – совершенно разбитым, а 7 – на пике работоспособности)",
        "Оцените уровень своих сил: чувствуете ли вы себя полным сил или обессиленным (при 1 – абсолютно обессиленным, а 7 – полон энергии)",
        "Оцените скорость ваших мыслей или действий: насколько вы ощущаете себя медлительным или быстрым (при 1 – крайне медлительным, а 7 – исключительно быстрым)",
        "Оцените вашу активность: насколько вы чувствуете себя бездеятельным или деятельным (при 1 – полностью бездеятельным, а 7 – очень деятельным)",
        "Оцените своё счастье: насколько вы ощущаете себя счастливым или несчастным (при 1 – крайне несчастным, а 7 – чрезвычайно счастливым)",
        "Оцените вашу жизнерадостность: насколько вы чувствуете себя жизнерадостным или мрачным (при 1 – полностью мрачным, а 7 – исключительно жизнерадостным)"
    ],
    2: [
        "Оцените, насколько вы чувствуете напряжение или расслабленность (при 1 – невероятно напряжённый, а 7 – совершенно расслабленный)",
        "Оцените ваше здоровье: ощущаете ли вы себя здоровым или больным (при 1 – крайне больным, а 7 – абсолютно здоровым)",
        "Оцените вашу вовлечённость: насколько вы чувствуете себя безучастным или увлечённым (при 1 – совершенно безучастным, а 7 – полностью увлечённым)",
        "Оцените, насколько вы равнодушны или заинтересованы (при 1 – крайне равнодушны, а 7 – чрезвычайно заинтересованы)",
        "Оцените ваш эмоциональный подъем: насколько вы чувствуете восторг или уныние (при 1 – совершенно унылый, а 7 – безмерно восторженный)",
        "Оцените вашу радость: насколько вы чувствуете радость или печаль (при 1 – крайне печальный, а 7 – исключительно радостный)"
    ],
    3: [
        "Оцените, насколько вы чувствуете себя отдохнувшим или усталым (при 1 – совершенно усталым, а 7 – полностью отдохнувшим)",
        "Оцените, насколько вы ощущаете свежесть или изнурённость (при 1 – абсолютно изнурённый, а 7 – исключительно свежий)",
        "Оцените уровень своей сонливости или возбуждения (при 1 – крайне сонливый, а 7 – невероятно возбуждённый)",
        "Оцените, насколько у вас желание отдохнуть или работать (при 1 – исключительно желание отдохнуть, а 7 – сильное желание работать)",
        "Оцените ваше спокойствие: насколько вы чувствуете себя взволнованным или спокойным (при 1 – полностью взволнованным, а 7 – исключительно спокойным)",
        "Оцените ваш оптимизм: насколько вы чувствуете себя пессимистичным или оптимистичным (при 1 – крайне пессимистичным, а 7 – чрезвычайно оптимистичным)"
    ],
    4: [
        "Оцените вашу выносливость: насколько вы чувствуете себя выносливым или утомляемым (при 1 – совершенно утомляемым, а 7 – исключительно выносливым)",
        "Оцените уровень вашей бодрости: насколько вы чувствуете себя бодрым или вялым (при 1 – крайне вялым, а 7 – полностью бодрым)",
        "Оцените способность соображать: насколько вам сложно или легко соображать (при 1 – соображать крайне трудно, а 7 – соображать очень легко)",
        "Оцените вашу внимательность: насколько вы чувствуете себя рассеянным или внимательным (при 1 – совершенно рассеянным, а 7 – исключительно внимательным)",
        "Оцените вашу надежду: насколько вы чувствуете себя разочарованным или полным надежд (при 1 – полностью разочарованным, а 7 – полон надежд)",
        "Оцените ваше удовлетворение: насколько вы чувствуете себя недовольным или довольным (при 1 – абсолютно недовольным, а 7 – исключительно довольным)"
    ],
    5: [
        "Оцените ваше бодрствование: насколько вы чувствуете себя сонным или бодрствующим (при 1 – крайне сонным, а 7 – совершенно бодрствующим)",
        "Оцените, насколько вы чувствуете себя напряжённым или расслабленным (при 1 – невероятно напряжённым, а 7 – абсолютно расслабленным)",
        "Оцените, насколько вы ощущаете свежесть или утомлённость (при 1 – совершенно утомлённый, а 7 – исключительно свежий)",
        "Оцените ваше здоровье: насколько вы ощущаете себя нездоровым или здоровым (при 1 – абсолютно нездоровым, а 7 – полностью здоровым)",
        "Оцените уровень вашей энергии: насколько вы чувствуете себя вялым или энергичным (при 1 – чрезвычайно вялым, а 7 – исключительно энергичным)",
        "Оцените вашу решительность: насколько вы чувствуете себя колеблющимся или решительным (при 1 – совершенно колеблющимся, а 7 – исключительно решительным)"
    ],
    6: [
        "Оцените, насколько вы чувствуете себя сосредоточенным или рассеянным (при 1 – невероятно рассеянный, а 7 – чрезвычайно сосредоточенный)",
        "Оцените, насколько вы чувствуете себя пассивным или деятельным (при 1 – полностью пассивным, а 7 – исключительно деятельным)",
        "Оцените ваш оптимизм: насколько вы чувствуете себя пессимистичным или оптимистичным (при 1 – крайне пессимистичным, а 7 – чрезвычайно оптимистичным)",
        "Оцените ваше спокойствие: насколько вы чувствуете себя взволнованным или спокойным (при 1 – совершенно взволнованным, а 7 – исключительно спокойным)",
        "Оцените вашу уверенность: насколько вы чувствуете себя неуверенным или уверенным (при 1 – абсолютно неуверенным, а 7 – полностью уверенным)",
        "Оцените ваше удовлетворение: насколько вы чувствуете себя недовольным или довольным (при 1 – крайне недовольным, а 7 – исключительно довольным)"
    ]
}

OPEN_QUESTIONS = [
    "7. Какие три слова лучше всего описывают ваше текущее состояние?",
    "8. Что больше всего повлияло на ваше состояние сегодня?"
]

# ----------------------- Настройка директорий -----------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

REMINDER_DIR = os.path.join(BASE_DIR, "reminder")
os.makedirs(REMINDER_DIR, exist_ok=True)
os.chmod(REMINDER_DIR, 0o777)

# ----------------------- Состояния для ConversationHandler -----------------------
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
RETRO_CHOICE, RETRO_SCHEDULE_DAY = range(8, 10)
RETRO_CHAT = 10
AFTER_TEST_CHOICE, GEMINI_CHAT = range(11, 13)
REMINDER_CHOICE, REMINDER_DAILY_TIME, REMINDER_DAILY_REMIND, REMINDER_WEEKLY_DAY = range(100, 104)

# ----------------------- Вспомогательные функции -----------------------
def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [[str(i) for i in range(1, 8)], ["Главное меню"]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

async def exit_to_main(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    main_menu_keyboard = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Возвращаемся в главное меню.\n\nДобро пожаловать! Выберите действие:",
        reply_markup=reply_markup
    )
    return ConversationHandler.END

async def start(update: Update, context: CallbackContext) -> None:
    main_menu_keyboard = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

def remaining_days_in_month() -> int:
    today = datetime.now()
    _, last_day = monthrange(today.year, today.month)
    return last_day - today.day

def build_gemini_prompt_for_test(fixed_questions: list, test_answers: dict) -> str:
    prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
              "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, а 7 – исключительно позитивное состояние.\n"
              "Каждая шкала состоит из 2 вопросов (итоговый балл = сумма двух оценок, диапазон 2–14: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).\n"
              "Пожалуйста, выполните все вычисления итоговых баллов в уме без вывода промежуточных данных. "
              "Сформируйте один абзац общего анализа итоговых баллов и динамики состояния клиента, а затем сразу кратко опишите анализ открытых вопросов.\n"
              "Запрещается использование символа \"*\" для форматирования результатов.\n\n")
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

def build_followup_chat_prompt(user_message: str, chat_context: str) -> str:
    prompt = (
        "Вы — высококвалифицированный психолог с более чем десятилетним опытом работы, специализирующийся на клинической и консультативной психологии. "
        "Ваш профессионализм подкреплён глубокими академическими знаниями, полученными в ведущих университетах, а также постоянным совершенствованием навыков через участие в семинарах, конференциях и сертификационных программах. "
        "Вы известны своим тонким чувством эмпатии и умением выслушать, что позволяет Вам устанавливать доверительные отношения с клиентами, помогая им находить решения даже в самых сложных жизненных ситуациях. "
        "Ваш аналитический склад ума и системный подход к оценке психологического состояния клиента позволяют Вам не только выявлять коренные причины проблем, но и разрабатывать индивидуальные стратегии, направленные на восстановление внутренней гармонии и эмоционального равновесия. "
        "Вы всегда придерживаетесь принципов строгой конфиденциальности и этических стандартов, демонстрируя глубокое уважение к личному пространству каждого человека. "
        "Благодаря своему многолетнему опыту, Вы умеете адаптировать современные научные методики под уникальные особенности каждого клиента, способствуя развитию их личностного потенциала, устойчивости к стрессам и способности к саморегуляции. "
        "Ваш подход сочетает в себе научную строгость и гуманизм, что делает Вас незаменимым специалистом в помощи людям на пути к обретению эмоциональной устойчивости и жизненной гармонии.\n\n"
        "Этапы взаимодействия:\n"
        "1. Ежедневный опрос уже пройден – данные теста учтены, но не повторяйте их дословно.\n"
        "2. В дальнейшем ведите беседу, опираясь на контекст опроса.\n\n"
        "Запрещается использовать оформление с символом \"*\" для форматирования результатов.\n\n"
        "Контекст теста: " + chat_context + "\n\n"
        "Вопрос клиента: " + user_message
    )
    return prompt

async def call_gemini_api(prompt: str, max_tokens: int = 600) -> dict:
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

# ----------------------- Обработчики теста -----------------------
async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

async def test_start(update: Update, context: CallbackContext) -> int:
    context.user_data['test_answers'] = {}
    context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data['question_index'] = 0
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

    try:
        test_answers = context.user_data.get("test_answers", {})
        self_feeling = (int(test_answers.get("fixed_1")) + int(test_answers.get("fixed_2"))) / 2
        activity = (int(test_answers.get("fixed_3")) + int(test_answers.get("fixed_4"))) / 2
        mood = (int(test_answers.get("fixed_5")) + int(test_answers.get("fixed_6"))) / 2
        chat_context = f"Самочувствие: {self_feeling}, Активность: {activity}, Настроение: {mood}. Открытые ответы учтены."
    except Exception as e:
        logger.error(f"Ошибка при формировании контекста опроса: {e}")
        chat_context = "Данные теста учтены."
    context.user_data["chat_context"] = chat_context

    message = (
        f"Результат анализа:\n{interpretation}\n\n"
        "Теперь вы можете общаться с ИИ-психологом по результатам теста. Отправляйте свои сообщения, "
        "и они будут учитываться в рамках этого чата.\n"
        "Для выхода в главное меню нажмите кнопку «Главное меню»."
    )
    await update.message.reply_text(
        message,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    await update.message.reply_text(
        "Вы выбрали дальнейшее действие после теста. (Функциональность ещё не реализована, переходим к чату с ИИ.)",
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

async def gemini_chat_handler(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text.strip()
    if user_input.lower() == "главное меню":
        return await exit_to_main(update, context)
    chat_context = context.user_data.get("chat_context", "")
    prompt = build_followup_chat_prompt(user_input, chat_context)
    gemini_response = await call_gemini_api(prompt)
    answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return GEMINI_CHAT

# ----------------------- Функции для ретроспективы -----------------------
def build_gemini_prompt_for_retro(averages: dict, test_count: int) -> str:
    prompt = f"Ретроспектива: за последнюю неделю проведено {test_count} тестов.\n"
    prompt += "Средние показатели:\n"
    for key, value in averages.items():
        prompt += f"{key}: {value if value is not None else 'не указано'}\n"
    prompt += "Пожалуйста, сформируйте аналитический отчет по динамике состояния клиента за неделю."
    return prompt

def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
    prompt = (
        "Анализ данных ретроспективы за последнюю неделю:\n" +
        week_overview +
        "\n\nВопрос клиента: " + user_message
    )
    return prompt

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
    pool = context.bot_data.get('db_pool')
    if pool is None:
        await update.message.reply_text("Ошибка подключения к БД.")
        return ConversationHandler.END
    await db.upsert_weekly_retrospective(pool, user_id, day_number)
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
    gemini_response = await call_gemini_api(prompt, max_tokens=600)
    answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
    await update.message.reply_text(
        answer,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return RETRO_CHAT

# ----------------------- Обработчики раздела "Напоминание" с использованием БД -----------------------
async def reminder_start(update: Update, context: CallbackContext) -> int:
    keyboard = [["Ежедневный тест", "Ретроспектива"], ["Главное меню"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите тип напоминания:", reply_markup=reply_markup)
    return REMINDER_CHOICE

async def reminder_choice_handler(update: Update, context: CallbackContext) -> int:
    user_choice = update.message.text.strip().lower()
    if user_choice == "ежедневный тест":
        await update.message.reply_text("Введите время в формате ЧЧ:ММ для напоминания о ежедневном тесте:")
        return REMINDER_DAILY_REMIND
    elif user_choice == "ретроспектива":
        await update.message.reply_text("Введите день недели для ретроспективы (например, понедельник):")
        return REMINDER_WEEKLY_DAY
    else:
        return await exit_to_main(update, context)

async def reminder_set_daily(update: Update, context: CallbackContext) -> int:
    reminder_time_str = update.message.text.strip()  # ожидается формат "HH:MM"
    user_id = update.message.from_user.id
    try:
        datetime.strptime(reminder_time_str, "%H:%M")
    except ValueError:
        await update.message.reply_text("Неверный формат времени. Введите время в формате ЧЧ:ММ.")
        return REMINDER_DAILY_REMIND

    pool = context.bot_data.get('db_pool')
    if pool is None:
        await update.message.reply_text("Ошибка подключения к БД.")
        return ConversationHandler.END

    await db.upsert_daily_reminder(pool, user_id, reminder_time_str)
    await update.message.reply_text(
        "Напоминание о ежедневном тесте установлено!",
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return ConversationHandler.END

async def reminder_set_weekly(update: Update, context: CallbackContext) -> int:
    day_text = update.message.text.strip().lower()
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
        await update.message.reply_text("Неверный ввод. Введите корректный день недели (например, понедельник).")
        return REMINDER_WEEKLY_DAY
    day_number = days_mapping[day_text]
    user_id = update.message.from_user.id
    pool = context.bot_data.get('db_pool')
    if pool is None:
        await update.message.reply_text("Ошибка подключения к БД.")
        return ConversationHandler.END
    await db.upsert_weekly_retrospective(pool, user_id, day_number)
    await update.message.reply_text(
        "Напоминание о ретроспективе установлено!",
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )
    return ConversationHandler.END

# ----------------------- Фоновые задачи для отправки напоминаний -----------------------
async def daily_reminder_scheduler(app, pool):
    """Фоновая задача для отправки ежедневных напоминаний из БД."""
    while True:
        now = datetime.now().time()
        reminders = await db.get_active_daily_reminders(pool)
        for reminder in reminders:
            if reminder["last_sent"] is None or reminder["last_sent"] < date.today():
                if now >= reminder["reminder_time"]:
                    try:
                        await app.bot.send_message(
                            chat_id=reminder["user_id"],
                            text="Напоминание: пора пройти ежедневный тест!"
                        )
                        await db.update_last_sent_daily(pool, reminder["user_id"])
                    except Exception as e:
                        logger.error(f"Ошибка отправки напоминания пользователю {reminder['user_id']}: {e}")
        await asyncio.sleep(60)

async def weekly_retrospective_scheduler(app, pool):
    """Фоновая задача для отправки напоминаний о ретроспективе из БД."""
    while True:
        now = datetime.now()
        weekday = now.weekday()
        reminders = await db.get_active_weekly_retrospectives(pool)
        for reminder in reminders:
            if reminder["retrospective_day"] == weekday and (reminder["last_sent"] is None or reminder["last_sent"] < date.today()):
                try:
                    await app.bot.send_message(
                        chat_id=reminder["user_id"],
                        text="Напоминание: сегодня день ретроспективы!"
                    )
                    await db.update_last_sent_weekly(pool, reminder["user_id"])
                except Exception as e:
                    logger.error(f"Ошибка отправки ретроспективного напоминания пользователю {reminder['user_id']}: {e}")
        await asyncio.sleep(3600)

# ----------------------- Дополнительные команды -----------------------
async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "Наш бот предназначен для оценки вашего состояния с помощью короткого теста.\n\n"
        "Команды:\n"
        "• Тест – пройти тест (фиксированные вопросы, зависящие от дня недели, и 2 открытых вопроса).\n"
        "• Ретроспектива – анализ изменений за последнюю неделю и обсуждение итогов.\n"
        "• Напоминание – установить напоминание для прохождения теста.\n"
        "• Помощь – справочная информация.\n\n"
        "Во всех этапах работы доступна кнопка «Главное меню» для возврата в стартовое меню."
    )
    await update.message.reply_text(
        help_text,
        reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
    )

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Ошибка при обработке обновления {update}: {context.error}")

# ----------------------- Асинхронная основная функция -----------------------
async def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
        return

    app = Application.builder().token(TOKEN).build()

    # Если JobQueue не установлен, создаем его вручную
    if app.job_queue is None:
        from telegram.ext import JobQueue
        job_queue = JobQueue()
        await job_queue.start()
        app.job_queue = job_queue

    # Обработчик теста
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
        fallbacks=[
            CommandHandler("cancel", test_cancel),
            MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
        ],
        allow_reentry=True
    )
    app.add_handler(test_conv_handler)

    # Обработчик ретроспективы
    retro_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective_start)],
        states={
            RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_choice_handler)],
            RETRO_SCHEDULE_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_schedule_day)],
            RETRO_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_chat_handler)]
        },
        fallbacks=[
            CommandHandler("cancel", test_cancel),
            MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
        ],
        allow_reentry=True
    )
    app.add_handler(retro_conv_handler)

    # Обработчик напоминаний
    reminder_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Напоминание$"), reminder_start)],
        states={
            REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_choice_handler)],
            REMINDER_DAILY_REMIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_set_daily)],
            REMINDER_WEEKLY_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_set_weekly)]
        },
        fallbacks=[MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
        allow_reentry=True
    )
    app.add_handler(reminder_conv_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))
    app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main))
    app.add_error_handler(error_handler)

    # Создаем пул соединений с БД и сохраняем его в bot_data
    db_pool = await db.create_db_pool()
    app.bot_data['db_pool'] = db_pool

    # Запуск фоновых задач для отправки напоминаний
    app.job_queue.run_once(lambda ctx: asyncio.create_task(daily_reminder_scheduler(app, db_pool)), when=0)
    app.job_queue.run_once(lambda ctx: asyncio.create_task(weekly_retrospective_scheduler(app, db_pool)), when=0)

    # Асинхронная инициализация и запуск бота
    await app.initialize()
    await app.start_polling()
    await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
