# handlers/retrospective.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import asyncpg # Импортируем для Record
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

# --- Импорты проекта ---
from constants import (
    State, RETRO_OPEN_QUESTIONS,
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
    # Очищаем предыдущие данные ретроспективы
    context.user_data.pop("retro_period_days", None)
    context.user_data.pop("retro_answers", None)
    context.user_data.pop("retro_chat_context", None)
    context.user_data.pop("retro_chat_history", None)

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
    elif choice == "Главное меню":
        # Очищаем user_data при выходе
        context.user_data.pop("retro_period_days", None)
        context.user_data.pop("retro_answers", None)
        context.user_data.pop("retro_chat_context", None)
        context.user_data.pop("retro_chat_history", None)
        return await exit_to_main(update, context)
    else:
        await update.message.reply_text("Пожалуйста, выберите 'За 1 неделю' или 'За 2 недели'.", reply_markup=RETRO_NOW_PERIOD_KEYBOARD)
        return State.RETRO_PERIOD_CHOICE

    logger.info(f"Пользователь {user_id} выбрал ретроспективу за {period_days} дней.")
    context.user_data["retro_period_days"] = period_days
    context.user_data["retro_answers"] = {}

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
        context.user_data.pop("retro_chat_history", None)
        return await exit_to_main(update, context)

    context.user_data.setdefault("retro_answers", {})[f"retro_open_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered retro open Q{q_index+1}.")

    if q_index < len(RETRO_OPEN_QUESTIONS) - 1:
        await update.message.reply_text(RETRO_OPEN_QUESTIONS[q_index+1], reply_markup=CANCEL_KEYBOARD)
        return next_state
    else:
        logger.info(f"User {user_id} finished retro open questions.")
        await update.message.reply_text("Спасибо! Собираю данные и готовлю анализ...", reply_markup=ReplyKeyboardRemove())
        success = await run_retrospective_analysis(update, context)
        if success:
            return State.GEMINI_CHAT_RETRO # Переходим в чат
        else:
            # Ошибка или мало данных, сообщение уже отправлено
            context.user_data.pop("retro_period_days", None)
            context.user_data.pop("retro_answers", None)
            context.user_data.pop("retro_chat_context", None) # На всякий случай
            context.user_data.pop("retro_chat_history", None)
            return ConversationHandler.END # Завершаем диалог

# Генерация состояний и хендлеров для открытых вопросов ретро (без изменений)
retro_open_states = {}
for i in range(len(RETRO_OPEN_QUESTIONS)):
    current_state_enum = State(State.RETRO_OPEN_1.value + i)
    next_state_enum = State(State.RETRO_OPEN_1.value + i + 1) if i < len(RETRO_OPEN_QUESTIONS) - 1 else State.GEMINI_CHAT_RETRO
    def create_handler_wrapper(current_s, next_s):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
            return await retro_open_handler(update, context, current_s, next_s)
        return handler
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
        return False

    now = get_now_utc()
    start_date = now - timedelta(days=period_days)
    logger.info(f"Запуск анализа ретроспективы для {user_id} за {period_days} дней (с {start_date.date()})")

    tests_in_period_records: List[asyncpg.Record] = []
    try:
        tests_in_period_records = await db.get_test_results_for_period(pool, user_id, start_date, end_date=now)
    except Exception as e:
        logger.exception(f"Ошибка при получении данных тестов из БД для user {user_id}:")
        await update.message.reply_text("Произошла ошибка при получении данных для анализа.", reply_markup=MAIN_MENU_KEYBOARD)
        return False

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
        return False

    # --- Расчет средних ---
    sums: Dict[str, float] = {f"fixed_{i}": 0.0 for i in range(1, 7)}
    counts: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}

    for record in tests_in_period_records:
        fixed_answers = record.get("fixed_answers")
        if not isinstance(fixed_answers, dict):
            logger.warning(f"Некорректный формат fixed_answers в записи БД для user {user_id}: {fixed_answers}")
            continue
        for i in range(1, 7):
            key = f"fixed_{i}"
            try:
                val_str = fixed_answers.get(key)
                if isinstance(val_str, str): sums[key] += int(val_str); counts[key] += 1
                elif isinstance(val_str, (int, float)): sums[key] += float(val_str); counts[key] += 1
            except (ValueError, TypeError, KeyError) as e:
                logger.warning(f"Ошибка при обработке ответа {key}={val_str} из БД для user {user_id}: {e}")
                continue

    averages: Dict[str, Optional[float]] = {}
    try:
        # Самочувствие
        if counts["fixed_1"] > 0 and counts["fixed_2"] > 0: averages["Самочувствие"] = (sums["fixed_1"] / counts["fixed_1"] + sums["fixed_2"] / counts["fixed_2"]) / 2
        elif counts["fixed_1"] > 0: averages["Самочувствие"] = sums["fixed_1"] / counts["fixed_1"]
        elif counts["fixed_2"] > 0: averages["Самочувствие"] = sums["fixed_2"] / counts["fixed_2"]
        else: averages["Самочувствие"] = None
        # Активность
        if counts["fixed_3"] > 0 and counts["fixed_4"] > 0: averages["Активность"] = (sums["fixed_3"] / counts["fixed_3"] + sums["fixed_4"] / counts["fixed_4"]) / 2
        elif counts["fixed_3"] > 0: averages["Активность"] = sums["fixed_3"] / counts["fixed_3"]
        elif counts["fixed_4"] > 0: averages["Активность"] = sums["fixed_4"] / counts["fixed_4"]
        else: averages["Активность"] = None
        # Настроение
        if counts["fixed_5"] > 0 and counts["fixed_6"] > 0: averages["Настроение"] = (sums["fixed_5"] / counts["fixed_5"] + sums["fixed_6"] / counts["fixed_6"]) / 2
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
    initial_ai_response = interpretation # Сохраняем первый ответ AI

    # --- Сохранение результатов ретроспективы в БД ---
    retro_id: Optional[int] = None
    try:
        retro_id = await db.save_retrospective_result(
            pool=pool, user_id=user_id, timestamp=now, period_days=period_days,
            test_count=test_count, averages=averages, open_answers=retro_open_answers,
            interpretation=interpretation if "Ошибка:" not in interpretation and "Извините," not in interpretation else None
        )
        if retro_id is not None: logger.info(f"Данные ретроспективы user {user_id} сохранены в БД с retro_id={retro_id}")
        else: logger.error(f"Не удалось сохранить результат ретроспективы для user {user_id} в БД.")
    except Exception as e:
        logger.exception(f"Ошибка сохранения ретроспективы {user_id} в БД:")

    # --- Формируем контекст для чата ---
    avg_str_parts = [f"{key}: {round(val, 1) if val is not None else 'N/A'}" for key, val in averages.items()]
    context.user_data["retro_chat_context"] = f"Период: {period_days} дн. Тестов: {test_count}. Средние: {', '.join(avg_str_parts)}."

    # --- Инициализация истории чата первым ответом AI ---
    if initial_ai_response and "Ошибка:" not in initial_ai_response and "Извините," not in initial_ai_response:
        context.user_data["retro_chat_history"] = [{"role": "model", "content": initial_ai_response}]
    else:
        context.user_data["retro_chat_history"] = [] # Начинаем с пустой истории, если AI не ответил

    # Отправляем результат пользователю
    message = (f"📊 **Анализ ретроспективы за {period_days} дней:**\n\n{interpretation}\n\n-------\n"
               "Вы можете задать уточняющие вопросы по этому анализу или поделиться своими мыслями.\n\n"
               "Чтобы завершить, нажмите 'Главное меню'.")
    await update.message.reply_text(message, reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    return True # Успех

async def retrospective_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Обрабатывает сообщения пользователя в чате после ретроспективы, сохраняет историю и обрабатывает запрос на резюме."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    # Получаем или инициализируем историю чата для ретроспективы
    chat_history: List[Dict[str, str]] = context.user_data.setdefault("retro_chat_history", [])

    if user_input == "Главное меню":
        # Очищаем контекст чата и историю при выходе
        context.user_data.pop("retro_period_days", None)
        context.user_data.pop("retro_answers", None)
        context.user_data.pop("retro_chat_context", None)
        context.user_data.pop("retro_chat_history", None) # Очищаем историю
        return await exit_to_main(update, context)

    # Проверяем наличие базового контекста ретроспективы
    base_chat_context = context.user_data.get("retro_chat_context")
    if not base_chat_context:
        logger.warning(f"Отсутствует базовый контекст для чата ретроспективы user {user_id}")
        await update.message.reply_text("Не найден контекст для этого чата. Возвращаемся в меню.", reply_markup=MAIN_MENU_KEYBOARD)
        # Очищаем историю на всякий случай
        context.user_data.pop("retro_chat_history", None)
        return ConversationHandler.END

    logger.info(f"User {user_id} продолжает чат после ретроспективы: '{user_input[:50]}...'")

    # Добавляем сообщение пользователя в историю
    chat_history.append({"role": "user", "content": user_input})

    # --- Проверка на запрос резюме ---
    is_summary_request = False
    summary_keywords = ["итог", "резюме", "подведи", "обсуждали", "вывод"]
    if any(keyword in user_input.lower() for keyword in summary_keywords) and len(user_input.split()) < 6:
        is_summary_request = True
        logger.info(f"User {user_id} запросил резюме чата ретроспективы.")

    # --- Выбор и построение промпта ---
    prompt = ""
    answer = "" # Инициализируем
    if is_summary_request:
        if len(chat_history) < 2: # Только ответ AI и запрос
            answer = "Мы еще почти ничего не обсудили, чтобы подводить итог."
            prompt = None
        else:
            prompt = gemini_client.build_summary_prompt(chat_history)
    else:
        # Обычный ответ в чате
        prompt = gemini_client.build_gemini_prompt_for_retro_chat(
            week_overview=base_chat_context, # Используем базовый контекст
            chat_history=chat_history # Передаем историю
        )

    # --- Вызов API (если есть промпт) ---
    if prompt:
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
        # Для ретроспективы можно дать чуть больше токенов
        api_answer = await gemini_client.call_gemini_api(prompt, max_tokens=500)
        answer = api_answer

        # Добавляем ответ AI в историю (кроме резюме)
        if not is_summary_request and api_answer and "Ошибка:" not in api_answer and "Извините," not in api_answer:
            chat_history.append({"role": "model", "content": api_answer})
            # Ограничиваем размер истории
            context.user_data["retro_chat_history"] = chat_history[-20:]
        elif is_summary_request:
            # Удаляем запрос пользователя на резюме из истории
             chat_history.pop()
             context.user_data["retro_chat_history"] = chat_history
    elif not answer: # Если промпт не создавался
        pass # answer уже содержит сообщение "Мы еще почти ничего не обсудили..."

    # --- Отправка ответа ---
    if answer:
        await update.message.reply_text(answer, reply_markup=CANCEL_KEYBOARD)
    else:
        logger.warning(f"Пустой ответ AI для user {user_id} в чате ретроспективы (после всех проверок).")
        # await update.message.reply_text("Не могу сейчас ответить. Попробуйте позже.", reply_markup=CANCEL_KEYBOARD)

    return State.GEMINI_CHAT_RETRO # Остаемся в состоянии чата ретроспективы
