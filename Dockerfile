FROM python:3.10-slim

# Отключаем запись .pyc и включаем небуферизированный вывод
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# Создаем папку для хранения данных (DATA_DIR)
RUN mkdir -p /app/data

# Установка зависимостей
COPY requirements.txt .

# Устанавливаем зависимости из requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Установка yandex-cloud-ml-sdk (убираем дублирование, добавляем в requirements.txt)
# Если не хочешь добавлять в requirements.txt, оставь так:
# RUN pip install --no-cache-dir yandex-cloud-ml-sdk

# Копируем весь исходный код проекта в контейнер
COPY . .

# Команда для запуска бота
CMD ["python", "bot.py"]
