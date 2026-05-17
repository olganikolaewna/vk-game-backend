FROM python:3.11-slim

WORKDIR /app

# Используем зеркала Debian для ускорения и надёжности
RUN sed -i 's/deb.debian.org/mirror.yandex.ru/g' /etc/apt/sources.list.d/debian.sources || \
    sed -i 's/deb.debian.org/archive.debian.org/g' /etc/apt/sources.list

# Устанавливаем системные зависимости для psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Копируем и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем папку с кодом приложения
COPY ./app ./app

# Указываем порт
ENV PORT=7860

# Создаём непривилегированного пользователя для безопасности
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Команда для запуска
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT