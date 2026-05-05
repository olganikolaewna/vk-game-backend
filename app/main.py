from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import logging
from sqlmodel import Session, select
from .db import get_session
import httpx
import os
from .routers import sudoku, stats, users, puzzle
from .db import create_db_and_tables  # <-- Импортируем функцию

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://91.227.68.140:8000")

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="VK Game Platform API",
    description="Бэкенд для платформы мини-игр с ИИ",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Разрешаем запросы от фронтенда
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 👇 ВАЖНО: создаём таблицы при старте приложения
@app.on_event("startup")
def on_startup():
    logger.info("Создание таблиц в базе данных...")
    create_db_and_tables()
    logger.info("Таблицы созданы (или уже существовали)")

# Подключаем роутер


app.include_router(sudoku.router, prefix="/api/v1/games", tags=["Sudoku"])
app.include_router(puzzle.router, prefix="/api/v1/games", tags=["Puzzle"]) 
app.include_router(stats.router)
app.include_router(users.router)


@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "VK Game Platform API is running", "status": "OK"}

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}

# app/main.py (дополнить)

@app.get("/health/detailed")
async def detailed_health_check(session: Session = Depends(get_session)):
    """Детальная проверка всех компонентов"""
    status = {
        "api": "ok",
        "database": "unknown",
        "ai_service": "unknown"
    }
    
    # Проверка БД
    try:
        session.exec(select(1)).first()
        status["database"] = "ok"
    except:
        status["database"] = "error"
    
    # Проверка ИИ-сервиса
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{AI_SERVICE_URL}/health")
            if r.status_code == 200:
                status["ai_service"] = "ok"
    except:
        status["ai_service"] = "error"
    
    return status