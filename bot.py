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

from dotenv import load_dotenv

import grpc
from yandex.cloud.auth import Authenticator
from yandex.cloud.ai.llm.v1.text_generation_service_pb2 import TextGenerationRequest
from yandex.cloud.ai.llm.v1.text_generation_service_pb2_grpc import TextGenerationServiceStub

# Загружаем переменные окружения из .env файла
load_dotenv()

# Получение учетных данных для Яндекс
YANDEX_IAM_TOKEN = os.getenv("YANDEX_IAM_TOKEN")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH", None)
if not YANDEX_IAM_TOKEN and not SERVICE_ACCOUNT_KEY_PATH:
    raise ValueError("Ошибка: необходимо задать YANDEX_IAM_TOKEN или SERVICE_ACCOUNT_KEY_PATH в переменных окружения.")

def create_yandex_gpt_client():
    """
    Создает клиента для работы с Yandex GPT с использованием актуальной авторизации.
    Если задан IAM-токен, используется он, иначе – ключ сервисного аккаунта.
    """
    if YANDEX_IAM_TOKEN:
        authenticator = Authenticator(iam_token=YANDEX_IAM_TOKEN)
    else:
        authenticator = Authenticator(service_account_key=SERVICE_ACCOUNT_KEY_PATH)
    channel = grpc.secure_channel('llm.api.cloud.yandex.net:443', authenticator)
    return TextGenerationServiceStub(channel)

# Инициализируем клиента Yandex GPT
gpt_client = create_yandex_gpt_client()

# Каталог для хранения JSON с тестовыми данными
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.info(f"YANDEX_IAM_TOKEN загружен: {bool(YANDEX_IAM_TOKEN)}")
logger.info(f"DATA_DIR установлен: {DATA_DIR}")

# Определение состояний для диалога (ConversationHandler)
TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
RETRO_CHOICE, RETRO_SCHEDULE_DAY = range(8, 10)
AFTER_TEST_CHOICE, YANDEX_CHAT = range(10, 12)

# Глобальный словарь для запланированных ретроспектив (user_id -> день недели, где 0 – понедельник, ... 6 – воскресенье)
scheduled_retrospectives = {}

# Вопросы для теста
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

def build_prompt_for_test(test_answers: dict) -> str:
    """
    Формирует текст запроса для анализа теста.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент прошёл ежедневный психологический тест. "
        "Проанализируйте его ответы, дайте развернутую интерпретацию состояния, "
        "используя научные термины и принципы когнитивной психологии. "
        "При необходимости задайте уточняющие вопросы."
    )
    prompt = standard + "\n\n"
    all_questions = FIXED_QUESTIONS + OPEN_QUESTIONS
    for i, question in enumerate(all_questions, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nСформируй развернутый анализ состояния клиента."
    logger.info("Промпт для теста сформирован")
    return prompt

def build_prompt_for_retro(averages: dict, test_count: int) -> str:
    """
    Формирует текст запроса для ретроспективного анализа.
    """
    standard = (
        "Вы профессиональный психолог с 10-летним стажем. "
        "Ваш клиент предоставил недельный тест. "
        "Проанализируйте изменения его состояния за неделю, учитывая открытые ответы, "
        "и дайте развернутую интерпретацию эмоционального состояния, используя научные термины."
    )
    prompt = standard + "\n\n"
    prompt += f"Количество тестов: {test_count}\n"
    for category, avg in averages.items():
        prompt += f"{category}: {avg}\n"
    prompt += "\nСформируй подробный анализ с рекомендациями."
    logger.info("Промпт для ретроспективы сформирован")
    return prompt

def build_prompt_for_chat(user_message: str, test_answers: dict) -> str:
    """
    Формирует текст запроса для общения с Yandex GPT с учетом результатов последнего теста.
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
    logger.info("Промпт для чата сформирован")
    return prompt

async def call_yandex_gpt(prompt: str) -> str:
    """
    Асинхронно отправляет запрос к Yandex GPT и возвращает сгенерированный текст.
    """
    try:
        logger.info("Отправка запроса в Yandex GPT")
        request = TextGenerationRequest(
            model="yandex-gpt",
            prompt=prompt,
            max_tokens=200
        )
        # Вызываем синхронный метод в отдельном потоке, чтобы не блокировать цикл событий
        response = await asyncio.to_thread(gpt_client.GenerateText, request)
        logger.info("Получен ответ от Yandex GPT")
        return response.text if response.text else "Ошибка: пустой ответ от Yandex GPT"
    except Exception as e:
        logger.error(f"Ошибка вызова Yandex GPT: {e}")
        return f"Ошибка вызова Yandex GPT: {e}"

# Основные обработчики команд и диалогов Telegram-бота

async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)

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

    # Сохраняем данные теста в формате JSON
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
        logger.info(f"Сохранены тестовые данные: {filename}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении теста: {e}")
        await update.message.reply_text("Ошибка при сохранении данных теста.")
        return ConversationHandler.END

    context.user_data["last_test_answers"] = context.user_data.get("test_answers", {})

    # Формируем запрос для Yandex GPT и отправляем его
    prompt = build_prompt_for_test(context.user_data.get("test_answers", {}))
    response_text = await call_yandex_gpt(prompt)
    await update.message.reply_text(f"Результат анализа:\n{response_text}", reply_markup=ReplyKeyboardRemove())

    # Если у пользователя запланирована ретроспектива, проверяем и запускаем её
    await check_and_run_scheduled_retrospective(update, context)

    # Предлагаем выбор дальнейших действий
    keyboard = [["Главное меню", "Пообщаться с Yandex GPT"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите дальнейшее действие:", reply_markup=reply_markup)
    return AFTER_TEST_CHOICE

async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

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
    test_answers = context.user_data.get("last_test_answers", {})
    prompt = build_prompt_for_chat(user_message, test_answers)
    response_text = await call_yandex_gpt(prompt)
    await update.message.reply_text(response_text)
    return YANDEX_CHAT

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
        await update.message.reply_text("Неверный ввод. Выберите день недели.")
        return RETRO_SCHEDULE_DAY
    day_number = days_mapping[day_text]
    user_id = update.message.from_user.id
    scheduled_retrospectives[user_id] = day_number
    await update.message.reply_text(
        f"Ретроспектива запланирована на {update.message.text.strip()}. После нового теста в этот день отчет будет сформирован автоматически.",
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

async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "Бот предназначен для оценки вашего состояния с помощью ежедневного теста.\n\n"
        "Команды:\n"
        "• Тест – пройти тест (6 фиксированных вопросов и 2 открытых).\n"
        "• Ретроспектива – анализ изменений за последнюю неделю. Вы можете выбрать:\n"
        "   – Ретроспектива сейчас (при наличии минимум 4 тестов за 7 дней),\n"
        "   – Запланировать ретроспективу (выбрать день недели для автоматического запуска после нового теста).\n"
        "• Помощь – справочная информация.\n\n"
        "После теста можно перейти в режим общения с Yandex GPT. Для выхода в главное меню введите 'Главное меню'.\n\n"
        "По вопросам доработки обращайтесь: @Nik_Ly."
    )
    await update.message.reply_text(help_text, reply_markup=ReplyKeyboardRemove())

async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Ошибка при обработке обновления {update}: {context.error}")

def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
        return

    app = Application.builder().token(TOKEN).build()

    # Обработчик для теста и общения с Yandex GPT
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

    # Обработчик для ретроспективы
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
