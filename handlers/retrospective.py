# handlers/retrospective.py
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import aiofiles
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

# --- ИЗМЕНЕНО: Абсолютный импорт ---
from constants import (
    State, RETRO_OPEN_QUESTIONS, DATA_DIR,
    RETRO_CHOICE_KEYBOARD, RETRO_NOW_PERIOD_KEYBOARD, CANCEL_KEYBOARD,
    MAIN_MENU_KEYBOARD # Добавлено для сообщений об ошибках
)
import gemini_client
from utils import get_now_utc

# --- НЕ ИЗМЕНЕНО: Относительный импорт из той же папки ---
from .common import exit_to_main

logger = logging.getLogger(__name__)

# --- Начало диалога ретроспективы ---

async def retrospective_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Предлагает выбор: провести ретроспективу сейчас или запланировать."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} вошел в меню ретроспективы.")
    await update.message.reply_text(
        "Ретроспектива помогает проанализировать динамику вашего состояния. Выберите опцию:",
        reply_markup=RETRO_CHOICE_KEYBOARD
    )
    return State.RETRO_CHOICE

async def retrospective_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    """Обрабатывает выбор пользователя: сейчас или запланировать."""
    choice = update.message.text.strip()
    user_id = update.effective_user.id

    if choice == "Ретроспектива сейчас":
        logger.info(f"Пользователь {user_id} выбрал ретроспективу сейчас.")
        await update.message.reply_text(
            "За какой период провести ретроспективу?",
            reply_markup=RETRO_NOW_PERIOD_KEYBOARD
        )
        return State.RETRO_PERIOD_CHOICE
    elif choice == "Запланировать":
        logger.info(f"Пользователь {user_id} выбрал запланировать ретроспективу.")
        # Сообщение о том, как это сделать
        await update.message.reply_text(
            "Для планирования ретроспективы используйте соответствующую кнопку или команду в главном меню.",
             reply_markup=MAIN_MENU_KEYBOARD # Возвращаем в главное меню
             )
        return ConversationHandler.END # Завершаем этот диалог
    elif choice == "Главное меню":
        return await exit_to_main(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите один из предложенных вариантов.",
            reply_markup=RETRO_CHOICE_KEYBOARD
        )
        return State.RETRO_CHOICE

# --- Логика мгновенной ретроспективы ---

async def retrospective_period_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает выбор периода для мгновенной ретроспективы."""
    choice = update.message.text.strip()
    user_id = update.effective_user.id
    period_days = 0

    if choice == "За 1 неделю":
        period_days = 7
    elif choice == "За 2 недели":
        period_days = 14
    elif choice == "Главное меню":
        return await exit_to_main(update, context)
    else:
        await update.message.reply_text(
            "Пожалуйста, выберите 'За 1 неделю' или 'За 2 недели'.",
            reply_markup=RETRO_NOW_PERIOD_KEYBOARD
        )
        return State.RETRO_PERIOD_CHOICE

    logger.info(f"Пользователь {user_id} выбрал ретроспективу за {period_days} дней.")
    context.user_data["retro_period_days"] = period_days
    context.user_data["retro_answers"] = {} # Сбрасываем ответы для новой ретроспективы

    # Сразу переходим к качественным вопросам
    await update.message.reply_text(
        f"Отлично. Теперь ответьте на несколько вопросов о прошедших {period_days} днях.\n\n"
        f"{RETRO_OPEN_QUESTIONS[0]}",
        reply_markup=CANCEL_KEYBOARD
    )
    return State.RETRO_OPEN_1

async def retro_open_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, current_state: State, next_state: State) -> State | int:
    """Общий обработчик для открытых вопросов ретроспективы."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id
    q_index = current_state.value - State.RETRO_OPEN_1.value # Определяем номер вопроса (0-3)

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    context.user_data["retro_answers"][f"retro_open_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered retro open Q{q_index+1}.")

    if q_index < len(RETRO_OPEN_QUESTIONS) - 1:
        # Переходим к следующему вопросу
        await update.message.reply_text(RETRO_OPEN_QUESTIONS[q_index+1], reply_markup=CANCEL_KEYBOARD)
        return next_state
    else:
        # Вопросы закончились, запускаем анализ
        logger.info(f"User {user_id} finished retro open questions.")
        await update.message.reply_text(
            "Спасибо! Собираю данные и готовлю анализ...",
            reply_markup=ReplyKeyboardRemove()
        )
        # Запускаем анализ и проверяем результат (была ли ошибка, например, мало данных)
        success = await run_retrospective_analysis(update, context)
        if success:
            return State.GEMINI_CHAT_RETRO # Переходим в чат по результатам
        else:
            # Если анализ не удался (например, мало данных), сообщение уже отправлено,
            # просто завершаем диалог
            return ConversationHandler.END


# Генерируем состояния и переходы для открытых вопросов
retro_open_states = {}
for i in range(len(RETRO_OPEN_QUESTIONS)):
    current_state_enum = State(State.RETRO_OPEN_1.value + i)
    # Определяем следующее состояние: либо следующий вопрос, либо чат
    next_state_enum = State(State.RETRO_OPEN_1.value + i + 1) if i < len(RETRO_OPEN_QUESTIONS) - 1 else State.GEMINI_CHAT_RETRO

    # Создаем обертки для передачи состояний в обработчик
    async def create_handler_wrapper(current_s, next_s):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
            # В последнем шаге next_state будет GEMINI_CHAT_RETRO
            return await retro_open_handler(update, context, current_s, next_s)
        return handler

    retro_open_states[current_state_enum] = [
        MessageHandler(filters.TEXT & ~filters.COMMAND, create_handler_wrapper(current_state_enum, next_state_enum))
    ]


async def run_retrospective_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Собирает данные тестов, рассчитывает средние, вызывает Gemini и отправляет результат.
       Возвращает True при успехе, False при ошибке (например, мало данных)."""
    user_id = update.effective_user.id
    period_days = context.user_data.get("retro_period_days", 7)
    now = get_now_utc()
    start_date = now - timedelta(days=period_days)
    logger.info(f"Запуск анализа ретроспективы для {user_id} за {period_days} дней (с {start_date.date()})")

    # --- Сбор данных тестов ---
    # TODO (Data Storage): Заменить чтение файлов на запрос к БД.
    user_files: List[str] = []
    try:
        all_files = os.listdir(DATA_DIR)
        user_files = [f for f in all_files if f.startswith(f"test_{user_id}_") and f.endswith(".json")]
    except FileNotFoundError:
        logger.warning(f"Директория {DATA_DIR} не найдена.")
    except Exception as e:
        logger.exception(f"Ошибка листинга файлов в {DATA_DIR}")

    tests_in_period_answers: List[Dict[str, Any]] = [] # Собираем только словари с ответами
    for filename in user_files:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
                # Проверяем timestamp
                ts_str = data.get("timestamp")
                if ts_str:
                    # Пытаемся обработать время с TZ и без
                    try:
                        if '+' in ts_str or 'Z' in ts_str:
                            ts_aware = datetime.fromisoformat(ts_str)
                        else: # Если сохранено без TZ, считаем UTC
                            ts_aware = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)

                        # Сравниваем aware datetime
                        if start_date <= ts_aware <= now:
                            test_answers = data.get("test_answers")
                            if isinstance(test_answers, dict):
                                tests_in_period_answers.append(test_answers)
                    except ValueError:
                         logger.warning(f"Неверный формат timestamp '{ts_str}' в файле {filename}")
                         continue # Пропускаем файл с неверным форматом

        except Exception as e:
            logger.exception(f"Ошибка чтения или парсинга файла {filename}:")

    test_count = len(tests_in_period_answers)
    logger.info(f"Найдено {test_count} тестов для пользователя {user_id} за период.")

    # Проверка на минимальное количество тестов
    MIN_TESTS_FOR_RETRO = 3
    if test_count < MIN_TESTS_FOR_RETRO:
        await update.message.reply_text(
            f"К сожалению, найдено слишком мало ({test_count}) данных за последние {period_days} дней. "
            f"Для анализа ретроспективы нужно хотя бы {MIN_TESTS_FOR_RETRO} пройденных теста за этот период.",
            reply_markup=MAIN_MENU_KEYBOARD # Используем константу
        )
        logger.warning(f"Недостаточно данных для ретроспективы user {user_id} ({test_count}<{MIN_TESTS_FOR_RETRO})")
        return False # Анализ не выполнен

    # --- Расчет средних ---
    sums: Dict[str, float] = {f"fixed_{i}": 0.0 for i in range(1, 7)}
    counts: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}

    for answers in tests_in_period_answers:
        for i in range(1, 7):
            key = f"fixed_{i}"
            try:
                # Проверяем, что значение - строка и конвертируем в int
                val_str = answers.get(key)
                if isinstance(val_str, str):
                    val = int(val_str)
                    sums[key] += val
                    counts[key] += 1
                elif isinstance(val_str, (int, float)): # На случай, если где-то уже сохранено числом
                    sums[key] += float(val_str)
                    counts[key] += 1
            except (ValueError, TypeError, KeyError):
                # Игнорируем ошибки конвертации или отсутствующие ключи
                continue

    averages: Dict[str, Optional[float]] = {}
    try:
        # Рассчитываем среднее только если есть данные для обеих компонент шкалы
        if counts["fixed_1"] > 0 and counts["fixed_2"] > 0:
            avg1 = sums["fixed_1"] / counts["fixed_1"]
            avg2 = sums["fixed_2"] / counts["fixed_2"]
            averages["Самочувствие"] = (avg1 + avg2) / 2
        else: averages["Самочувствие"] = None

        if counts["fixed_3"] > 0 and counts["fixed_4"] > 0:
            avg3 = sums["fixed_3"] / counts["fixed_3"]
            avg4 = sums["fixed_4"] / counts["fixed_4"]
            averages["Активность"] = (avg3 + avg4) / 2
        else: averages["Активность"] = None

        if counts["fixed_5"] > 0 and counts["fixed_6"] > 0:
            avg5 = sums["fixed_5"] / counts["fixed_5"]
            avg6 = sums["fixed_6"] / counts["fixed_6"]
            averages["Настроение"] = (avg5 + avg6) / 2
        else: averages["Настроение"] = None
    except ZeroDivisionError:
         logger.error(f"Деление на ноль при расчете средних для user {user_id}")
         # Обнуляем все, если была ошибка деления
         averages = {k: None for k in ["Самочувствие", "Активность", "Настроение"]}

    # --- Вызов Gemini ---
    open_answers = context.user_data.get("retro_answers", {})
    prompt = gemini_client.build_gemini_prompt_for_retro(averages, test_count, open_answers, period_days)
    interpretation = await gemini_client.call_gemini_api(prompt, max_tokens=800) # Больше токенов для ретро

    # --- Сохранение результатов ретроспективы ---
    # TODO (Data Storage): Заменить на сохранение в БД.
    retro_start_time: str = datetime.now().strftime("%Y%m%d_%H%M%S")
    retro_filename: str = os.path.join(DATA_DIR, f"retro_{user_id}_{retro_start_time}.json")
    retro_data = {
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(),
        "period_days": period_days,
        "test_count": test_count,
        "averages": averages,
        "open_answers": open_answers,
        "interpretation": interpretation
    }
    try:
        async with aiofiles.open(retro_filename, "w", encoding="utf-8") as f:
            # Используем default=str для сериализации None и других типов, если нужно
            await f.write(json.dumps(retro_data, ensure_ascii=False, indent=4, default=str))
        logger.info(f"Данные ретроспективы {user_id} сохранены в {retro_filename}")
    except Exception as e:
        logger.exception(f"Ошибка сохранения ретроспективы {user_id} в файл:")

    # --- Формирование контекста для чата ---
    avg_str_parts = []
    for key, val in averages.items():
        avg_str_parts.append(f"{key}: {round(val, 1) if val is not None else 'N/A'}")
    context.user_data["retro_chat_context"] = f"Период: {period_days} дн. Тестов: {test_count}. Средние: {', '.join(avg_str_parts)}."

    # --- Ответ пользователю ---
    message = (
        f"📊 **Анализ ретроспективы за {period_days} дней:**\n\n{interpretation}\n\n"
        "-------\n"
        "Вы можете задать уточняющие вопросы по этому анализу или поделиться своими мыслями.\n\n"
        "Чтобы завершить, нажмите 'Главное меню'."
    )
    await update.message.reply_text(
        message,
        reply_markup=CANCEL_KEYBOARD,
        parse_mode='Markdown'
    )
    return True # Анализ успешно завершен


async def retrospective_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает сообщения пользователя в чате после ретроспективы."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    # Если контекста нет (например, из-за ошибки на предыдущем шаге), не продолжаем чат
    if "retro_chat_context" not in context.user_data:
        logger.warning(f"Отсутствует контекст для чата ретроспективы user {user_id}")
        await update.message.reply_text("Не найден контекст для этого чата. Возвращаемся в меню.", reply_markup=MAIN_MENU_KEYBOARD)
        return ConversationHandler.END

    logger.info(f"User {user_id} продолжает чат после ретроспективы: '{user_input[:50]}...'")
    chat_context = context.user_data.get("retro_chat_context", "Результаты ретроспективы учтены.")
    prompt = gemini_client.build_gemini_prompt_for_retro_chat(user_input, chat_context)

    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    answer = await gemini_client.call_gemini_api(prompt, max_tokens=500)

    await update.message.reply_text(
        answer,
        reply_markup=CANCEL_KEYBOARD
    )
    return State.GEMINI_CHAT_RETRO
