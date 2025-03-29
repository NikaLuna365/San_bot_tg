import os
 import json
 import logging
 import asyncio
 from calendar import monthrange
 from datetime import datetime, timedelta, time, date
 from typing import Any, Dict, List
 
 import aiofiles
 from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
 from telegram.ext import (
     Application, CommandHandler, MessageHandler, ConversationHandler,
     filters, CallbackContext
     Application,
     CommandHandler,
     MessageHandler,
     ConversationHandler,
     filters,
     CallbackContext,
 )
 
 # Импорт SDK для Gemini от Google
 from google.generativeai import GenerativeModel, configure, types
 
 # Импорт функций для работы с БД (ежедневные напоминания и запланированные ретроспективы)
 # Импорт функций для работы с базой данных
 from db import (
     create_db_pool,
     upsert_daily_reminder,
     get_active_daily_reminders,
     update_last_sent_daily,
     upsert_scheduled_retrospective,
     get_active_scheduled_retrospectives,
     update_last_sent_scheduled_retrospective,
     # Старый функционал (может быть нужен):
     upsert_weekly_retrospective,
     get_active_weekly_retrospectives,
     update_last_sent_weekly
     update_last_sent_weekly,
 )
 
 # ----------------------- Настройка логирования -----------------------
 logging.basicConfig(
     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
     level=logging.INFO
     format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
 )
 logger = logging.getLogger(__name__)
 
 # ----------------------- Полный список вопросов (НЕ упрощать) -----------------------
 WEEKDAY_FIXED_QUESTIONS = {
 # ----------------------- Константы состояний диалогов -----------------------
 # Состояния для теста
 TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
 # Состояния для мгновенной ретроспективы
 RETRO_CHOICE = 8
 RETRO_PERIOD_CHOICE = 9
 RETRO_OPEN_1 = 10
 RETRO_OPEN_2 = 11
 RETRO_OPEN_3 = 12
 RETRO_OPEN_4 = 13
 RETRO_CHAT = 14
 AFTER_TEST_CHOICE, GEMINI_CHAT = range(15, 17)
 # Состояния для напоминаний
 REMINDER_CHOICE, REMINDER_DAILY_TIME, REMINDER_DAILY_REMIND = range(100, 103)
 # Новые состояния для запланированной ретроспективы
 RETRO_SCHEDULE_DAY_NEW = 200
 RETRO_SCHEDULE_CURRENT = 201
 RETRO_SCHEDULE_TARGET = 202
 RETRO_SCHEDULE_MODE = 203
 
 # ----------------------- Глобальные переменные для планирования задач -----------------------
 scheduled_reminders: Dict[int, Any] = {}
 scheduled_retrospectives: Dict[int, Any] = {}
 
 # ----------------------- Тексты вопросов -----------------------
 WEEKDAY_FIXED_QUESTIONS: Dict[int, List[str]] = {
     0: [
         "Оцените, насколько ваше самочувствие сегодня ближе к хорошему или плохому (при 1 – крайне плохое самочувствие, а 7 – превосходное самочувствие)",
         "Оцените, чувствуете ли вы себя сильным или слабым (при 1 – чрезвычайно слабым, а 7 – исключительно сильным)",
         "Оцените свою активность: насколько вы ощущаете себя пассивным или активным (при 1 – крайне пассивным, а 7 – исключительно активным)",
         "Оцените вашу подвижность: насколько вы ощущаете себя малоподвижным или подвижным (при 1 – крайне малоподвижным, а 7 – чрезвычайно подвижным)",
         "Оцените ваше эмоциональное состояние: насколько вы чувствуете себя весёлым или грустным (при 1 – крайне грустным, а 7 – исключительно весёлым)",
         "Оцените ваше настроение: насколько оно ближе к хорошему или плохому (при 1 – очень плохое настроение, а 7 – прекрасное настроение)"
         "Оцените ваше настроение: насколько оно ближе к хорошему или плохому (при 1 – очень плохое настроение, а 7 – прекрасное настроение)",
     ],
     1: [
         "Оцените свою работоспособность: насколько вы чувствуете себя работоспособным или разбитым (при 1 – совершенно разбитым, а 7 – на пике работоспособности)",
         "Оцените уровень своих сил: чувствуете ли вы себя полным сил или обессиленным (при 1 – абсолютно обессиленным, а 7 – полон энергии)",
         "Оцените скорость ваших мыслей или действий: насколько вы ощущаете себя медлительным или быстрым (при 1 – крайне медлительным, а 7 – исключительно быстрым)",
         "Оцените вашу активность: насколько вы чувствуете себя бездеятельным или деятельным (при 1 – полностью бездеятельным, а 7 – очень деятельным)",
         "Оцените своё счастье: насколько вы ощущаете себя счастливым или несчастным (при 1 – крайне несчастным, а 7 – чрезвычайно счастливым)",
         "Оцените вашу жизнерадостность: насколько вы чувствуете себя жизнерадостным или мрачным (при 1 – полностью мрачным, а 7 – исключительно жизнерадостным)"
     ],
     2: [
         "Оцените, насколько вы чувствуете напряжение или расслабленность (при 1 – невероятно напряжённый, а 7 – совершенно расслабленный)",
         "Оцените ваше здоровье: ощущаете ли вы себя здоровым или больным (при 1 – крайне больным, а 7 – абсолютно здоровым)",
         "Оцените вашу вовлечённость: насколько вы чувствуете себя безучастным или увлечённым (при 1 – совершенно безучастным, а 7 – полностью увлечённым)",
         "Оцените, насколько вы равнодушны или заинтересованы (при 1 – крайне равнодушны, а 7 – чрезвычайно заинтересованы)",
         "Оцените ваш эмоциональный подъем: насколько вы чувствуете восторг или уныние (при 1 – совершенно унылый, а 7 – безмерно восторженный)",
         "Оцените вашу радость: насколько вы чувствуете радость или печаль (при 1 – крайне печальный, а 7 – исключительно радостный)"
     ],
     3: [
         "Оцените, насколько вы чувствуете себя отдохнувшим или усталым (при 1 – совершенно усталым, а 7 – полностью отдохнувшим)",
         "Оцените, насколько вы ощущаете свежесть или изнурённость (при 1 – абсолютно изнурённый, а 7 – исключительно свежий)",
         "Оцените уровень своей сонливости или возбуждения (при 1 – крайне сонливый, а 7 – невероятно возбуждённый)",
         "Оцените, насколько у вас желание отдохнуть или работать (при 1 – исключительно желание отдохнуть, а 7 – сильное желание работать)",
         "Оцените ваше спокойствие: насколько вы чувствуете себя взволнованным или спокойным (при 1 – полностью взволнованным, а 7 – исключительно спокойным)",
         "Оцените ваш оптимизм: насколько вы чувствуете себя пессимистичным или оптимистичным (при 1 – крайне пессимистичным, а 7 – чрезвычайно оптимистичным)"
     ],
     4: [
         "Оцените вашу выносливость: насколько вы чувствуете себя выносливым или утомляемым (при 1 – совершенно утомляемым, а 7 – исключительно выносливым)",
         "Оцените уровень вашей бодрости: насколько вы чувствуете себя бодрым или вялым (при 1 – крайне вялым, а 7 – полностью бодрым)",
         "Оцените способность соображать: насколько вам сложно или легко соображать (при 1 – соображать крайне трудно, а 7 – соображать очень легко)",
         "Оцените вашу внимательность: насколько вы чувствуете себя рассеянным или внимательным (при 1 – совершенно рассеянным, а 7 – исключительно внимательным)",
         "Оцените вашу надежду: насколько вы чувствуете себя разочарованным или полным надежд (при 1 – полностью разочарованным, а 7 – полон надежд)",
         "Оцените ваше удовлетворение: насколько вы чувствуете себя недовольным или довольным (при 1 – абсолютно недовольным, а 7 – исключительно довольным)"
     ],
     5: [
         "Оцените ваше бодрствование: насколько вы чувствуете себя сонным или бодрствующим (при 1 – крайне сонным, а 7 – совершенно бодрствующим)",
         "Оцените, насколько вы чувствуете себя напряжённым или расслабленным (при 1 – невероятно напряжённым, а 7 – абсолютно расслабленным)",
         "Оцените, насколько вы ощущаете свежесть или утомлённость (при 1 – совершенно утомлённый, а 7 – исключительно свежий)",
         "Оцените ваше здоровье: насколько вы ощущаете себя нездоровым или здоровым (при 1 – абсолютно нездоровым, а 7 – полностью здоровым)",
         "Оцените уровень вашей энергии: насколько вы чувствуете себя вялым или энергичным (при 1 – чрезвычайно вялым, а 7 – исключительно энергичным)",
         "Оцените вашу решительность: насколько вы чувствуете себя колеблющимся или решительным (при 1 – совершенно колеблющимся, а 7 – исключительно решительным)"
         "Оцените вашу жизнерадостность: насколько вы чувствуете себя жизнерадостным или мрачным (при 1 – полностью мрачным, а 7 – исключительно жизнерадостным)",
     ],
     6: [
         "Оцените, насколько вы чувствуете себя сосредоточенным или рассеянным (при 1 – невероятно рассеянный, а 7 – чрезвычайно сосредоточенный)",
         "Оцените, насколько вы чувствуете себя пассивным или деятельным (при 1 – полностью пассивным, а 7 – исключительно деятельным)",
         "Оцените ваш оптимизм: насколько вы чувствуете себя пессимистичным или оптимистичным (при 1 – крайне пессимистичным, а 7 – чрезвычайно оптимистичным)",
         "Оцените ваше спокойствие: насколько вы чувствуете себя взволнованным или спокойным (при 1 – совершенно взволнованным, а 7 – исключительно спокойным)",
         "Оцените вашу уверенность: насколько вы чувствуете себя неуверенным или уверенным (при 1 – абсолютно неуверенным, а 7 – полностью уверенным)",
         "Оцените ваше удовлетворение: насколько вы чувствуете себя недовольным или довольным (при 1 – крайне недовольным, а 7 – исключительно довольным)"
     ]
     # Добавьте остальные дни недели аналогичным образом...
 }
 # Если день недели не найден – используем вопросы для 0-го дня.
 if 0 not in WEEKDAY_FIXED_QUESTIONS:
     WEEKDAY_FIXED_QUESTIONS[0] = WEEKDAY_FIXED_QUESTIONS.get(1, [])
 
 OPEN_QUESTIONS = [
 OPEN_QUESTIONS: List[str] = [
     "7. Какие три слова лучше всего описывают ваше текущее состояние?",
     "8. Что больше всего повлияло на ваше состояние сегодня?"
     "8. Что больше всего повлияло на ваше состояние сегодня?",
 ]
 
 RETRO_OPEN_QUESTIONS = [
 RETRO_OPEN_QUESTIONS: List[str] = [
     "Какие события на этой неделе больше всего повлияли на ваше общее состояние?",
     "Какие факторы способствовали вашей продуктивности, а какие, наоборот, мешали?",
     "Какие у вас были ожидания от этой недели, и насколько они оправдались?",
     "Какие уроки вы вынесли из прошедшей недели, и как вы планируете использовать этот опыт в будущем?"
     "Какие уроки вы вынесли из прошедшей недели, и как вы планируете использовать этот опыт в будущем?",
 ]
 
 # ----------------------- Состояния для диалогов -----------------------
 # Состояния для теста
 TEST_FIXED_1, TEST_FIXED_2, TEST_FIXED_3, TEST_FIXED_4, TEST_FIXED_5, TEST_FIXED_6, TEST_OPEN_1, TEST_OPEN_2 = range(8)
 # Состояния для мгновенной ретроспективы
 RETRO_CHOICE = 8
 RETRO_PERIOD_CHOICE = 9
 RETRO_OPEN_1 = 10
 RETRO_OPEN_2 = 11
 RETRO_OPEN_3 = 12
 RETRO_OPEN_4 = 13
 RETRO_CHAT = 14
 AFTER_TEST_CHOICE, GEMINI_CHAT = range(15, 17)
 # Состояния для ежедневных напоминаний
 REMINDER_CHOICE, REMINDER_DAILY_TIME, REMINDER_DAILY_REMIND = range(100, 103)
 # Новые состояния для запланированной ретроспективы
 RETRO_SCHEDULE_DAY_NEW = 200
 RETRO_SCHEDULE_CURRENT = 201
 RETRO_SCHEDULE_TARGET = 202
 RETRO_SCHEDULE_MODE = 203
 
 scheduled_reminders = {}
 scheduled_retrospectives = {}
 
 # ----------------------- Функции вспомогательные -----------------------
 # ----------------------- Вспомогательные функции -----------------------
 def build_fixed_keyboard() -> ReplyKeyboardMarkup:
     keyboard = [[str(i) for i in range(1, 8)], ["Главное меню"]]
     return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
 
 async def exit_to_main(update: Update, context: CallbackContext) -> int:
     context.user_data.clear()
     main_menu_keyboard = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
     reply_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True, one_time_keyboard=True)
     await update.message.reply_text("Возвращаемся в главное меню.\n\nДобро пожаловать! Выберите действие:", reply_markup=reply_markup)
     await update.message.reply_text(
         "Возвращаемся в главное меню.\n\nДобро пожаловать! Выберите действие:", reply_markup=reply_markup
     )
     return ConversationHandler.END
 
 async def start(update: Update, context: CallbackContext) -> None:
     main_menu_keyboard = [["Тест", "Ретроспектива"], ["Напоминание", "Помощь"]]
     reply_markup = ReplyKeyboardMarkup(main_menu_keyboard, resize_keyboard=True, one_time_keyboard=True)
     await update.message.reply_text("Добро пожаловать! Выберите действие:", reply_markup=reply_markup)
 
 def remaining_days_in_month() -> int:
     today = datetime.now()
     _, last_day = monthrange(today.year, today.month)
     return last_day - today.day
 
 def build_gemini_prompt_for_test(fixed_questions: list, test_answers: dict) -> str:
     prompt = ("Вы профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
               "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, а 7 – исключительно позитивное состояние.\n"
               "Каждая шкала состоит из 2 вопросов (итоговый балл = сумма двух оценок, диапазон 2–14: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).\n"
               "Пожалуйста, выполните все вычисления итоговых баллов в уме без вывода промежуточных данных. "
               "Сформируйте один абзац общего анализа итоговых баллов и динамики состояния клиента, а затем сразу кратко опишите анализ открытых вопросов.\n"
               "Запрещается использование символа \"*\" для форматирования результатов.\n\n")
 def build_gemini_prompt_for_test(fixed_questions: List[str], test_answers: Dict[str, Any]) -> str:
     prompt = (
         "Вы профессиональный психолог с 10-летним стажем. Клиент прошёл ежедневный опрос.\n"
         "Фиксированные вопросы оцениваются по 7-балльной шкале, где 1 – крайне негативное состояние, а 7 – исключительно позитивное состояние.\n"
         "Каждая шкала состоит из 2 вопросов (итоговый балл = сумма двух оценок, диапазон 2–14: 2–5 – низкий, 6–10 – средний, 11–14 – высокий).\n"
         "Пожалуйста, выполните все вычисления итоговых баллов в уме без вывода промежуточных данных. "
         "Сформируйте один абзац общего анализа итоговых баллов и динамики состояния клиента, а затем сразу кратко опишите анализ открытых вопросов.\n"
         "Запрещается использование символа \"*\" для форматирования результатов.\n\n"
     )
     for i, question in enumerate(fixed_questions, start=1):
         key = f"fixed_{i}"
         answer = test_answers.get(key, "не указано")
         prompt += f"{i}. {question}\n   Ответ: {answer}\n"
     # Добавляем 2 открытых вопроса
     for j, question in enumerate(OPEN_QUESTIONS, start=1):
         key = f"open_{j}"
         answer = test_answers.get(key, "не указано")
         prompt += f"{len(fixed_questions)+j}. {question}\n   Ответ: {answer}\n"
         prompt += f"{len(fixed_questions) + j}. {question}\n   Ответ: {answer}\n"
     logger.info(f"Промпт для теста:\n{prompt}")
     return prompt
 
 def build_gemini_prompt_for_retro(averages: dict, test_count: int, open_answers: dict, period_days: int) -> str:
 def build_gemini_prompt_for_retro(
     averages: Dict[str, Any], test_count: int, open_answers: Dict[str, Any], period_days: int
 ) -> str:
     prompt = f"Ретроспектива: за последние {period_days} дней проведено {test_count} тестов.\n"
     prompt += "Средние показатели:\n"
     for key, value in averages.items():
         prompt += f"{key}: {value if value is not None else 'не указано'}\n"
     prompt += "\nКачественный анализ:\n"
     prompt += f"1. {RETRO_OPEN_QUESTIONS[0]}\n   Ответ: {open_answers.get('retro_open_1', 'не указано')}\n"
     prompt += f"2. {RETRO_OPEN_QUESTIONS[1]}\n   Ответ: {open_answers.get('retro_open_2', 'не указано')}\n"
     prompt += f"3. {RETRO_OPEN_QUESTIONS[2]}\n   Ответ: {open_answers.get('retro_open_3', 'не указано')}\n"
     prompt += f"4. {RETRO_OPEN_QUESTIONS[3]}\n   Ответ: {open_answers.get('retro_open_4', 'не указано')}\n"
     for idx, question in enumerate(RETRO_OPEN_QUESTIONS, start=1):
         key = f"retro_open_{idx}"
         answer = open_answers.get(key, "не указано")
         prompt += f"{idx}. {question}\n   Ответ: {answer}\n"
     prompt += "\nПожалуйста, сформируйте аналитический отчет по динамике состояния клиента за указанный период."
     return prompt
 
 def build_followup_chat_prompt(user_message: str, chat_context: str) -> str:
     prompt = (
         "Вы — высококвалифицированный психолог с более чем десятилетним стажем. "
         "Обращайтесь к пользователю на «Вы». "
         "Ваш профессионализм подкреплён глубокими академическими знаниями и практическим опытом. "
         "Контекст теста: " + chat_context + "\n\n"
         "Вопрос пользователя: " + user_message
     )
     return prompt
 
 def build_gemini_prompt_for_retro_chat(user_message: str, week_overview: str) -> str:
     prompt = (
         "Вы — высококвалифицированный психолог с более чем десятилетним стажем. "
         "Обращайтесь к пользователю на «Вы». "
         "Пожалуйста, отвечайте на вопросы, рассматривая их как отдельные аспекты анализа состояния клиента, без прямого упоминания ретроспективы. "
         "Контекст анализа: " + week_overview + "\n\n"
         "Вопрос пользователя: " + user_message
     )
     return prompt
 
 async def call_gemini_api(prompt: str, max_tokens: int = 600) -> dict:
 async def call_gemini_api(prompt: str, max_tokens: int = 600) -> Dict[str, str]:
     api_key = os.getenv("GEMINI_API_KEY")
     if not api_key:
         logger.error("GEMINI_API_KEY не задан в переменных окружения.")
         return {"interpretation": "Ошибка: API ключ не задан."}
     try:
         configure(api_key=api_key)
         model = GenerativeModel("gemini-2.0-flash")
         logger.info(f"Отправка запроса к Gemini API с промптом:\n{prompt}")
         gen_config = types.GenerationConfig(
             candidate_count=1,
             max_output_tokens=max_tokens,
             temperature=0.4,
             top_p=1.0,
             top_k=40
         )
         # Оборачиваем синхронный вызов в asyncio.to_thread
         response = await asyncio.to_thread(lambda: model.generate_content([prompt], generation_config=gen_config))
         logger.debug(f"Полный ответ от Gemini: {vars(response)}")
         if hasattr(response, "text") and response.text:
             interpretation = response.text
         elif hasattr(response, "content") and response.content:
             interpretation = response.content
         else:
             interpretation = vars(response).get("content", "Нет ответа от Gemini.")
         logger.info(f"Ответ от Gemini: {interpretation}")
         return {"interpretation": interpretation}
     except Exception as e:
         logger.exception("Ошибка при вызове Gemini API:")
         return {"interpretation": "Ошибка при обращении к Gemini API."}
 
 # ----------------------- Обработчики теста -----------------------
 async def test_cancel(update: Update, context: CallbackContext) -> int:
     await update.message.reply_text("Тест отменён.", reply_markup=ReplyKeyboardRemove())
     return ConversationHandler.END
 
 async def test_start(update: Update, context: CallbackContext) -> int:
     """Начало теста: задаём первый вопрос дня."""
     context.user_data['test_answers'] = {}
     context.user_data['test_start_time'] = datetime.now().strftime("%Y%m%d_%H%M%S")
     context.user_data['question_index'] = 0
     current_day = datetime.now().weekday()
     fixed_questions = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
     context.user_data['fixed_questions'] = fixed_questions
     context.user_data["test_answers"] = {}
     context.user_data["test_start_time"] = datetime.now().strftime("%Y%m%d_%H%M%S")
     context.user_data["question_index"] = 0
     current_day: int = datetime.now().weekday()
     fixed_questions: List[str] = WEEKDAY_FIXED_QUESTIONS.get(current_day, WEEKDAY_FIXED_QUESTIONS[0])
     context.user_data["fixed_questions"] = fixed_questions
 
     await update.message.reply_text(fixed_questions[0], reply_markup=build_fixed_keyboard())
     return TEST_FIXED_1
 
 async def test_fixed_handler(update: Update, context: CallbackContext) -> int:
     """Обрабатываем ответы на фиксированные вопросы (1–6)."""
     user_input = update.message.text.strip()
     user_input: str = update.message.text.strip()
     if user_input.lower() == "главное меню":
         return await exit_to_main(update, context)
     index = context.user_data.get('question_index', 0)
     index: int = context.user_data.get("question_index", 0)
     if user_input not in [str(i) for i in range(1, 8)]:
         await update.message.reply_text("Пожалуйста, выберите вариант от 1 до 7.", reply_markup=build_fixed_keyboard())
         return TEST_FIXED_1 + index
 
     context.user_data[f"fixed_{index+1}"] = user_input
     index += 1
     context.user_data['question_index'] = index
     fixed_questions = context.user_data.get('fixed_questions', [])
 
     context.user_data["question_index"] = index
     fixed_questions: List[str] = context.user_data.get("fixed_questions", [])
     if index < len(fixed_questions):
         await update.message.reply_text(fixed_questions[index], reply_markup=build_fixed_keyboard())
         return TEST_FIXED_1 + index
     else:
         # Переходим к первому открытому вопросу
         await update.message.reply_text(
             OPEN_QUESTIONS[0],
             reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
         )
         return TEST_OPEN_1
 
 async def test_open_1(update: Update, context: CallbackContext) -> int:
     """Обрабатываем ответ на первый открытый вопрос."""
     user_input = update.message.text.strip()
     user_input: str = update.message.text.strip()
     if user_input.lower() == "главное меню":
         return await exit_to_main(update, context)
     context.user_data['open_1'] = user_input
     context.user_data["open_1"] = user_input
     await update.message.reply_text(
         OPEN_QUESTIONS[1],
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return TEST_OPEN_2
 
 async def test_open_2(update: Update, context: CallbackContext) -> int:
     """Обрабатываем ответ на второй открытый вопрос, сохраняем тест, вызываем интерпретацию."""
     user_input = update.message.text.strip()
     user_input: str = update.message.text.strip()
     if user_input.lower() == "главное меню":
         return await exit_to_main(update, context)
 
     context.user_data['open_2'] = user_input
     user_id = update.message.from_user.id
     test_start_time = context.user_data.get("test_start_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
 
     # Сохраняем результаты теста в JSON
     filename = os.path.join("data", f"{user_id}_{test_start_time}.json")
     test_data = {
     context.user_data["open_2"] = user_input
     user_id: int = update.message.from_user.id
     test_start_time: str = context.user_data.get("test_start_time", datetime.now().strftime("%Y%m%d_%H%M%S"))
     filename: str = os.path.join("data", f"{user_id}_{test_start_time}.json")
     test_data: Dict[str, Any] = {
         "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "test_answers": {
             k: v for k, v in context.user_data.items() if k.startswith("fixed_") or k.startswith("open_")
         }
         "test_answers": {k: v for k, v in context.user_data.items() if k.startswith("fixed_") or k.startswith("open_")}
     }
     try:
         with open(filename, "w", encoding="utf-8") as f:
             json.dump(test_data, f, ensure_ascii=False, indent=4)
         async with aiofiles.open(filename, "w", encoding="utf-8") as f:
             await f.write(json.dumps(test_data, ensure_ascii=False, indent=4))
         logger.info(f"Тестовые данные сохранены в {filename}")
     except Exception as e:
         logger.exception("Ошибка при сохранении теста:")
         await update.message.reply_text("Произошла ошибка при сохранении данных теста.")
         return ConversationHandler.END
 
     # Генерация интерпретации через Gemini
     prompt = build_gemini_prompt_for_test(
         context.user_data.get("fixed_questions", []),
         test_data["test_answers"]
     )
     gemini_response = await call_gemini_api(prompt)
     interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
     prompt: str = build_gemini_prompt_for_test(context.user_data.get("fixed_questions", []), test_data["test_answers"])
     gemini_response: Dict[str, str] = await call_gemini_api(prompt)
     interpretation: str = gemini_response.get("interpretation", "Нет интерпретации.")
 
     # Формируем контекст для последующего чата
     try:
         self_feeling = (int(context.user_data.get("fixed_1")) + int(context.user_data.get("fixed_2"))) / 2
         activity = (int(context.user_data.get("fixed_3")) + int(context.user_data.get("fixed_4"))) / 2
         mood = (int(context.user_data.get("fixed_5")) + int(context.user_data.get("fixed_6"))) / 2
         chat_context = f"Самочувствие: {self_feeling}, Активность: {activity}, Настроение: {mood}. Открытые ответы учтены."
         chat_context: str = f"Самочувствие: {self_feeling}, Активность: {activity}, Настроение: {mood}. Открытые ответы учтены."
     except Exception as e:
         logger.exception("Ошибка при формировании контекста опроса:")
         chat_context = "Данные теста учтены."
     context.user_data["chat_context"] = chat_context
 
     message = (
     message: str = (
         f"Результат анализа:\n{interpretation}\n\n"
         "Теперь вы можете общаться с ИИ-психологом по результатам теста. "
         "Отправляйте свои сообщения, и они будут учитываться в рамках этого чата.\n"
         "Для выхода в главное меню нажмите кнопку «Главное меню»."
     )
     await update.message.reply_text(
         message,
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return GEMINI_CHAT
 
 async def after_test_choice_handler(update: Update, context: CallbackContext) -> int:
     """Пока не используется. Можно доработать логику после теста."""
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     await update.message.reply_text("Вы выбрали дальнейшее действие после теста. (Функциональность ещё не реализована.)",
                                     reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True))
     await update.message.reply_text(
         "Вы выбрали дальнейшее действие после теста. (Функциональность ещё не реализована.)",
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return GEMINI_CHAT
 
 async def gemini_chat_handler(update: Update, context: CallbackContext) -> int:
     """Чат с ИИ после теста."""
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     chat_context = context.user_data.get("chat_context", "")
     prompt = build_followup_chat_prompt(update.message.text.strip(), chat_context)
     gemini_response = await call_gemini_api(prompt)
     answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
     chat_context: str = context.user_data.get("chat_context", "")
     prompt: str = build_followup_chat_prompt(update.message.text.strip(), chat_context)
     gemini_response: Dict[str, str] = await call_gemini_api(prompt)
     answer: str = gemini_response.get("interpretation", "Нет ответа от Gemini.")
     await update.message.reply_text(
         answer,
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return GEMINI_CHAT
 
 # ----------------------- Обработчики мгновенной ретроспективы (RETRO) -----------------------
 
 # ----------------------- Обработчики мгновенной ретроспективы -----------------------
 async def retrospective_start(update: Update, context: CallbackContext) -> int:
     """Точка входа в ретроспективу (мгновенную или запланированную)."""
     keyboard = [["Ретроспектива сейчас", "Запланировать ретроспективу", "Главное меню"]]
     await update.message.reply_text(
         "Выберите вариант ретроспективы:",
         reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_CHOICE
 
 async def retrospective_choice_handler(update: Update, context: CallbackContext) -> int:
     """Обрабатываем выбор: мгновенная ретроспектива или запланированная."""
     choice = update.message.text.strip().lower()
     choice: str = update.message.text.strip().lower()
     if choice == "главное меню":
         return await exit_to_main(update, context)
     elif choice == "ретроспектива сейчас":
         # Предлагаем выбрать 7 или 14 дней
         keyboard = [["Ретроспектива за 1 неделю", "Ретроспектива за 2 недели"], ["Главное меню"]]
         await update.message.reply_text(
             "Выберите период ретроспективы:",
             reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
         )
         return RETRO_PERIOD_CHOICE
     elif choice == "запланировать ретроспективу":
         # Переход к новому диалогу планирования
         await update.message.reply_text(
             "Введите день недели для запланированной ретроспективы (например, 'Понедельник'):",
             reply_markup=ReplyKeyboardMarkup(
                 [["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье", "Главное меню"]],
                 resize_keyboard=True,
                 one_time_keyboard=True
             )
         )
         return RETRO_SCHEDULE_DAY_NEW
     else:
         await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.")
         return RETRO_CHOICE
 
 async def retrospective_period_choice(update: Update, context: CallbackContext) -> int:
     """Выбор периода (7 или 14 дней) для мгновенной ретроспективы."""
     period_choice = update.message.text.strip().lower()
     period_choice: str = update.message.text.strip().lower()
     if period_choice == "главное меню":
         return await exit_to_main(update, context)
     if period_choice in ["ретроспектива за 1 неделю", "1 неделя", "1", "1 неделю"]:
         await update.message.reply_text("Формируется ретроспектива за последние 7 дней...")
         await run_retrospective_now(update, context, period_days=7)
     elif period_choice in ["ретроспектива за 2 недели", "2 недели", "2", "2 неделя"]:
         await update.message.reply_text("Формируется ретроспектива за последние 14 дней...")
         await run_retrospective_now(update, context, period_days=14)
     else:
         await update.message.reply_text("Пожалуйста, выберите один из предложенных вариантов.")
         return RETRO_PERIOD_CHOICE
     return RETRO_CHAT
 
 # ----------------------- Обработчики ретроспективных вопросов (мгновенных) -----------------------
 async def retro_open_1(update: Update, context: CallbackContext) -> int:
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     context.user_data["retro_open_1"] = update.message.text.strip()
     await update.message.reply_text(
         RETRO_OPEN_QUESTIONS[1],
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_OPEN_2
 
 async def retro_open_2(update: Update, context: CallbackContext) -> int:
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     context.user_data["retro_open_2"] = update.message.text.strip()
     await update.message.reply_text(
         RETRO_OPEN_QUESTIONS[2],
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_OPEN_3
 
 async def retro_open_3(update: Update, context: CallbackContext) -> int:
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     context.user_data["retro_open_3"] = update.message.text.strip()
     await update.message.reply_text(
         RETRO_OPEN_QUESTIONS[3],
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_OPEN_4
 
 async def retro_open_4(update: Update, context: CallbackContext) -> int:
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     context.user_data["retro_open_4"] = update.message.text.strip()
     await update.message.reply_text("Запускается ретроспектива по результатам теста...")
     await run_retrospective_now(update, context, period_days=7)  # По умолчанию 7 дней, можно модифицировать
     await run_retrospective_now(update, context, period_days=7)
     return RETRO_CHAT
 
 async def run_retrospective_now(update: Update, context: CallbackContext, period_days: int = 7):
     """Выполняем мгновенную ретроспективу на заданное кол-во дней."""
     user_id = update.message.from_user.id
     now = datetime.now()
     period_start = now - timedelta(days=period_days)
 async def run_retrospective_now(update: Update, context: CallbackContext, period_days: int = 7) -> None:
     user_id: int = update.message.from_user.id
     now: datetime = datetime.now()
     period_start: datetime = now - timedelta(days=period_days)
 
     user_files = [f for f in os.listdir("data") if f.startswith(f"{user_id}_") and f.endswith(".json")]
     tests = []
     # Чтение файлов тестов асинхронно
     user_files: List[str] = [f for f in os.listdir("data") if f.startswith(f"{user_id}_") and f.endswith(".json")]
     tests: List[Dict[str, Any]] = []
     for file in user_files:
         file_path = os.path.join("data", file)
         file_path: str = os.path.join("data", file)
         try:
             with open(file_path, "r", encoding="utf-8") as f:
                 data = json.load(f)
                 ts = datetime.strptime(data.get("timestamp", ""), "%Y-%m-%d %H:%M:%S")
                 if period_start <= ts <= now:
                     tests.append(data)
             async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                 content = await f.read()
                 data = json.loads(content)
                 ts_str: str = data.get("timestamp", "")
                 if ts_str:
                     ts: datetime = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                     if period_start <= ts <= now:
                         tests.append(data)
         except Exception as e:
             logger.exception(f"Ошибка чтения файла {file_path}:")
 
     if len(tests) < 4:
         await update.message.reply_text(
             f"Недостаточно данных для ретроспективы за последние {period_days} дней. Пройдите тест минимум 4 раза за указанный период.",
             reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
         )
         return
 
     sums = {f"fixed_{i}": 0 for i in range(1, 7)}
     counts = {f"fixed_{i}": 0 for i in range(1, 7)}
     sums: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}
     counts: Dict[str, int] = {f"fixed_{i}": 0 for i in range(1, 7)}
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
     averages: Dict[str, Any] = {}
     if counts["fixed_1"] and counts["fixed_2"]:
         averages["Самочувствие"] = round((sums["fixed_1"]/counts["fixed_1"] + sums["fixed_2"]/counts["fixed_2"]) / 2, 2)
         averages["Самочувствие"] = round((sums["fixed_1"] / counts["fixed_1"] + sums["fixed_2"] / counts["fixed_2"]) / 2, 2)
     else:
         averages["Самочувствие"] = None
     if counts["fixed_3"] and counts["fixed_4"]:
         averages["Активность"] = round((sums["fixed_3"]/counts["fixed_3"] + sums["fixed_4"]/counts["fixed_4"]) / 2, 2)
         averages["Активность"] = round((sums["fixed_3"] / counts["fixed_3"] + sums["fixed_4"] / counts["fixed_4"]) / 2, 2)
     else:
         averages["Активность"] = None
     if counts["fixed_5"] and counts["fixed_6"]:
         averages["Настроение"] = round((sums["fixed_5"]/counts["fixed_5"] + sums["fixed_6"]/counts["fixed_6"]) / 2, 2)
         averages["Настроение"] = round((sums["fixed_5"] / counts["fixed_5"] + sums["fixed_6"] / counts["fixed_6"]) / 2, 2)
     else:
         averages["Настроение"] = None
 
     open_answers = {
     open_answers: Dict[str, Any] = {
         "retro_open_1": context.user_data.get("retro_open_1", "не указано"),
         "retro_open_2": context.user_data.get("retro_open_2", "не указано"),
         "retro_open_3": context.user_data.get("retro_open_3", "не указано"),
         "retro_open_4": context.user_data.get("retro_open_4", "не указано")
         "retro_open_4": context.user_data.get("retro_open_4", "не указано"),
     }
 
     prompt = build_gemini_prompt_for_retro(averages, len(tests), open_answers, period_days)
     gemini_response = await call_gemini_api(prompt)
     interpretation = gemini_response.get("interpretation", "Нет интерпретации.")
 
     prompt: str = build_gemini_prompt_for_retro(averages, len(tests), open_answers, period_days)
     gemini_response: Dict[str, str] = await call_gemini_api(prompt)
     interpretation: str = gemini_response.get("interpretation", "Нет интерпретации.")
     context.user_data["last_retrospective_week"] = now.isocalendar()[1]
 
     # Сохраняем результаты ретроспективы
     retro_filename = os.path.join("data", f"{user_id}_retro_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
     retro_data = {
     retro_filename: str = os.path.join("data", f"{user_id}_retro_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
     retro_data: Dict[str, Any] = {
         "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         "test_count": len(tests),
         "averages": averages,
         "open_answers": open_answers,
         "interpretation": interpretation,
         "period_days": period_days
         "period_days": period_days,
     }
     try:
         with open(retro_filename, "w", encoding="utf-8") as f:
             json.dump(retro_data, f, ensure_ascii=False, indent=4)
         async with aiofiles.open(retro_filename, "w", encoding="utf-8") as f:
             await f.write(json.dumps(retro_data, ensure_ascii=False, indent=4))
         logger.info(f"Данные ретроспективы сохранены в {retro_filename}")
     except Exception as e:
         logger.exception("Ошибка при сохранении данных ретроспективы:")
 
     # Формируем week_overview
     week_overview = (f"Самочувствие: {averages.get('Самочувствие', 'не указано')}, "
                      f"Активность: {averages.get('Активность', 'не указано')}, "
                      f"Настроение: {averages.get('Настроение', 'не указано')}. "
                      "Ответы на качественные вопросы учтены.")
     week_overview: str = (
         f"Самочувствие: {averages.get('Самочувствие', 'не указано')}, "
         f"Активность: {averages.get('Активность', 'не указано')}, "
         f"Настроение: {averages.get('Настроение', 'не указано')}. "
         "Ответы на качественные вопросы учтены."
     )
     context.user_data["week_overview"] = week_overview
 
     message = (f"Ретроспектива за последние {period_days} дней:\n{interpretation}\n\n"
                "Если хотите обсудить итоги периода, задайте свой вопрос.\n"
                "Для выхода в главное меню нажмите кнопку «Главное меню».")
     message: str = (
         f"Ретроспектива за последние {period_days} дней:\n{interpretation}\n\n"
         "Если хотите обсудить итоги периода, задайте свой вопрос.\n"
         "Для выхода в главное меню нажмите кнопку «Главное меню»."
     )
     await update.message.reply_text(
         message,
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_CHAT
 
 async def retrospective_chat_handler(update: Update, context: CallbackContext) -> int:
     """Продолжение беседы после ретроспективы."""
     if update.message.text.strip().lower() == "главное меню":
         return await exit_to_main(update, context)
     week_overview = context.user_data.get("week_overview", "")
     prompt = build_gemini_prompt_for_retro_chat(update.message.text.strip(), week_overview)
     gemini_response = await call_gemini_api(prompt, max_tokens=600)
     answer = gemini_response.get("interpretation", "Нет ответа от Gemini.")
     week_overview: str = context.user_data.get("week_overview", "")
     prompt: str = build_gemini_prompt_for_retro_chat(update.message.text.strip(), week_overview)
     gemini_response: Dict[str, str] = await call_gemini_api(prompt, max_tokens=600)
     answer: str = gemini_response.get("interpretation", "Нет ответа от Gemini.")
     await update.message.reply_text(
         answer,
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_CHAT
 
 # ----------------------- Запланированная ретроспектива -----------------------
 # ----------------------- Обработчики запланированной ретроспективы -----------------------
 async def retro_schedule_day_handler(update: Update, context: CallbackContext) -> int:
     """Шаг 1: Пользователь выбирает день недели."""
     day_text = update.message.text.strip().lower()
     days_mapping = {
     day_text: str = update.message.text.strip().lower()
     days_mapping: Dict[str, int] = {
         "понедельник": 0,
         "вторник": 1,
         "среда": 2,
         "четверг": 3,
         "пятница": 4,
         "суббота": 5,
         "воскресенье": 6
         "воскресенье": 6,
     }
     if day_text == "главное меню" or day_text not in days_mapping:
         await update.message.reply_text("Неверный ввод. Пожалуйста, выберите день недели или 'Главное меню'.")
         return RETRO_SCHEDULE_DAY_NEW
     context.user_data["retro_schedule_day"] = days_mapping[day_text]
     await update.message.reply_text(
         "Введите ваше текущее время (например, 15:30):",
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_SCHEDULE_CURRENT
 
 async def retro_schedule_current_handler(update: Update, context: CallbackContext) -> int:
     """Шаг 2: Пользователь вводит своё текущее время."""
     current_time_str = update.message.text.strip()
     current_time_str: str = update.message.text.strip()
     if current_time_str.lower() == "главное меню":
         return await exit_to_main(update, context)
     try:
         datetime.strptime(current_time_str, "%H:%M")
     except ValueError:
         await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ.")
         return RETRO_SCHEDULE_CURRENT
     context.user_data["retro_current_time"] = current_time_str
     await update.message.reply_text(
         "Введите желаемое время проведения ретроспективы (например, 08:00):",
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_SCHEDULE_TARGET
 
 async def retro_schedule_target_handler(update: Update, context: CallbackContext) -> int:
     """Шаг 3: Пользователь вводит желаемое время ретроспективы."""
     target_time_str = update.message.text.strip()
     target_time_str: str = update.message.text.strip()
     if target_time_str.lower() == "главное меню":
         return await exit_to_main(update, context)
     try:
         datetime.strptime(target_time_str, "%H:%M")
     except ValueError:
         await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ.")
         return RETRO_SCHEDULE_TARGET
     context.user_data["retro_target_time"] = target_time_str
     await update.message.reply_text(
         "Выберите режим ретроспективы:",
         reply_markup=ReplyKeyboardMarkup([["Еженедельная", "Двухнедельная", "Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return RETRO_SCHEDULE_MODE
 
 async def retro_schedule_mode_handler(update: Update, context: CallbackContext) -> int:
     """Шаг 4: Пользователь выбирает еженедельную или двухнедельную ретроспективу."""
     mode_text = update.message.text.strip().lower()
     mode_text: str = update.message.text.strip().lower()
     if mode_text == "главное меню":
         return await exit_to_main(update, context)
     if mode_text in ["еженедельная", "1"]:
         mode = "weekly"
     elif mode_text in ["двухнедельная", "2"]:
         mode = "biweekly"
     else:
         await update.message.reply_text("Пожалуйста, выберите 'Еженедельная' или 'Двухнедельная'.")
         return RETRO_SCHEDULE_MODE
     context.user_data["retro_mode"] = mode
 
     # Вычисляем серверное время
     try:
         user_current_time = datetime.strptime(context.user_data["retro_current_time"], "%H:%M").time()
         user_target_time = datetime.strptime(context.user_data["retro_target_time"], "%H:%M").time()
     except Exception as e:
         logger.exception("Ошибка при разборе введённого времени:")
         await update.message.reply_text("Ошибка в формате времени. Попробуйте ещё раз.")
         return ConversationHandler.END
 
     server_now = datetime.utcnow()
     server_now: datetime = datetime.utcnow()
     server_date = server_now.date()
     user_current_dt = datetime.combine(server_date, user_current_time)
     offset = server_now - user_current_dt
 
     user_target_dt = datetime.combine(server_date, user_target_time)
     computed_target_dt = user_target_dt + offset
 
     def get_next_occurrence(target_weekday, target_dt, current_dt):
         days_ahead = target_weekday - target_dt.weekday()
     def get_next_occurrence(target_weekday: int, target_dt: datetime, current_dt: datetime) -> datetime:
         days_ahead = context.user_data["retro_schedule_day"] - target_dt.weekday()
         if days_ahead < 0 or (days_ahead == 0 and target_dt <= current_dt):
             days_ahead += 7
         return target_dt + timedelta(days=days_ahead)
 
     scheduled_dt = get_next_occurrence(context.user_data["retro_schedule_day"], computed_target_dt, server_now)
     scheduled_time = scheduled_dt.time()
     logger.info(f"Пользователь указал время ретроспективы {user_target_time}, вычисленное серверное время: {scheduled_time}")
 
     pool = context.bot_data.get("db_pool")
     try:
         await upsert_scheduled_retrospective(
             pool,
             update.message.from_user.id,
             context.user_data["retro_schedule_day"],
             user_target_time,   # локальное время
             scheduled_time,     # серверное время
             context.user_data["retro_current_time"],
             scheduled_time.strftime("%H:%M:%S"),
             mode
         )
     except Exception as e:
         logger.exception("Ошибка при сохранении запланированной ретроспективы:")
         await update.message.reply_text("Ошибка при сохранении ретроспективы. Попробуйте ещё раз позже.")
         return ConversationHandler.END
 
     # Планируем задачу
     initial_delay = (scheduled_dt - server_now).total_seconds()
     interval = 7 * 24 * 3600 if mode == "weekly" else 14 * 24 * 3600
 
     job = context.job_queue.run_repeating(
         send_retrospective_notification,
         interval=interval,
         first=initial_delay,
         data={'user_id': update.message.from_user.id, 'mode': mode},
         name=str(update.message.from_user.id) + "_retro"
     )
     scheduled_retrospectives[update.message.from_user.id] = job
 
     await update.message.reply_text(
         "Запланированная ретроспектива установлена!",
         reply_markup=ReplyKeyboardMarkup([["Пройти ретроспективу", "Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return ConversationHandler.END
 
 async def send_retrospective_notification(context: CallbackContext):
     """Отправка уведомления о начале ретроспективы (еженедельной/двухнедельной)."""
 async def send_retrospective_notification(context: CallbackContext) -> None:
     job_data = context.job.data
     user_id = job_data['user_id']
     await context.bot.send_message(
         chat_id=user_id,
         text="Напоминание: пришло время пройти запланированную ретроспективу!",
         reply_markup=ReplyKeyboardMarkup([["Пройти ретроспективу", "Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
 
 # ----------------------- Функция загрузки запланированных ретроспектив при старте -----------------------
 async def schedule_active_retrospectives(app: Application):
     """При запуске бота восстанавливаем ранее запланированные ретроспективы из БД."""
 async def schedule_active_retrospectives(app: Application) -> None:
     pool = app.bot_data.get("db_pool")
     try:
         retros = await get_active_scheduled_retrospectives(pool)
         for r in retros:
             try:
                 if isinstance(r["local_time"], str):
                     local_time_obj = datetime.strptime(r["local_time"], "%H:%M:%S").time()
                 else:
                     local_time_obj = r["local_time"]
                 if isinstance(r["server_time"], str):
                     server_time_obj = datetime.strptime(r["server_time"], "%H:%M:%S").time()
                 else:
                     server_time_obj = r["server_time"]
                 local_time_obj = datetime.strptime(r["local_time"], "%H:%M:%S").time() if isinstance(r["local_time"], str) else r["local_time"]
                 server_time_obj = datetime.strptime(r["server_time"], "%H:%M:%S").time() if isinstance(r["server_time"], str) else r["server_time"]
             except Exception as e:
                 logger.exception(f"Ошибка преобразования времени для ретроспективы (local: {r['local_time']}, server: {r['server_time']}):")
                 continue
             user_id = r["user_id"]
             server_now = datetime.utcnow()
             server_target_dt = datetime.combine(server_now.date(), server_time_obj)
 
             def get_next_occurrence(target_weekday, target_dt, current_dt):
             def get_next_occurrence(target_dt: datetime, current_dt: datetime) -> datetime:
                 days_ahead = r["scheduled_day"] - target_dt.weekday()
                 if days_ahead < 0 or (days_ahead == 0 and target_dt <= current_dt):
                     days_ahead += 7
                 return target_dt + timedelta(days=days_ahead)
 
             scheduled_dt = get_next_occurrence(r["scheduled_day"], server_target_dt, server_now)
             scheduled_dt = get_next_occurrence(server_target_dt, server_now)
             initial_delay = (scheduled_dt - server_now).total_seconds()
             mode = r["retrospective_type"]
             interval = 7 * 24 * 3600 if mode == "weekly" else 14 * 24 * 3600
 
             if user_id in scheduled_retrospectives:
                 scheduled_retrospectives[user_id].schedule_removal()
 
             job = app.job_queue.run_repeating(
                 send_retrospective_notification,
                 interval=interval,
                 first=initial_delay,
                 data={'user_id': user_id, 'mode': mode},
                 name=str(user_id) + "_retro"
             )
             scheduled_retrospectives[user_id] = job
             logger.info(f"Запланированная ретроспектива для пользователя {user_id} загружена, серверное время: {server_time_obj}")
     except Exception as e:
         logger.exception("Ошибка при загрузке запланированных ретроспектив из БД:")
 
 # ----------------------- Обработчики раздела "Напоминание" -----------------------
 # ----------------------- Обработчики напоминаний -----------------------
 async def reminder_start(update: Update, context: CallbackContext) -> int:
     keyboard = [["Ежедневный тест", "Ретроспектива"], ["Главное меню"]]
     await update.message.reply_text("Выберите тип напоминания:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
     return REMINDER_CHOICE
 
 async def reminder_daily_test(update: Update, context: CallbackContext) -> int:
     """Шаг 1: спрашиваем текущее время пользователя."""
     user_choice = update.message.text.strip().lower()
     user_choice: str = update.message.text.strip().lower()
     if user_choice == "ежедневный тест":
         await update.message.reply_text("Сколько у вас сейчас времени? (например, 15:30)")
         return REMINDER_DAILY_TIME
     elif user_choice == "ретроспектива":
         await update.message.reply_text("Функция ретроспективы в разработке.")
         return ConversationHandler.END
     else:
         return await exit_to_main(update, context)
 
 async def reminder_receive_current_time(update: Update, context: CallbackContext) -> int:
     """Шаг 2: спрашиваем, во сколько напоминать о ежедневном тесте."""
     current_time = update.message.text.strip()
     current_time: str = update.message.text.strip()
     context.user_data["current_time"] = current_time
     await update.message.reply_text("Во сколько напоминать о ежедневном тесте? (например, 08:00)")
     return REMINDER_DAILY_REMIND
 
 async def send_daily_reminder(context: CallbackContext):
     """Функция, вызываемая job_queue для напоминания о тесте."""
 async def send_daily_reminder(context: CallbackContext) -> None:
     job_data = context.job.data
     user_id = job_data['user_id']
     await context.bot.send_message(
         chat_id=user_id,
         text="Напоминание: пришло время пройти ежедневный тест!"
     )
 
 async def reminder_set_daily(update: Update, context: CallbackContext) -> int:
     """Шаг 3: устанавливаем ежедневное напоминание."""
     reminder_time_str = update.message.text.strip()  # формат "HH:MM"
     user_id = update.message.from_user.id
     reminder_time_str: str = update.message.text.strip()
     user_id: int = update.message.from_user.id
     try:
         reminder_time_obj = datetime.strptime(reminder_time_str, "%H:%M").time()
     except ValueError:
         await update.message.reply_text("Неверный формат времени. Пожалуйста, введите время в формате ЧЧ:ММ.")
         return REMINDER_DAILY_REMIND
 
     if user_id in scheduled_reminders:
         scheduled_reminders[user_id].schedule_removal()
 
     pool = context.bot_data.get("db_pool")
     try:
         await upsert_daily_reminder(pool, user_id, reminder_time_obj)
         await upsert_daily_reminder(pool, user_id, reminder_time_str)
     except Exception as e:
         logger.exception("Ошибка при сохранении напоминания в БД:")
         await update.message.reply_text("Ошибка при сохранении напоминания. Попробуйте еще раз позже.")
         return ConversationHandler.END
 
     job = context.job_queue.run_daily(
         send_daily_reminder,
         reminder_time_obj,
         data={'user_id': user_id},
         name=str(user_id)
     )
     scheduled_reminders[user_id] = job
 
     await update.message.reply_text(
         "Напоминание установлено!",
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
     return ConversationHandler.END
 
 # ----------------------- Дополнительные команды -----------------------
 async def help_command(update: Update, context: CallbackContext) -> None:
     help_text = (
         "Наш бот предназначен для оценки вашего состояния с помощью короткого теста.\n\n"
         "Команды:\n"
         "• Тест – пройти тест (фиксированные вопросы, зависящие от дня недели, и 2 открытых вопроса).\n"
         "• Ретроспектива – анализ изменений за последний период (за 7 или 14 дней) и обсуждение итогов.\n"
         "• Напоминание – установить напоминание для прохождения теста.\n"
         "• Помощь – справочная информация.\n\n"
         "Во всех этапах работы доступна кнопка «Главное меню» для возврата в стартовое меню."
     )
     await update.message.reply_text(
         help_text,
         reply_markup=ReplyKeyboardMarkup([["Главное меню"]], resize_keyboard=True, one_time_keyboard=True)
     )
 
 async def error_handler(update: object, context: CallbackContext) -> None:
     logger.exception(f"Ошибка при обработке обновления {update}:")
 
 # ----------------------- Основная функция -----------------------
 def main() -> None:
     loop = asyncio.new_event_loop()
     asyncio.set_event_loop(loop)
     TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
     if not TOKEN:
         logger.error("TELEGRAM_BOT_TOKEN не задан в переменных окружения.")
         return
 
     # Создаём приложение
     app = Application.builder().token(TOKEN).post_init(schedule_active_retrospectives).build()
 
     # Создаём пул соединений с БД
     pool = loop.run_until_complete(create_db_pool())
     app.bot_data["db_pool"] = pool
 
     # ConversationHandler для теста
     test_conv_handler = ConversationHandler(
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
             AFTER_TEST_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, after_test_choice_handler)],
             GEMINI_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, gemini_chat_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", test_cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
         ],
         allow_reentry=True
     )
     app.add_handler(test_conv_handler)
 
     # ConversationHandler для мгновенной ретроспективы
     retro_conv_handler = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Ретроспектива$"), retrospective_start)],
         states={
             RETRO_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_choice_handler)],
             RETRO_PERIOD_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_period_choice)],
             RETRO_OPEN_1: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_1)],
             RETRO_OPEN_2: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_2)],
             RETRO_OPEN_3: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_3)],
             RETRO_OPEN_4: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_open_4)],
             RETRO_CHAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, retrospective_chat_handler)]
         },
         fallbacks=[
             CommandHandler("cancel", test_cancel),
             MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
         ],
         allow_reentry=True
     )
     app.add_handler(retro_conv_handler)
 
     # ConversationHandler для планирования ретроспективы
     retro_schedule_conv_handler = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Запланировать ретроспективу$"), retrospective_choice_handler)],
         states={
             RETRO_SCHEDULE_DAY_NEW: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_day_handler)],
             RETRO_SCHEDULE_CURRENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_current_handler)],
             RETRO_SCHEDULE_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_target_handler)],
             RETRO_SCHEDULE_MODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, retro_schedule_mode_handler)]
         },
         fallbacks=[
             MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
         ],
         fallbacks=[MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
         allow_reentry=True
     )
     app.add_handler(retro_schedule_conv_handler)
 
     # ConversationHandler для напоминаний
     reminder_conv_handler = ConversationHandler(
         entry_points=[MessageHandler(filters.Regex("^Напоминание$"), reminder_start)],
         states={
             REMINDER_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_daily_test)],
             REMINDER_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_receive_current_time)],
             REMINDER_DAILY_REMIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, reminder_set_daily)]
         },
         fallbacks=[
             MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)
         ],
         fallbacks=[MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main)],
         allow_reentry=True
     )
     app.add_handler(reminder_conv_handler)
 
     # Стандартные команды
     app.add_handler(CommandHandler("start", start))
     app.add_handler(CommandHandler("help", help_command))
     app.add_handler(MessageHandler(filters.Regex("^Помощь$"), help_command))
     app.add_handler(MessageHandler(filters.Regex("^(?i)главное меню$"), exit_to_main))
 
     # Глобальный обработчик ошибок
     app.add_error_handler(error_handler)
 
     # Запускаем бота
     app.run_polling()
 
 if __name__ == "__main__":
     main()
