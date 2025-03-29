# handlers/test.py
import logging
import json
import os
from datetime import datetime
from typing import Dict, Any, List

import aiofiles
from telegram import Update, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler

# Импорты из проекта
from ..constants import (
    State, WEEKDAY_FIXED_QUESTIONS, OPEN_QUESTIONS,
    build_fixed_keyboard, CANCEL_KEYBOARD, DATA_DIR
)
from .. import gemini_client # Импортируем модуль Gemini
from .common import exit_to_main # Импортируем общий обработчик выхода

logger = logging.getLogger(__name__)

async def test_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Начинает диалог прохождения теста."""
    user_id = update.effective_user.id
    logger.info(f"Пользователь {user_id} начал тест.")
    context.user_data["test_answers"] = {}
    # Генерируем уникальное имя файла на основе времени начала
    context.user_data["test_start_time_str"] = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.user_data["question_index"] = 0

    # Определяем вопросы на сегодня
    current_day: int = datetime.now().weekday() # 0 = Понедельник, 6 = Воскресенье
    # Используем get с запасным вариантом (вопросы Понедельника)
    fixed_questions: List[str] = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
    context.user_data["fixed_questions"] = fixed_questions

    await update.message.reply_text(
        f"Начнем тест! Сегодня {datetime.now().strftime('%A').lower()}.\n\n{fixed_questions[0]}",
        reply_markup=build_fixed_keyboard()
    )
    return State.TEST_FIXED_1

async def test_fixed_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответы на фиксированные вопросы теста (1-6)."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id
    q_index: int = context.user_data.get("question_index", 0)

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    # Валидация ввода
    if user_input not in [str(i) for i in range(1, 8)]:
        await update.message.reply_text(
            "Пожалуйста, выберите вариант от 1 до 7, или нажмите 'Главное меню'.",
            reply_markup=build_fixed_keyboard() # Показываем ту же клавиатуру
        )
        # Остаемся в том же состоянии
        return State(State.TEST_FIXED_1.value + q_index)

    # Сохраняем ответ
    context.user_data["test_answers"][f"fixed_{q_index+1}"] = user_input
    logger.debug(f"User {user_id} answered fixed Q{q_index+1}: {user_input}")

    # Переходим к следующему вопросу
    q_index += 1
    context.user_data["question_index"] = q_index
    fixed_questions: List[str] = context.user_data.get("fixed_questions", [])

    if q_index < len(fixed_questions):
        await update.message.reply_text(fixed_questions[q_index], reply_markup=build_fixed_keyboard())
        # Переходим к следующему состоянию TEST_FIXED_...
        return State(State.TEST_FIXED_1.value + q_index)
    else:
        # Фиксированные вопросы закончились, переходим к открытым
        logger.info(f"User {user_id} finished fixed questions.")
        await update.message.reply_text(OPEN_QUESTIONS[0], reply_markup=CANCEL_KEYBOARD)
        return State.TEST_OPEN_1

async def test_open_1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответ на первый открытый вопрос."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    context.user_data["test_answers"]["open_1"] = user_input
    logger.debug(f"User {user_id} answered open Q1.")
    await update.message.reply_text(OPEN_QUESTIONS[1], reply_markup=CANCEL_KEYBOARD)
    return State.TEST_OPEN_2

async def test_open_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает ответ на второй открытый вопрос, сохраняет результаты и вызывает Gemini."""
    user_input: str = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    context.user_data["test_answers"]["open_2"] = user_input
    logger.info(f"User {user_id} finished open questions.")

    # --- Сохранение данных ---
    # TODO (Data Storage): Заменить сохранение в JSON на сохранение в БД в будущем.
    test_start_time_str: str = context.user_data.get("test_start_time_str", "unknown_time")
    filename: str = os.path.join(DATA_DIR, f"test_{user_id}_{test_start_time_str}.json")
    test_data: Dict[str, Any] = {
        "user_id": user_id,
        "timestamp": datetime.now().isoformat(), # Используем ISO формат
        "weekday": datetime.now().weekday(),
        "test_answers": context.user_data.get("test_answers", {})
    }
    try:
        async with aiofiles.open(filename, "w", encoding="utf-8") as f:
            await f.write(json.dumps(test_data, ensure_ascii=False, indent=4))
        logger.info(f"Тестовые данные пользователя {user_id} сохранены в {filename}")
    except Exception as e:
        logger.exception(f"Ошибка при сохранении теста пользователя {user_id}:")
        await update.message.reply_text(
            "Произошла ошибка при сохранении данных теста. Результаты могут быть не сохранены.",
            reply_markup=CANCEL_KEYBOARD
            )
        # Не прерываем диалог, попытаемся получить интерпретацию

    # --- Получение интерпретации от Gemini ---
    await update.message.reply_text("Спасибо за ответы! Анализирую ваше состояние...", reply_markup=ReplyKeyboardRemove())

    fixed_questions = context.user_data.get("fixed_questions", [])
    prompt = gemini_client.build_gemini_prompt_for_test(fixed_questions, test_data["test_answers"])
    interpretation = await gemini_client.call_gemini_api(prompt)

    # Сохраняем интерпретацию в файл (или позже в БД)
    test_data["interpretation"] = interpretation # Добавляем в словарь
    try:
        async with aiofiles.open(filename, "w", encoding="utf-8") as f:
             await f.write(json.dumps(test_data, ensure_ascii=False, indent=4))
        logger.info(f"Интерпретация теста для {user_id} добавлена в {filename}")
    except Exception as e:
        logger.exception(f"Ошибка при дозаписи интерпретации теста для {user_id}:")
        # Не уведомляем пользователя повторно

    # --- Формирование контекста для чата ---
    try:
        # Пытаемся рассчитать средние (для контекста чата)
        ans = test_data["test_answers"]
        s_1 = (int(ans.get("fixed_1", 0)) + int(ans.get("fixed_2", 0))) / 2
        s_2 = (int(ans.get("fixed_3", 0)) + int(ans.get("fixed_4", 0))) / 2
        s_3 = (int(ans.get("fixed_5", 0)) + int(ans.get("fixed_6", 0))) / 2
        # Округляем для краткости
        chat_context = f"Самочувствие: {s_1:.1f}/7, Активность: {s_2:.1f}/7, Настроение: {s_3:.1f}/7."
    except Exception:
        logger.warning(f"Не удалось рассчитать средние баллы для контекста чата user {user_id}")
        chat_context = "Результаты теста учтены." # Запасной вариант
    context.user_data["test_chat_context"] = chat_context # Используем другое имя, чтобы не путать с ретроспективой

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
        reply_markup=CANCEL_KEYBOARD, # Кнопка "Главное меню"
        parse_mode='Markdown'
    )
    return State.GEMINI_CHAT_TEST


async def gemini_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> State:
    """Обрабатывает сообщения пользователя в чате после теста."""
    user_input = update.message.text.strip()
    user_id = update.effective_user.id

    if user_input == "Главное меню":
        return await exit_to_main(update, context)

    logger.info(f"User {user_id} продолжает чат после теста: '{user_input[:50]}...'")
    chat_context = context.user_data.get("test_chat_context", "Результаты теста учтены.")
    prompt = gemini_client.build_followup_chat_prompt(user_input, chat_context)

    # Показываем, что бот думает
    await context.bot.send_chat_action(chat_id=user_id, action="typing")
    answer = await gemini_client.call_gemini_api(prompt, max_tokens=400) # Уменьшаем лимит для чата

    await update.message.reply_text(
        answer,
        reply_markup=CANCEL_KEYBOARD # Оставляем кнопку "Главное меню"
    )
    # Остаемся в том же состоянии чата
    return State.GEMINI_CHAT_TEST
