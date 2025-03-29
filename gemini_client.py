# gemini_client.py
import os
import logging
import asyncio
from typing import Any, Dict, List

from google.generativeai import GenerativeModel, configure, types

# Импортируем тексты вопросов из констант
from .constants import WEEKDAY_FIXED_QUESTIONS, OPEN_QUESTIONS, RETRO_OPEN_QUESTIONS

logger = logging.getLogger(__name__)

# Настройка Gemini API ключа
API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    logger.warning("GEMINI_API_KEY не задан в переменных окружения. Функции AI не будут работать.")
    configure(api_key="DUMMY_KEY") # Чтобы избежать ошибки при импорте, если ключ не задан
else:
    configure(api_key=API_KEY)

# Выбор модели (можно вынести в .env)
GEMINI_MODEL_NAME = "gemini-1.5-flash" # Используем новую модель


def build_gemini_prompt_for_test(fixed_questions: List[str], test_answers: Dict[str, Any]) -> str:
    """Строит промпт для Gemini на основе ответов на ежедневный тест."""
    prompt = (
        "Ты профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
        "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, а 7 – исключительно позитивное состояние.\n"
        "Существует три интегральные шкалы:\n"
        "1. Самочувствие (среднее вопросов 1 и 2).\n"
        "2. Активность (среднее вопросов 3 и 4).\n"
        "3. Настроение (среднее вопросов 5 и 6).\n"
        "Диапазон каждой шкалы 1-7. Оцени каждую шкалу как Низкая (1-2.5), Средняя (3-5.5), Высокая (6-7).\n"
        "Пожалуйста, выполни все вычисления интегральных шкал и их оценку (Низкая/Средняя/Высокая) в уме, без вывода промежуточных данных или числовых значений шкал в ответе.\n"
        "Сформируй краткий (2-3 предложения) общий вывод об эмоциональном состоянии клиента на основе оценки шкал. Учитывай возможную противоречивость шкал.\n"
        "Затем, одним абзацем, кратко проанализируй ответы на открытые вопросы, связывая их с общим состоянием.\n"
        "Запрещается использование символа \"*\" для форматирования результатов. Ответ должен быть целостным текстом.\n\n"
        "Ответы клиента:\n"
    )
    # Добавляем фиксированные вопросы и ответы
    for i, question in enumerate(fixed_questions, start=1):
        key = f"fixed_{i}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{i}. {question}\n   Ответ: {answer}\n"
    # Добавляем открытые вопросы и ответы
    for j, question in enumerate(OPEN_QUESTIONS, start=1):
        key = f"open_{j}"
        answer = test_answers.get(key, "не указано")
        prompt += f"{len(fixed_questions) + j}. {question}\n   Ответ: {answer}\n"

    logger.debug(f"Промпт для теста:\n{prompt}")
    return prompt

def build_gemini_prompt_for_retro(
    averages: Dict[str, Any], test_count: int, open_answers: Dict[str, Any], period_days: int
) -> str:
    """Строит промпт для Gemini на основе данных ретроспективы."""
    prompt = (
        f"Ты профессиональный психолог. Проанализируй ретроспективу клиента.\n"
        f"Период анализа: последние {period_days} дней.\n"
        f"Количество пройденных тестов за период: {test_count}.\n\n"
        "Средние показатели по шкалам (1-7):\n"
    )
    # Добавляем средние значения
    for key, value in averages.items():
        avg_value = round(value, 1) if value is not None else 'нет данных'
        prompt += f"- {key}: {avg_value}\n"

    prompt += "\nОтветы на качественные вопросы ретроспективы:\n"
    # Добавляем ответы на открытые вопросы ретроспективы
    for idx, question in enumerate(RETRO_OPEN_QUESTIONS, start=1):
        key = f"retro_open_{idx}"
        answer = open_answers.get(key, "не указано")
        prompt += f"{idx}. {question}\n   Ответ: {answer}\n"

    prompt += (
        "\nЗадание:\n"
        "1. Кратко (2-3 предложения) оцени общую динамику состояния клиента за период на основе средних показателей.\n"
        "2. Развернуто (3-5 предложений) проанализируй ответы на качественные вопросы, выдели ключевые события, факторы продуктивности/трудностей, оправданность ожиданий и вынесенные уроки.\n"
        "3. Сформулируй 1-2 поддерживающих или наводящих вопроса для клиента, чтобы стимулировать дальнейшую рефлексию по итогам периода.\n"
        "Запрещается использование символа \"*\" для форматирования."
    )
    logger.debug(f"Промпт для ретроспективы:\n{prompt}")
    return prompt

def build_followup_chat_prompt(user_message: str, chat_context: str) -> str:
    """Строит промпт для Gemini для продолжения диалога после теста."""
    prompt = (
        "Ты — поддерживающий психолог. Клиент только что прошел ежедневный тест и хочет обсудить его результаты или свое состояние.\n"
        "Обращайся к пользователю на «Вы». Будь эмпатичным и кратким.\n"
        f"Контекст результатов теста: {chat_context}\n\n"
        "Вопрос или сообщение клиента:\n"
        f"{user_message}\n\n"
        "Твой ответ (будь кратким и поддерживающим):"
    )
    return prompt

def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
    """Строит промпт для Gemini для продолжения диалога после ретроспективы."""
    prompt = (
        "Ты — психолог-консультант. Клиент ознакомился с результатами ретроспективы за прошедший период и задает уточняющий вопрос или делится мыслями.\n"
        "Обращайся к пользователю на «Вы». Отвечай по существу вопроса, опираясь на предоставленный контекст.\n"
        f"Контекст анализа за период: {week_overview}\n\n"
        "Вопрос или сообщение клиента:\n"
        f"{user_message}\n\n"
        "Твой ответ (по существу, с опорой на контекст):"
    )
    return prompt

async def call_gemini_api(prompt: str, max_tokens: int = 600) -> str:
    """Выполняет вызов Gemini API и возвращает текстовый ответ."""
    if not API_KEY:
        logger.error("GEMINI_API_KEY не настроен. Возвращена заглушка.")
        return "Ошибка: Ключ Gemini API не настроен."

    try:
        model = GenerativeModel(GEMINI_MODEL_NAME)
        logger.info(f"Отправка запроса к Gemini ({GEMINI_MODEL_NAME})...")
        # logger.debug(f"Промпт для Gemini:\n{prompt}") # Раскомментировать для отладки промптов

        generation_config = types.GenerationConfig(
            candidate_count=1,
            max_output_tokens=max_tokens,
            temperature=0.6, # Слегка увеличил температуру для большей вариативности
            top_p=1.0,
            top_k=40
        )

        # Используем asyncio.to_thread для неблокирующего вызова синхронной функции
        response = await asyncio.to_thread(
            model.generate_content,
            contents=[prompt], # Передаем как список
            generation_config=generation_config
        )

        logger.debug(f"Полный ответ от Gemini: {response}")

        # Проверяем наличие текста в ответе
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
             interpretation = "".join(part.text for part in response.candidates[0].content.parts)
             logger.info("Успешный ответ от Gemini получен.")
             # logger.debug(f"Интерпретация Gemini: {interpretation}")
             return interpretation.strip()
        else:
             # Логируем причину отсутствия ответа, если она есть
             block_reason = response.prompt_feedback.block_reason if response.prompt_feedback else "Неизвестно"
             finish_reason = response.candidates[0].finish_reason if response.candidates else "Неизвестно"
             logger.warning(f"Ответ от Gemini не содержит текста. Причина блокировки: {block_reason}, Причина завершения: {finish_reason}")
             return "К сожалению, не удалось получить ответ от AI. Попробуйте переформулировать."

    except Exception as e:
        logger.exception("Ошибка при вызове Gemini API:")
        return "Произошла ошибка при обращении к AI. Попробуйте позже."
