# Dockerfile (обновленная версия)
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Устанавливаем системные зависимости (включая tzdata) и очищаем кэш apt
# Если gcc не нужен, удалите 'gcc' из строки ниже
RUN apt-get update && apt-get install -y --no-install-recommends tzdata gcc && rm -rf /var/lib/apt/lists/*
# Установка системного часового пояса (хорошая практика)
ENV TZ=Etc/UTC

# Эта строка выглядит подозрительно, возможно, ее можно удалить?
RUN mkdir -p /app/reminder && chmod 777 /app/reminder

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

# !!! ВАЖНО: Изменена команда запуска на main.py !!!
CMD ["python", "main.py"]
