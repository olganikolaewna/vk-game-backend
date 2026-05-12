from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import logging
from sqlmodel import Session, select
from .db import get_session, init_db  # <-- Исправляем импорт
import httpx
import os
from .routers import sudoku, stats, users, puzzle

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
    logger.info("Инициализация базы данных PostgreSQL...")
    try:
        init_db()  # Используем правильную функцию из db.py
        logger.info("✅ База данных готова к работе")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# Подключаем роутеры
app.include_router(sudoku.router, prefix="/api/v1/games", tags=["Sudoku"])
app.include_router(puzzle.router, prefix="/api/v1/games", tags=["Puzzle"]) 
app.include_router(stats.router, prefix="/api/v1/stats", tags=["Stats"])
app.include_router(users.router, prefix="/api/v1/users", tags=["Users"])


@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "VK Game Platform API is running", "status": "OK"}


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


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
        # Простой запрос для проверки подключения
        from sqlmodel import text
        session.exec(text("SELECT 1")).first()
        status["database"] = "ok"
        logger.info("✅ Database health check passed")
    except Exception as e:
        status["database"] = f"error: {str(e)}"
        logger.error(f"❌ Database health check failed: {e}")
    
    # Проверка ИИ-сервиса
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{AI_SERVICE_URL}/health")
            if r.status_code == 200:
                status["ai_service"] = "ok"
            else:
                status["ai_service"] = f"error: HTTP {r.status_code}"
    except httpx.TimeoutException:
        status["ai_service"] = "error: timeout"
    except Exception as e:
        status["ai_service"] = f"error: {str(e)}"
    
    # Общий статус
    all_ok = all(v == "ok" for v in status.values())
    overall = "healthy" if all_ok else "degraded"
    
    return {
        "status": overall,
        "components": status,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat()
    }