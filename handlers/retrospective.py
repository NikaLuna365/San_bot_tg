# handlers/retrospective.py
import logging
# import json # Больше не нужен для записи/чтения файлов
# import os # Больше не нужен для путей/листинга
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

# import aiofiles # Больше не нужен
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

# --- Импорты проекта ---
from constants import (
    State, RETRO_OPEN_QUESTIONS, # DATA_DIR больше не нужен
    RETRO_NOW_PERIOD_KEYBOARD, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
)
import gemini_client
import db # Добавлен импорт db
from utils import get_now_utc
from .common import exit_to_main

logger = logging.getLogger(__name__)

# --- Начало диалога ретроспективы ---

async def retrospective_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог мгновенной ретроспективы, спрашивая период."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} начал мгновенную ретроспективу.")
    await update.message.reply_text(
        "Ретроспектива помогает проанализировать динамику вашего состояния за прошедший период. "
        "За какой период провести ретроспективу?",
        reply_markup=RETRO_NOW_PERIOD_KEYBOARD
    )
    return State.RETRO_PERIOD_CHOICE

# --- Логика мгновенной ретроспективы ---

async def retrospective_period_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Обрабатывает выбор периода для мгновенной ретроспективы."""
    choice = update.message.text.strip()
    user_id = update.effective_user.id
    period_days = 0

    if choice == "За 1 неделю": period_days = 7
    elif choice == "За 2 недели": period_days = 14
    elif choice == "Главное меню": return await exit_to_main(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите 'За 1 неделю' или 'За 2 недели'.", reply_markup=RETRO_NOW_PERIOD_KEYBOARD)
        return State.RETRO_PERIOD_CHOICE

    logger.info(f"Пользователь {user_id} выбрал ретроспективу за {period_days} дней.")
    context.user_data["retro_period_days"] = period_days
    context.user_data["retro_answers"] = {} # Инициализируем словарь для ответов на открытые вопросы ретро

    await update.message.reply_text(f"Отлично. Теперь ответьте на несколько вопросов о прошедших {period_days} днях.\n\n{RETRO_OPEN_QUESTIONS[0]}", reply_markup=CANCEL_KEYBOARD)
    return State.RETRO_OPEN_1

async def retro_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, current_state: State, next_state: State) -> State | int:
    """Общий обработчик для открытых вопросов ретроспективы."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    q_index = current_state.value - State.RETRO_OPEN_1.value

    if user_input == "Главное меню":
        context.user_data.pop("retro_period_days", None)
        context.user_data.pop("retro_answers", None)
        context.user_data.pop("retro_chat_context", None)
        return await exit_to_main(update, context)

    # Сохраняем ответ в user_data
    context.user_data.setdefault("retro_answers", {})[f"retro_open_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered retro open Q{q_index+1}.")

    if q_index < len(RETRO_OPEN_QUESTIONS) - 1:
        await update.message.reply_text(RETRO_OPEN_QUESTIONS[q_index+1], reply_markup=CANCEL_KEYBOARD)
        return next_state
    else:
        # Последний открытый вопрос ретроспективы отвечен
        logger.info(f"User {user_id} finished retro open questions.")
        await update.message.reply_text("Спасибо! Собираю данные и готовлю анализ...", reply_markup=ReplyKeyboardRemove())
        success = await run_retrospective_analysis(update, context)
        if success:
            # Если анализ успешен, переходим в чат
            return State.GEMINI_CHAT_RETRO
        else:
            # Если анализ не удался (например, мало данных), возвращаемся в главное меню
            # Сообщение об ошибке уже отправлено внутри run_retrospective_analysis
            context.user_data.pop("retro_period_days", None)
            context.user_data.pop("retro_answers", None)
            # Завершаем диалог
            return ConversationHandler.END

# Генерация состояний и хендлеров для открытых вопросов ретро (без изменений)
retro_open_states = {}
for i in range(len(RETRO_OPEN_QUESTIONS)):
    current_state_enum = State(State.RETRO_OPEN_1.value + i)
    # Определяем следующее состояние: либо следующий вопрос, либо чат
    next_state_enum = State(State.RETRO_OPEN_1.value + i + 1) if i < len(RETRO_OPEN_QUESTIONS) - 1 else State.GEMINI_CHAT_RETRO
    # Создаем замыкание для передачи правильных состояний в обработчик
    def create_handler_wrapper(current_s, next_s):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
            # Передаем текущее и следующее состояния в общий обработчик
            return await retro_open_handler(update, context, current_s, next_s)
        return handler
    # Регистрируем обработчик для текущего состояния
    retro_open_states[current_state_enum] = [ MessageHandler(filters.TEXT & ~filters.COMMAND, create_handler_wrapper(current_state_enum, next_state_enum))]


async def run_retrospective_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Запускает анализ ретроспективы: получает данные из БД, считает средние,
    вызывает Gemini, сохраняет результат в БД и отправляет пользователю.
    Возвращает True в случае успеха, False если данных недостаточно или произошла ошибка.
    """
    user_id = update.effective_user.id
    period_days = context.user_data.get("retro_period_days", 7)
    pool = context.bot_data.get("db_pool")

    if not pool:
        logger.error(f"DB pool not found for user {user_id} in run_retrospective_analysis")
        await update.message.reply_text("Ошибка: не удалось подключиться к базе данных.", reply_markup=MAIN_MENU_KEYBOARD)
        return False # Ошибка

    now = get_now_utc()
    start_date = now - timedelta(days=period_days)
    logger.info(f"Запуск анализа ретроспективы для {user_id} за {period_days} дней (с {start_date.date()})")

    # --- ИЗМЕНЕНО: Получение данных из БД ---
    tests_in_period_records: List[asyncpg.Record] = []
    try:
        tests_in_period_records = await db.get_test_results_for_period(pool, user_id, start_date, end_date=now)
    except Exception as e:
        logger.exception(f"Ошибка при получении данных тестов из БД для user {user_id}:")
        await update.message.reply_text("Произошла ошибка при получении данных для анализа.", reply_markup=MAIN_MENU_KEYBOARD)
        return False # Ошибка

    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    test_count = len(tests_in_period_records)
    logger.info(f"Найдено {test_count} тестов для {user_id} за период.")

    MIN_TESTS_FOR_RETRO = 3
    if test_count < MIN_TESTS_FOR_RETRO:
        await update.message.reply_text(
            f"К сожалению, найдено слишком мало ({test_count}) данных за последние {period_days} дней. "
            f"Для анализа ретроспективы нужно хотя бы {MIN_TESTS_FOR_RETRO} пройденных теста за этот период.",
            reply_markup=MAIN_MENU_KEYBOARD
        )
        logger.warning(f"Недостаточно данных для ретроспективы user {user_id} ({test_count}<{MIN_TESTS_FOR_RETRO})")
        return False # Недостаточно данных

    # --- Расчет средних (логика остается, но данные берем из записей БД) ---
    sums: Dict[str, float] = {f"fixed_{i}": 0.0 for i in range(1, 7)}
    counts: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}

    for record in tests_in_period_records:
        # asyncpg возвращает JSONB как словари Python
        fixed_answers = record.get("fixed_answers")
        if not isinstance(fixed_answers, dict):
            logger.warning(f"Некорректный формат fixed_answers в записи БД для user {user_id}: {fixed_answers}")
            continue

        for i in range(1, 7):
            key = f"fixed_{i}"
            try:
                val_str = fixed_answers.get(key) # Ответы уже должны быть строками '1'...'7'
                if isinstance(val_str, str):
                    sums[key] += int(val_str)
                    counts[key] += 1
                # Добавим проверку на случай, если в БД попали числа
                elif isinstance(val_str, (int, float)):
                     sums[key] += float(val_str)
                     counts[key] += 1
                # Игнорируем другие типы или отсутствующие ключи
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Ошибка при обработке ответа {key}={val_str} из БД для user {user_id}: {e}")
                continue

    averages: Dict[str, Optional[float]] = {}
    try:
        # Самочувствие
        if counts["fixed_1"] > 0 and counts["fixed_2"] > 0:
            averages["Самочувствие"] = (sums["fixed_1"] / counts["fixed_1"] + sums["fixed_2"] / counts["fixed_2"]) / 2
        elif counts["fixed_1"] > 0: averages["Самочувствие"] = sums["fixed_1"] / counts["fixed_1"]
        elif counts["fixed_2"] > 0: averages["Самочувствие"] = sums["fixed_2"] / counts["fixed_2"]
        else: averages["Самочувствие"] = None

        # Активность
        if counts["fixed_3"] > 0 and counts["fixed_4"] > 0:
            averages["Активность"] = (sums["fixed_3"] / counts["fixed_3"] + sums["fixed_4"] / counts["fixed_4"]) / 2
        elif counts["fixed_3"] > 0: averages["Активность"] = sums["fixed_3"] / counts["fixed_3"]
        elif counts["fixed_4"] > 0: averages["Активность"] = sums["fixed_4"] / counts["fixed_4"]
        else: averages["Активность"] = None

        # Настроение
        if counts["fixed_5"] > 0 and counts["fixed_6"] > 0:
            averages["Настроение"] = (sums["fixed_5"] / counts["fixed_5"] + sums["fixed_6"] / counts["fixed_6"]) / 2
        elif counts["fixed_5"] > 0: averages["Настроение"] = sums["fixed_5"] / counts["fixed_5"]
        elif counts["fixed_6"] > 0: averages["Настроение"] = sums["fixed_6"] / counts["fixed_6"]
        else: averages["Настроение"] = None

    except ZeroDivisionError:
         logger.error(f"Деление на ноль при расчете средних для ретроспективы user {user_id}")
         averages = {k: None for k in ["Самочувствие", "Активность", "Настроение"]}

    # --- Получение интерпретации от Gemini ---
    retro_open_answers = context.user_data.get("retro_answers", {})
    prompt = gemini_client.build_gemini_prompt_for_retro(averages, test_count, retro_open_answers, period_days)
    interpretation = await gemini_client.call_gemini_api(prompt, max_tokens=800)

    # --- ИЗМЕНЕНО: Сохранение результатов ретроспективы в БД ---
    retro_id: Optional[int] = None
    try:
        retro_id = await db.save_retrospective_result(
            pool=pool,
            user_id=user_id,
            timestamp=now, # Время генерации ретроспективы
            period_days=period_days,
            test_count=test_count,
            averages=averages,
            open_answers=retro_open_answers,
            interpretation=interpretation if "Ошибка:" not in interpretation else None
        )
        if retro_id is not None:
            logger.info(f"Данные ретроспективы user {user_id} сохранены в БД с retro_id={retro_id}")
        else:
            logger.error(f"Не удалось сохранить результат ретроспективы для user {user_id} в БД.")
            # Не прерываем, но пользователь не увидит старую ретроспективу в будущем
    except Exception as e:
        logger.exception(f"Ошибка сохранения ретроспективы {user_id} в БД:")
        # Не прерываем, но логируем

    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # Формируем контекст для чата
    avg_str_parts = [f"{key}: {round(val, 1) if val is not None else 'N/A'}" for key, val in averages.items()]
    context.user_data["retro_chat_context"] = f"Период: {period_days} дн. Тестов: {test_count}. Средние: {', '.join(avg_str_parts)}."

    # Отправляем результат пользователю
    message = (f"📊 **Анализ ретроспективы за {period_days} дней:**\n\n{interpretation}\n\n-------\n"
               "Вы можете задать уточняющие вопросы по этому анализу или поделиться своими мыслями.\n\n"
               "Чтобы завершить, нажмите 'Главное меню'.")
    await update.message.reply_text(message, reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    return True # Успех

async def retrospective_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    user_input = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        # Очищаем контекст ретроспективы при выходе
        context.user_data.pop("retro_period_days", None)
        context.user_data.pop("retro_answers", None)
        context.user_data.pop("retro_chat_context", None)
        return await exit_to_main(update, context)

    if "retro_chat_context" not in context.user_data:
        logger.warning(f"Отсутствует контекст для чата ретроспективы user {user_id}")
        await update.message.reply_text("Не найден контекст для этого чата. Возвращаемся в меню.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    logger.info(f"User {user_id} продолжает чат после ретроспективы: '{user_input[:50]}...'")
    chat_context = context.user_data.get("retro_chat_context", "Результаты ретроспективы учтены.")
    prompt = gemini_client.build_gemini_prompt_for_retro_chat(user_input, chat_context)

    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    answer = await gemini_client.call_gemini_api(prompt, max_tokens=500)

    await update.message.reply_text(answer, reply_markup=CANCEL_KEYBOARD)
    return State.GEMINI_CHAT_RETRO # Остаемся в состоянии чата ретроспективы
