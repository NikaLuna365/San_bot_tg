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
        return ConversationHandler.END

    logger.info(f"User {user_id} продолжает чат после ретроспективы: '{user_input[:50]}...'")

    # Добавляем сообщение пользователя в историю
    chat_history.append({"role": "user", "content": user_input})

    # --- Проверка на запрос резюме ---
    is_summary_request = False
    summary_keywords = ["итог", "резюме", "подведи", "обсуждали"]
    if any(keyword in user_input.lower() for keyword in summary_keywords) and len(user_input.split()) < 5:
        is_summary_request = True
        logger.info(f"User {user_id} запросил резюме чата ретроспективы.")

    # --- Выбор и построение промпта ---
    prompt = ""
    if is_summary_request:
        if len(chat_history) < 2:
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
    answer = ""
    if prompt:
        await context.bot.send_chat_action(chat_id=user_id, action="typing")
        # Для ретроспективы можно дать чуть больше токенов
        answer = await gemini_client.call_gemini_api(prompt, max_tokens=500)

        # Добавляем ответ AI в историю (кроме резюме)
        if not is_summary_request:
            chat_history.append({"role": "model", "content": answer})
            # Ограничиваем размер истории
            context.user_data["retro_chat_history"] = chat_history[-20:]
    elif not answer:
        pass # answer уже пустой или содержит сообщение об ошибке

    # --- Отправка ответа ---
    if answer:
        await update.message.reply_text(answer, reply_markup=CANCEL_KEYBOARD)
    else:
        logger.warning(f"Пустой ответ AI для user {user_id} в чате ретроспективы.")

    return State.GEMINI_CHAT_RETRO # Остаемся в состоянии чата ретроспективы
