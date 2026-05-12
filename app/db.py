from sqlmodel import create_engine, Session, SQLModel
import os
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

# Получаем URL из переменных окружения
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/sudoku_db"
)

# Для синхронной работы (твой код использует синхронные сессии)
engine = create_engine(
    DATABASE_URL,
    echo=True,  # Показывать SQL запросы (можно выключить в production)
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

def get_session():
    """Dependency для получения сессии БД"""
    with Session(engine) as session:
        yield session

def init_db():
    """Создание таблиц при старте"""
    SQLModel.metadata.create_all(engine)
    logger.info("Database tables created")