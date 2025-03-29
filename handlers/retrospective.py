# handlers/retrospective.py
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import aiofiles
# !!! ИЗМЕНЕНО: Импортируем ReplyKeyboardRemove !!!
from telegram import Update, ReplyKeyboardRemove, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters

# Импорты из проекта
from constants import (
    State, RETRO_OPEN_QUESTIONS, DATA_DIR,
    RETRO_NOW_PERIOD_KEYBOARD, CANCEL_KEYBOARD, MAIN_MENU_KEYBOARD
)
import gemini_client
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
        reply_markup=RETRO_NOW_PERIOD_KEYBOARD # Сразу показываем выбор периода
    )
    # Возвращаем состояние выбора периода как начальное для этого диалога
    return State.RETRO_PERIOD_CHOICE

# --- Логика мгновенной ретроспективы ---

# Функция retrospective_period_choice остается без изменений
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
    context.user_data["retro_answers"] = {}

    await update.message.reply_text(f"Отлично. Теперь ответьте на несколько вопросов о прошедших {period_days} днях.\n\n{RETRO_OPEN_QUESTIONS[0]}", reply_markup=CANCEL_KEYBOARD)
    return State.RETRO_OPEN_1

# Функция retro_open_handler остается без изменений
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

    context.user_data["retro_answers"][f"retro_open_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered retro open Q{q_index+1}.")

    if q_index < len(RETRO_OPEN_QUESTIONS) - 1:
        await update.message.reply_text(RETRO_OPEN_QUESTIONS[q_index+1], reply_markup=CANCEL_KEYBOARD)
        return next_state
    else:
        logger.info(f"User {user_id} finished retro open questions.")
        await update.message.reply_text("Спасибо! Собираю данные и готовлю анализ...", reply_markup=ReplyKeyboardRemove())
        success = await run_retrospective_analysis(update, context)
        if success: return State.GEMINI_CHAT_RETRO
        else:
            context.user_data.pop("retro_period_days", None)
            context.user_data.pop("retro_answers", None)
            return ConversationHandler.END

# Генератор состояний retro_open_states остается без изменений
retro_open_states = {}
for i in range(len(RETRO_OPEN_QUESTIONS)):
    current_state_enum = State(State.RETRO_OPEN_1.value + i)
    next_state_enum = State(State.RETRO_OPEN_1.value + i + 1) if i < len(RETRO_OPEN_QUESTIONS) - 1 else State.GEMINI_CHAT_RETRO
    async def create_handler_wrapper(current_s, next_s):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int: return await retro_open_handler(update, context, current_s, next_s)
        return handler
    retro_open_states[current_state_enum] = [ MessageHandler(filters.TEXT & ~filters.COMMAND, create_handler_wrapper(current_state_enum, next_state_enum))]

# Функция run_retrospective_analysis остается без изменений
async def run_retrospective_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    period_days = context.user_data.get("retro_period_days", 7)
    now = get_now_utc(); start_date = now - timedelta(days=period_days)
    logger.info(f"Запуск анализа ретроспективы для {user_id} за {period_days} дней (с {start_date.date()})")
    user_files: List[str] = []; tests_in_period_answers: List[Dict[str, Any]] = []
    try:
        all_files = os.listdir(DATA_DIR); user_files = [f for f in all_files if f.startswith(f"test_{user_id}_") and f.endswith(".json")]
    except FileNotFoundError: logger.warning(f"Директория {DATA_DIR} не найдена.")
    except Exception as e: logger.exception(f"Ошибка листинга файлов в {DATA_DIR}")
    for filename in user_files:
        file_path = os.path.join(DATA_DIR, filename)
        try:
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f: content = await f.read(); data = json.loads(content)
            ts_str = data.get("timestamp")
            if ts_str:
                try:
                    if '+' in ts_str or 'Z' in ts_str: ts_aware = datetime.fromisoformat(ts_str)
                    else: ts_aware = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
                    if start_date <= ts_aware <= now:
                        test_answers = data.get("test_answers");
                        if isinstance(test_answers, dict): tests_in_period_answers.append(test_answers)
                except ValueError: logger.warning(f"Неверный формат timestamp '{ts_str}' в файле {filename}"); continue
        except Exception as e: logger.exception(f"Ошибка чтения или парсинга файла {filename}:")
    test_count = len(tests_in_period_answers); logger.info(f"Найдено {test_count} тестов для {user_id} за период.")
    MIN_TESTS_FOR_RETRO = 3
    if test_count < MIN_TESTS_FOR_RETRO:
        await update.message.reply_text(f"К сожалению, найдено слишком мало ({test_count}) данных за последние {period_days} дней. Для анализа ретроспективы нужно хотя бы {MIN_TESTS_FOR_RETRO} пройденных теста за этот период.", reply_markup=MAIN_MENU_KEYBOARD);
        logger.warning(f"Недостаточно данных для ретроспективы user {user_id} ({test_count}<{MIN_TESTS_FOR_RETRO})"); return False
    sums: Dict[str, float] = {f"fixed_{i}": 0.0 for i in range(1, 7)}; counts: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}
    for answers in tests_in_period_answers:
        for i in range(1, 7):
            key = f"fixed_{i}"
            try:
                val_str = answers.get(key)
                if isinstance(val_str, str): sums[key] += int(val_str); counts[key] += 1
                elif isinstance(val_str, (int, float)): sums[key] += float(val_str); counts[key] += 1
            except (ValueError, TypeError, KeyError): continue
    averages: Dict[str, Optional[float]] = {}
    try:
        if counts["fixed_1"] > 0 and counts["fixed_2"] > 0: averages["Самочувствие"] = (sums["fixed_1"] / counts["fixed_1"] + sums["fixed_2"] / counts["fixed_2"]) / 2; else: averages["Самочувствие"] = None
        if counts["fixed_3"] > 0 and counts["fixed_4"] > 0: averages["Активность"] = (sums["fixed_3"] / counts["fixed_3"] + sums["fixed_4"] / counts["fixed_4"]) / 2; else: averages["Активность"] = None
        if counts["fixed_5"] > 0 and counts["fixed_6"] > 0: averages["Настроение"] = (sums["fixed_5"] / counts["fixed_5"] + sums["fixed_6"] / counts["fixed_6"]) / 2; else: averages["Настроение"] = None
    except ZeroDivisionError: logger.error(f"Деление на ноль при расчете средних для user {user_id}"); averages = {k: None for k in ["Самочувствие", "Активность", "Настроение"]}
    open_answers = context.user_data.get("retro_answers", {}); prompt = gemini_client.build_gemini_prompt_for_retro(averages, test_count, open_answers, period_days); interpretation = await gemini_client.call_gemini_api(prompt, max_tokens=800)
    retro_start_time: str = datetime.now().strftime("%Y%m%d_%H%M%S"); retro_filename: str = os.path.join(DATA_DIR, f"retro_{user_id}_{retro_start_time}.json")
    retro_data = {"user_id": user_id,"timestamp": datetime.now().isoformat(),"period_days": period_days,"test_count": test_count,"averages": averages,"open_answers": open_answers,"interpretation": interpretation}
    try:
        async with aiofiles.open(retro_filename, "w", encoding="utf-8") as f: await f.write(json.dumps(retro_data, ensure_ascii=False, indent=4, default=str)); logger.info(f"Данные ретроспективы {user_id} сохранены в {retro_filename}")
    except Exception as e: logger.exception(f"Ошибка сохранения ретроспективы {user_id} в файл:")
    avg_str_parts = [f"{key}: {round(val, 1) if val is not None else 'N/A'}" for key, val in averages.items()]; context.user_data["retro_chat_context"] = f"Период: {period_days} дн. Тестов: {test_count}. Средние: {', '.join(avg_str_parts)}."
    message = (f"📊 **Анализ ретроспективы за {period_days} дней:**\n\n{interpretation}\n\n-------\nВы можете задать уточняющие вопросы по этому анализу или поделиться своими мыслями.\n\nЧтобы завершить, нажмите 'Главное меню'.")
    await update.message.reply_text(message, reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown'); return True

# Функция retrospective_chat_handler остается без изменений
async def retrospective_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State | int:
    user_input = update.message.text.strip(); user_id = update.effective_user.id
    if user_input == "Главное меню":
        context.user_data.pop("retro_period_days", None); context.user_data.pop("retro_answers", None); context.user_data.pop("retro_chat_context", None); return await exit_to_main(update, context)
    if "retro_chat_context" not in context.user_data: logger.warning(f"Отсутствует контекст для чата ретроспективы user {user_id}"); await update.message.reply_text("Не найден контекст для этого чата. Возвращаемся в меню.", reply_markup=MAIN_MENU_KEYBOARD); return ConversationHandler.END
    logger.info(f"User {user_id} продолжает чат после ретроспективы: '{user_input[:50]}...'"); chat_context = context.user_data.get("retro_chat_context", "Результаты ретроспективы учтены."); prompt = gemini_client.build_gemini_prompt_for_retro_chat(user_input, chat_context)
    await context.bot.send_chat_action(chat_id=user_id, action="typing"); answer = await gemini_client.call_gemini_api(prompt, max_tokens=500)
    await update.message.reply_text(answer, reply_markup=CANCEL_KEYBOARD); return State.GEMINI_CHAT_RETRO
