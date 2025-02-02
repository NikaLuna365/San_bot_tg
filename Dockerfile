FROM python:3.10-slim

# Отключаем запись .pyc и включаем небуферизированный вывод
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Создаем папку для хранения данных (DATA_DIR)
RUN mkdir -p /app/data

# Копируем файл зависимостей и устанавливаем их
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Копируем весь исходный код проекта в контейнер (включая .env, если требуется)
COPY . .

# Команда для запуска бота
CMD ["python", "bot.py"]
