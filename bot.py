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

# Импорт загрузчика переменных окружения и SDK Yandex Cloud
from dotenv import load_dotenv
from yandexcloud import SDK

# Загружаем переменные из .env
load_dotenv()

# Получаем API-ключи для Yandex GPT
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_KEY_ID = os.getenv("YANDEX_KEY_ID")

if not YANDEX_API_KEY or not YANDEX_KEY_ID:
    raise ValueError("Ошибка: Не настроены API-ключи Yandex GPT. Проверьте .env файл.")

# Определяем DATA_DIR: берем из переменной окружения или ставим значение по умолчанию
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.info(f"YANDEX_API_KEY загружен: {bool(YANDEX_API_KEY)}")
logger.info(f"YANDEX_KEY_ID загружен: {bool(YANDEX_KEY_ID)}")
logger.info(f"DATA_DIR установлен: {DATA_DIR}")

# ----------------------- Состояния для ConversationHandler -----------------------
# Состояния прохождения теста (6 фиксированных вопросов + 2 открытых)
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
# Состояния для ретроспективы
RETRO_CHOICE, RETRO_SCHEDULE_DAY = range(8, 10)
# Состояния после теста: выбор дальнейших действий и режим общения с Yandex GPT
AFTER_TEST_CHOICE, YANDEX_CHAT = range(10, 12)

# Глобальный словарь для запланированных ретроспектив (user_id -> weekday, где 0 – понедельник, …, 6 – воскресенье)
scheduled_retrospectives = {}

# ----------------------- Вопросы теста -----------------------
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

# ----------------------- Функции формирования промптов -----------------------

def build_prompt_for_test(test_answers: dict) -> str:
    """
    Формирует промпт для анализа теста.
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

def build_prompt_for_retro(averages: dict, test_count: int) -> str:
    """
    Формирует промпт для ретроспективного анализа.
    """
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

def build_prompt_for_chat(user_message: str, test_answers: dict) -> str:
    """
    Формирует промпт для общения с Yandex GPT.
    Включает результаты последнего теста для обеспечения контекста.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент недавно прошёл психологический тест. Ниже приведены его ответы. "
        "Проанализируйте их и ответьте на вопрос клиента, используя научные термины и принципы когнитивной психологии, "
        "при необходимости задайте уточняющие вопросы."
    )
    prompt = standard + "\n\n"
    prompt += "Результаты последнего теста:\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nВопрос клиента: " + user_message + "\n"
    logger.info(f"Промпт для чата:\n{prompt}")
    return prompt

# ----------------------- Функция вызова Yandex GPT -----------------------

async def call_yandex_gpt(prompt: str) -> str:
    """Функция отправки запроса в Yandex GPT через официальный SDK"""
    try:
        logger.info(f"Отправка запроса в Yandex GPT:\n{prompt}")
        sdk = SDK(oauth_token=YANDEX_API_KEY)
        model = sdk.ai.gpt().completion("yandex.gpt")
        result = model.generate(prompt=prompt)
        logger.info(f"Ответ от Yandex GPT: {result}")
        if result and 'text' in result:
            return result['text']
        else:
            return "Ошибка: пустой ответ от Yandex GPT"
    except Exception as e:
        logger.error(f"Ошибка вызова Yandex GPT: {e}")
        return f"Ошибка вызова Yandex GPT: {e}"

# ----------------------- Основное меню и обработчики команд -----------------------

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

# ----------------------- Разговор для прохождения теста -----------------------

async def test_start(update: Update, context: CallbackContext) -> int:
    context.user_data['test_answers'] = {}
    context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data['question_index'] = 0
    await update.message.reply_text(FIXED_QUESTIONS[0], reply_markup=build_fixed_keyboard())
    return TEST_FIXED_1

def build_fixed_keyboard() -> ReplyKeyboardMarkup:
    options = [str(i) for i in range(1, 8)]
    return ReplyKeyboardMarkup([options], resize_keyboard=True, one_time_keyboard=True)

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

    # Сохранение данных теста в формате JSON
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

    # Сохраняем результаты теста для дальнейшего общения с Yandex GPT
    context.user_data["last_test_answers"] = context.user_data.get("test_answers", {})

    # Формирование промпта для анализа теста и вызов Yandex GPT
    prompt = build_prompt_for_test(context.user_data.get("test_answers", {}))
    response_text = await call_yandex_gpt(prompt)
    await update.message.reply_text(f"Результат анализа:\n{response_text}", reply_markup=ReplyKeyboardRemove())

    # Если для пользователя запланирована ретроспектива и сегодня выбранный день – запускаем анализ
    await check_and_run_scheduled_retrospective(update, context)

    # После теста предлагаем выбор дальнейших действий
    keyboard = [["Главное меню", "Пообщаться с Yandex GPT"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите дальнейшее действие:", reply_markup=reply_markup)
    return AFTER_TEST_CHOICE

async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ----------------------- Обработчик выбора после теста -----------------------

async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "главное меню":
        await update.message.reply_text("Возвращаемся в главное меню.", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return ConversationHandler.END
    elif choice == "пообщаться с yandex gpt":
        await update.message.reply_text(
            "Введите сообщение для общения с Yandex GPT. Для выхода в главное меню введите 'Главное меню'.",
            reply_markup=ReplyKeyboardRemove()
        )
        return YANDEX_CHAT
    else:
        await update.message.reply_text("Пожалуйста, выберите: 'Главное меню' или 'Пообщаться с Yandex GPT'.")
        return AFTER_TEST_CHOICE

async def yandex_chat_handler(update: Update, context: CallbackContext) -> int:
    user_message = update.message.text.strip()
    if user_message.lower() == "главное меню":
        await update.message.reply_text("Возвращаемся в главное меню.", reply_markup=ReplyKeyboardRemove())
        await start(update, context)
        return ConversationHandler.END
    # Извлекаем результаты последнего теста для контекста
    test_answers = context.user_data.get("last_test_answers", {})
    prompt = build_prompt_for_chat(user_message, test_answers)
    response_text = await call_yandex_gpt(prompt)
    await update.message.reply_text(response_text)
    return YANDEX_CHAT

# ----------------------- Разговор для ретроспективы -----------------------

async def retrospective_start(update: Update, context: CallbackContext) -> int:
    keyboard = [["Ретроспектива сейчас", "Запланировать ретроспективу"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите вариант ретроспективы:", reply_markup=reply_markup)
    return RETRO_CHOICE

async def retrospective_choice_handler(update: Update, context: CallbackContext) -> int:
    choice = update.message.text.strip().lower()
    if choice == "ретроспектива сейчас":
        await run_retrospective_now(update, context)
        return ConversationHandler.END
    elif choice == "запланировать ретроспективу":
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        reply_markup = ReplyKeyboardMarkup([days], resize_keyboard=True, one_time_keyboard=True)
        await update.message.reply_text("Выберите день недели для ретроспективы:", reply_markup=reply_markup)
        return RETRO_SCHEDULE_DAY
    else:
        await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.")
        return RETRO_CHOICE

async def retrospective_schedule_day(update: Update, context: CallbackContext) -> int:
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
        await update.message.reply_text("Неверный ввод. Пожалуйста, выберите день недели.")
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
                if ts >= one_week_ago:
                    tests.append(data)
        except Exception as e:
            logger.error(f"Ошибка чтения файла {file_path}: {e}")
    if len(tests) < 4:
        await update.message.reply_text("Недостаточно данных для ретроспективы. Пройдите тест минимум 4 раза за 7 дней.")
        return
    # Вычисляем средние показатели по фиксированным вопросам
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

    prompt = build_prompt_for_retro(averages, len(tests))
    response_text = await call_yandex_gpt(prompt)
    await update.message.reply_text(f"Ретроспектива за последнюю неделю:\n{response_text}")

# ----------------------- Автоматический запуск запланированной ретроспективы (после теста) -----------------------

async def check_and_run_scheduled_retrospective(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if user_id in scheduled_retrospectives:
        scheduled_day = scheduled_retrospectives[user_id]
        current_day = datetime.now().weekday()  # 0 – понедельник, 6 – воскресенье
        if current_day == scheduled_day:
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
                        if ts >= one_week_ago:
                            tests.append(data)
                except Exception as e:
                    continue
            if len(tests) >= 4:
                await update.message.reply_text("Запланированная ретроспектива запускается автоматически после нового теста:")
                await run_retrospective_now(update, context)
            else:
                await update.message.reply_text("Запланированная ретроспектива не выполнена: недостаточно данных (не менее 4 тестов за 7 дней).")

# ----------------------- Команда "Помощь" -----------------------

async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "Наш бот предназначен для оценки вашего текущего состояния с помощью короткого теста.\n\n"
        "Команды:\n"
        "• Тест – пройти тест состояния (6 фиксированных вопросов с оценкой от 1 до 7 и 2 открытых вопроса).\n\n"
        "• Ретроспектива – анализ изменений вашего состояния за последнюю неделю. Вы можете выбрать:\n"
        "   – Ретроспектива сейчас (если у вас минимум 4 теста за 7 дней),\n"
        "   – Запланировать ретроспективу (выберите день недели – отчет выполнится автоматически после нового теста в этот день).\n\n"
        "• Помощь – справочная информация.\n\n"
        "После теста вы можете перейти в режим общения с Yandex GPT, где бот будет отвечать на ваши вопросы, "
        "учитывая результаты последнего теста. Для выхода из режима общения введите 'Главное меню'.\n\n"
        "По вопросам доработки и предложений обращайтесь: @Nik_Ly."
    )
    await update.message.reply_text(help_text, reply_markup=ReplyKeyboardRemove())

# ----------------------- Глобальный обработчик ошибок -----------------------

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Ошибка при обработке обновления {update}: {context.error}")

# ----------------------- Основная функция -----------------------

def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
        return

    app = Application.builder().token(TOKEN).build()

    # ConversationHandler для теста и общения с Yandex GPT
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
            YANDEX_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, yandex_chat_handler)]
        },
        fallbacks=[CommandHandler("cancel", test_cancel)],
        allow_reentry=True
    )
    app.add_handler(test_conv_handler)

    # ConversationHandler для ретроспективы
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

    # Команды /start и /help, а также обработка кнопки "Помощь"
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))

    app.add_error_handler(error_handler)
    app.run_polling()

if __name__ == "__main__":
    main()
