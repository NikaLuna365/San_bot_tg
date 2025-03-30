# Dockerfile (обновленная версия)
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Устанавливаем системные зависимости (включая tzdata) и очищаем кэш apt
# !!! ВНИМАНИЕ: gcc удален. Если сборка упадет, верните 'gcc' в строку ниже !!!
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
# Установка системного часового пояса (хорошая практика)
ENV TZ=Etc/UTC

# --- Удалена строка с mkdir /app/reminder ---

COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

# Команда запуска остается прежней
CMD ["python", "main.py"]
