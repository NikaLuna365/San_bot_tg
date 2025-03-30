# handlers/test.py
import logging
# import json # Больше не нужен здесь напрямую для записи
# import os # Больше не нужен здесь для путей
from datetime import datetime
from typing import Dict, Any, List, Optional # Добавили Optional

# import aiofiles # Больше не нужен
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

# --- Импорты проекта ---
from constants import (
    State, WEEKDAY_FIXED_QUESTIONS, OPEN_QUESTIONS,
    build_fixed_keyboard, CANCEL_KEYBOARD # DATA_DIR больше не нужен здесь
)
import gemini_client
import db # Добавлен импорт db
from utils import get_now_utc # Используем UTC время

from .common import exit_to_main

logger = logging.getLogger(__name__)

async def test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог прохождения теста."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} начал тест.")
    context.user_data["test_answers"] = {}
    # Убираем test_start_time_str, так как timestamp будет браться при сохранении
    context.user_data["question_index"] = 0

    # Определяем вопросы на сегодня
    now = get_now_utc() # Получаем текущее время UTC
    current_day: int = now.weekday() # 0 = Понедельник, 6 = Воскресенье
    context.user_data["current_weekday"] = current_day # Сохраняем для записи в БД

    fixed_questions: List[str] = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
    context.user_data["fixed_questions"] = fixed_questions

    await update.message.reply_text(
        f"Начнем тест! Сегодня {now.strftime('%A').lower()}.\n\n{fixed_questions[0]}",
        reply_markup=build_fixed_keyboard()
    )
    return State.TEST_FIXED_1

async def test_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответы на фиксированные вопросы теста (1-6)."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id
    q_index: int = context.user_data.get("question_index", 0)
    fixed_questions: List[str] = context.user_data.get("fixed_questions", [])

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    if user_input not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text(
            "Пожалуйста, выберите вариант от 1 до 7, или нажмите 'Главное меню'.",
            reply_markup=build_fixed_keyboard()
        )
        return State(State.TEST_FIXED_1.value + q_index)

    # Сохраняем ответ в user_data (как и раньше)
    # Сохраняем как строку, чтобы потом корректно записать в JSON
    context.user_data.setdefault("test_answers", {}).setdefault("fixed", {})[f"fixed_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered fixed Q{q_index+1}: {user_input}")

    q_index += 1
    context.user_data["question_index"] = q_index

    if q_index < len(fixed_questions):
        await update.message.reply_text(fixed_questions[q_index], reply_markup=build_fixed_keyboard())
        return State(State.TEST_FIXED_1.value + q_index)
    else:
        logger.info(f"User {user_id} finished fixed questions.")
        await update.message.reply_text(OPEN_QUESTIONS[0], reply_markup=CANCEL_KEYBOARD)
        return State.TEST_OPEN_1

async def test_open_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответ на первый открытый вопрос."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    context.user_data.setdefault("test_answers", {}).setdefault("open", {})["open_1"] = user_input
    logger.debug(f"User {user_id} answered open Q1.")
    await update.message.reply_text(OPEN_QUESTIONS[1], reply_markup=CANCEL_KEYBOARD)
    return State.TEST_OPEN_2

async def test_open_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответ на второй открытый вопрос, сохраняет результаты в БД и вызывает Gemini."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id
    pool = context.bot_data.get("db_pool")

    if not pool:
        logger.error(f"DB pool not found in context for user {user_id} in test_open_2")
        await update.message.reply_text("Произошла ошибка при доступе к базе данных. Результаты не сохранены.", reply_markup=MAIN_MENU_KEYBOARD)
        # Важно завершить диалог, если нет БД
        return ConversationHandler.END

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    context.user_data.setdefault("test_answers", {}).setdefault("open", {})["open_2"] = user_input
    logger.info(f"User {user_id} finished open questions.")

    # --- Подготовка данных для сохранения ---
    timestamp_utc = get_now_utc()
    weekday = context.user_data.get("current_weekday", timestamp_utc.weekday()) # Берем сохраненный или текущий
    all_answers = context.user_data.get("test_answers", {})
    fixed_answers = all_answers.get("fixed", {})
    open_answers = all_answers.get("open", {})
    test_id: Optional[int] = None # Инициализируем ID теста

    # --- Сохранение первичных данных в БД (без интерпретации) ---
    try:
        test_id = await db.save_test_result(
            pool=pool,
            user_id=user_id,
            timestamp=timestamp_utc,
            weekday=weekday,
            fixed_answers=fixed_answers,
            open_answers=open_answers,
            interpretation=None # Пока нет интерпретации
        )
        if test_id is None:
            logger.error(f"Не удалось сохранить первичные данные теста для user {user_id} в БД.")
            # Не прерываем диалог, но сообщаем
            await update.message.reply_text(
                "Произошла ошибка при сохранении данных теста. Пожалуйста, попробуйте позже.",
                reply_markup=CANCEL_KEYBOARD # Даем шанс попробовать позже или выйти
            )
            # Не будем продолжать с Gemini, если сохранение не удалось
            # Можно вернуть какое-то состояние ошибки или главное меню
            # Но лучше остаться здесь, чтобы пользователь мог нажать "Главное меню"
            # Если же он напишет текст, то попадет в test_open_2 снова,
            # что может привести к дублированию попыток сохранения.
            # Поэтому лучше вернуть END, но с CANCEL_KEYBOARD
            return ConversationHandler.END # Завершаем диалог

    except Exception as e:
        logger.exception(f"Непредвиденная ошибка при сохранении теста user {user_id} в БД:")
        await update.message.reply_text(
            "Произошла внутренняя ошибка при сохранении данных.",
            reply_markup=MAIN_MENU_KEYBOARD # Возвращаем в главное меню
        )
        return ConversationHandler.END

    # --- Получение интерпретации от Gemini ---
    await update.message.reply_text("Спасибо за ответы! Анализирую ваше состояние...", reply_markup=ReplyKeyboardRemove())

    fixed_questions = context.user_data.get("fixed_questions", [])
    # Важно передать словарь с ответами { 'fixed_1': '5', ... 'open_1': 'text', ... }
    all_answers_flat = {**fixed_answers, **open_answers}

    # Строим промпт на основе полных данных
    prompt = gemini_client.build_gemini_prompt_for_test(fixed_questions, all_answers_flat)
    interpretation = await gemini_client.call_gemini_api(prompt)

    # --- Обновление записи в БД с интерпретацией ---
    if interpretation and "Ошибка:" not in interpretation and test_id is not None:
         try:
             await db.update_test_interpretation(pool, test_id, interpretation)
         except Exception as e:
             # Ошибка обновления не критична для пользователя, просто логируем
             logger.exception(f"Ошибка при обновлении интерпретации теста {test_id} для user {user_id}:")
    elif test_id is None:
         logger.error(f"Не удалось обновить интерпретацию: test_id не был получен при сохранении для user {user_id}")


    # --- Формирование контекста для чата ---
    chat_context = "Результаты теста учтены." # Запасной вариант
    try:
        # Пытаемся рассчитать средние (для контекста чата) из сохраненных ответов
        s_1_sum = int(fixed_answers.get("fixed_1", 0)) + int(fixed_answers.get("fixed_2", 0))
        s_2_sum = int(fixed_answers.get("fixed_3", 0)) + int(fixed_answers.get("fixed_4", 0))
        s_3_sum = int(fixed_answers.get("fixed_5", 0)) + int(fixed_answers.get("fixed_6", 0))
        # Проверка деления на ноль (если вдруг ответов было меньше 2 на пару)
        s_1_count = (1 if "fixed_1" in fixed_answers else 0) + (1 if "fixed_2" in fixed_answers else 0)
        s_2_count = (1 if "fixed_3" in fixed_answers else 0) + (1 if "fixed_4" in fixed_answers else 0)
        s_3_count = (1 if "fixed_5" in fixed_answers else 0) + (1 if "fixed_6" in fixed_answers else 0)

        s_1_avg = f"{s_1_sum / s_1_count:.1f}/7" if s_1_count > 0 else "N/A"
        s_2_avg = f"{s_2_sum / s_2_count:.1f}/7" if s_2_count > 0 else "N/A"
        s_3_avg = f"{s_3_sum / s_3_count:.1f}/7" if s_3_count > 0 else "N/A"

        chat_context = f"Самочувствие: {s_1_avg}, Активность: {s_2_avg}, Настроение: {s_3_avg}."
    except (ValueError, TypeError, ZeroDivisionError) as e:
        logger.warning(f"Не удалось рассчитать средние баллы для контекста чата user {user_id}: {e}")
    context.user_data["test_chat_context"] = chat_context

    # --- Ответ пользователю ---
    message = (
        f"📊 **Анализ вашего состояния:**\n\n{interpretation}\n\n"
        "-------\n"
        "Теперь вы можете задать уточняющие вопросы по результатам или просто поделиться мыслями. "
        "Я постараюсь ответить в контексте пройденного теста.\n\n"
        "Чтобы завершить, нажмите 'Главное меню'."
    )
    await update.message.reply_text(
        message,
        reply_markup=CANCEL_KEYBOARD,
        parse_mode='Markdown'
    )
    return State.GEMINI_CHAT_TEST


async def gemini_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает сообщения пользователя в чате после теста."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        # Очищаем контекст чата при выходе
        context.user_data.pop("test_chat_context", None)
        context.user_data.pop("test_answers", None)
        context.user_data.pop("question_index", None)
        context.user_data.pop("fixed_questions", None)
        context.user_data.pop("current_weekday", None)
        return await exit_to_main(update, context)

    logger.info(f"User {user_id} продолжает чат после теста: '{user_input[:50]}...'")
    chat_context = context.user_data.get("test_chat_context", "Результаты теста учтены.")
    prompt = gemini_client.build_followup_chat_prompt(user_input, chat_context)

    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    answer = await gemini_client.call_gemini_api(prompt, max_tokens=400)

    await update.message.reply_text(
        answer,
        reply_markup=CANCEL_KEYBOARD
    )
    # Остаемся в том же состоянии чата
    return State.GEMINI_CHAT_TEST
