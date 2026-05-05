FROM python:3.11-slim

WORKDIR /app

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем папку с кодом приложения
COPY ./app ./app

# Создаём папку для базы данных с правильными правами
RUN mkdir -p /app/data && chmod -R 777 /app/data

# Указываем порт
ENV PORT=7860
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT 