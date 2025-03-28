FROM python:3.10-slim

# Отключаем запись байткода и включаем небуферизированный вывод
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Устанавливаем системные зависимости (например, gcc) и очищаем кэш apt
RUN apt-get update && apt-get install -y gcc && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN mkdir -p /app/reminder && chmod 777 /app/reminder
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Копируем весь исходный код проекта в контейнер
COPY . .

# Команда для запуска бота
CMD ["python", "bot.py"]
