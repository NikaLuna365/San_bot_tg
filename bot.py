import os
import json
import logging
import asyncio
from datetime import datetime, timedelta

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler, filters, CallbackContext
)

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Определение директорий
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Определение состояний для прохождения теста
(
    TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6,
    TEST_OPEN_1, TEST_OPEN_2, EDIT_CONFIRM, EDITING
) = range(10)

# Вопросы теста
FIXED_QUESTIONS = [
    "1. Как вы оцениваете свое физическое состояние сейчас?",
    "2. Чувствуете ли вы себя бодрым/здоровым?",
    "3. Чувствуете ли вы себя энергичным?",
    "4. Чувствуете ли вы усталость или необходимость отдохнуть?",
    "5. Как вы оцениваете свое настроение сейчас?",
    "6. Чувствуете ли вы себя позитивно или негативно настроенным?"
]

OPEN_QUESTIONS = [
    "7. Какие три слова лучше всего описывают ваше текущее состояние?",
    "8. Что больше всего повлияло на ваше состояние сегодня?"
]

ALL_QUESTIONS = FIXED_QUESTIONS + OPEN_QUESTIONS


def build_fixed_options_keyboard() -> ReplyKeyboardMarkup:
    """Возвращает клавиатуру с вариантами от 1 до 7 для фиксированных вопросов."""
    options = [str(i) for i in range(1, 8)]
    return ReplyKeyboardMarkup([options], resize_keyboard=True, one_time_keyboard=True)


def format_test_summary(answers: dict) -> str:
    """Формирует сводку ответов теста для пользователя."""
    summary = "Ваши ответы:\n"
    for i, question in enumerate(ALL_QUESTIONS, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i-6}"
        answer = answers.get(key, "не указано")
        summary += f"{i}. {question}\n   Ответ: {answer}\n"
    return summary


# Обработчик команды /start – выводит основное меню
async def start(update: Update, context: CallbackContext) -> None:
    keyboard = [["Тест", "Ретроспектива", "Помощь"]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text("Выберите действие:", reply_markup=reply_markup)


# Начало прохождения теста
async def test_start(update: Update, context: CallbackContext) -> int:
    context.user_data['test_answers'] = {}
    context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data["fixed_index"] = 0
    await update.message.reply_text(FIXED_QUESTIONS[0], reply_markup=build_fixed_options_keyboard())
    return TEST_FIXED_1


# Обработка фиксированных вопросов (от 1 до 6)
async def test_fixed_handler(update: Update, context: CallbackContext) -> int:
    current_index = context.user_data.get("fixed_index", 0)
    answer = update.message.text.strip()
    if answer not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text("Пожалуйста, выберите вариант от 1 до 7.")
        return TEST_FIXED_1 + current_index
    context.user_data['test_answers'][f"fixed_{current_index + 1}"] = answer
    current_index += 1
    context.user_data["fixed_index"] = current_index
    if current_index < len(FIXED_QUESTIONS):
        await update.message.reply_text(FIXED_QUESTIONS[current_index], reply_markup=build_fixed_options_keyboard())
        return TEST_FIXED_1 + current_index
    else:
        # Переход к открытым вопросам – убираем клавиатуру
        await update.message.reply_text(OPEN_QUESTIONS[0], reply_markup=ReplyKeyboardRemove())
        return TEST_OPEN_1


# Обработка первого открытого вопроса
async def test_open_1(update: Update, context: CallbackContext) -> int:
    answer = update.message.text.strip()
    context.user_data['test_answers']['open_1'] = answer
    await update.message.reply_text(OPEN_QUESTIONS[1])
    return TEST_OPEN_2


# Обработка второго открытого вопроса
async def test_open_2(update: Update, context: CallbackContext) -> int:
    answer = update.message.text.strip()
    context.user_data['test_answers']['open_2'] = answer
    summary = format_test_summary(context.user_data['test_answers'])
    await update.message.reply_text(
        summary + "\nВведите номер вопрос (от 1 до 8) для редактирования или введите 'готово' для подтверждения.",
        reply_markup=ReplyKeyboardRemove()
    )
    return EDIT_CONFIRM


# Обработка редактирования – ожидание команды: номер вопроса или "готово"
async def edit_confirm(update: Update, context: CallbackContext) -> int:
    text = update.message.text.strip().lower()
    if text == "готово":
        return await finalize_test(update, context)
    elif text.isdigit():
        num = int(text)
        if 1 <= num <= 8:
            context.user_data['edit_question'] = num
            question_text = ALL_QUESTIONS[num - 1]
            if num <= 6:
                await update.message.reply_text(
                    f"Введите новый ответ для вопрос {num}:\n{question_text}",
                    reply_markup=build_fixed_options_keyboard()
                )
            else:
                await update.message.reply_text(
                    f"Введите новый ответ для вопрос {num}:\n{question_text}",
                    reply_markup=ReplyKeyboardRemove()
                )
            return EDITING
        else:
            await update.message.reply_text("Неверный номер вопроса. Введите число от 1 до 8 или 'готово'.")
            return EDIT_CONFIRM
    else:
        await update.message.reply_text("Пожалуйста, введите номер вопроса (1-8) для редактирования или 'готово' для подтверждения.")
        return EDIT_CONFIRM


# Обработка редактирования – получение нового ответа
async def edit_answer(update: Update, context: CallbackContext) -> int:
    new_answer = update.message.text.strip()
    num = context.user_data.get('edit_question')
    if not num:
        await update.message.reply_text("Ошибка редактирования. Попробуйте снова.")
        return EDIT_CONFIRM
    key = f"fixed_{num}" if num <= 6 else f"open_{num - 6}"
    if num <= 6 and new_answer not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text("Пожалуйста, выберите вариант от 1 до 7.")
        return EDITING
    context.user_data['test_answers'][key] = new_answer
    summary = format_test_summary(context.user_data['test_answers'])
    await update.message.reply_text(
        summary + "\nВведите номер вопрос (1-8) для редактирования или 'готово' для подтверждения.",
        reply_markup=ReplyKeyboardRemove()
    )
    return EDIT_CONFIRM


# Завершение теста: сохранение данных, формирование промпта и вызов Gemini API
async def finalize_test(update: Update, context: CallbackContext) -> int:
    user_id = update.message.from_user.id
    test_start_time = context.user_data.get('test_start_time', datetime.now().strftime("%Y%m%d_%H%M%S"))
    filename = os.path.join(DATA_DIR, f"{user_id}_{test_start_time}.json")
    test_data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "test_answers": context.user_data.get("test_answers", {})
    }
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(test_data, f, ensure_ascii=False, indent=4)
        logger.info(f"JSON файл создан: {filename}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении JSON файла: {e}")
        await update.message.reply_text("Произошла ошибка при сохранении данных теста.")
        return ConversationHandler.END

    prompt = build_gemini_prompt(context.user_data.get("test_answers", {}))
    gemini_response = await call_gemini_api(prompt)
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
    await update.message.reply_text(f"Результат анализа:\n{interpretation}")
    return ConversationHandler.END


def build_gemini_prompt(test_answers: dict) -> str:
    """
    Формирует промпт для Gemini API, представляя его в роли психолога.
    В промпте перечисляются вопросы теста и ответы пользователя.
    """
    prompt = "Ты выступаешь в роли психолога. Проведи анализ результатов следующего тестирования состояния пользователя.\n\n"
    for i, question in enumerate(ALL_QUESTIONS, start=1):
        key = f"fixed_{i}" if i <= 6 else f"open_{i - 6}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    prompt += "\nСформируй интерпретацию результатов в формате: 'Ваше тестирование показывает, ваше положение на сегодня...'"
    return prompt


async def call_gemini_api(prompt: str) -> dict:
    """
    Имитирует вызов Gemini API.
    Для реальной интеграции необходимо заменить этот блок на HTTP-запрос к API,
    используя GEMINI_API_KEY из переменных окружения.
    """
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY не найден в переменных окружения.")
        return {"interpretation": "Ошибка: не настроен GEMINI_API_KEY."}

    logger.info(f"Отправка запроса к Gemini API с промптом:\n{prompt}")
    await asyncio.sleep(1)  # имитация задержки запроса
    # Симулированный ответ от Gemini API:
    return {
        "interpretation": (
            "Ваше тестирование показывает, что на сегодня вы демонстрируете сбалансированное физическое состояние, "
            "оптимальный уровень активности и позитивное настроение. Рекомендуется продолжать поддерживать здоровый образ жизни."
        )
    }


# Обработка команды "Ретроспектива"
async def retrospective(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    files = [f for f in os.listdir(DATA_DIR) if f.startswith(f"{user_id}_") and f.endswith(".json")]
    if not files:
        await update.message.reply_text("Данных для анализа пока нет. Пройдите тест хотя бы один раз.")
        return

    one_week_ago = datetime.now() - timedelta(days=7)
    tests = []
    for file in files:
        file_path = os.path.join(DATA_DIR, file)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ts = datetime.strptime(data.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
                if ts >= one_week_ago:
                    tests.append(data)
        except Exception as e:
            logger.error(f"Ошибка при чтении файла {file_path}: {e}")

    if not tests:
        await update.message.reply_text("Нет данных за последнюю неделю для анализа.")
        return

    # Вычисление средних оценок по категориям
    sums = {"fixed_1": 0, "fixed_2": 0, "fixed_3": 0, "fixed_4": 0, "fixed_5": 0, "fixed_6": 0}
    counts = {"fixed_1": 0, "fixed_2": 0, "fixed_3": 0, "fixed_4": 0, "fixed_5": 0, "fixed_6": 0}
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

    summary = {
        "user_id": user_id,
        "test_count": len(tests),
        "averages": averages,
        "analysis_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    prompt = (
        "Ты выступаешь в роли психолога. Проведи ретроспективный анализ данных тестирования за последнюю неделю.\n\n"
        f"Количество тестов: {summary['test_count']}\n"
    )
    for category, avg in averages.items():
        prompt += f"{category}: {avg}\n"
    prompt += "\nСформируй интерпретацию в формате: 'Ваше тестирование показывает, ваше положение на сегодня...'"
    
    gemini_response = await call_gemini_api(prompt)
    interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
    await update.message.reply_text(f"Ретроспективный анализ за последнюю неделю:\n{interpretation}")


# Обработчик команды "Помощь"
async def help_command(update: Update, context: CallbackContext) -> None:
    help_text = (
        "Доступные команды:\n"
        "Тест – пройти тест состояния (6 фиксированных вопросов и 2 открытых, ответы сохраняются в JSON).\n"
        "Ретроспектива – получить анализ ваших данных за последнюю неделю.\n"
        "Помощь – справка по боту."
    )
    await update.message.reply_text(help_text)


# Обработчик отмены теста
async def test_cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# Функция для рассылки еженедельных отчетов (запланированная задача)
async def send_weekly_reports(app: Application):
    logger.info("Запуск рассылки еженедельных отчетов")
    # Здесь необходимо реализовать логику перебора зарегистрированных пользователей и отправки им отчётов.
    # В этом примере просто логируется выполнение задачи.
    pass


def schedule_jobs(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: asyncio.create_task(send_weekly_reports(app)),
                      CronTrigger(day_of_week="mon", hour=9, minute=0))
    scheduler.start()


# Глобальный обработчик ошибок
async def error_handler(update: object, context: CallbackContext) -> None:
    logger.error(f"Exception while handling update {update}: {context.error}")


def main() -> None:
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
        return

    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тест$"), test_start)],
        states={
            TEST_FIXED_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_4: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_5: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_FIXED_6: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_fixed_handler)],
            TEST_OPEN_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_open_1)],
            TEST_OPEN_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, test_open_2)],
            EDIT_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_confirm)],
            EDITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_answer)],
        },
        fallbacks=[CommandHandler("cancel", test_cancel)],
        allow_reentry=True,
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.Regex("^Ретроспектива$"), retrospective))
    app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))
    app.add_error_handler(error_handler)

    schedule_jobs(app)
    app.run_polling()


if __name__ == "__main__":
    main()
