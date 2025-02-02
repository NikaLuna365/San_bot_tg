FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]

# docker-compose.yml
version: '3.8'

services:
  telegram-bot:
    build: .
    env_file:
      - .env
    restart: always
