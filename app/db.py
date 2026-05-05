# app/db.py
from sqlmodel import SQLModel, create_engine, Session
import os

# Определяем путь к базе данных
# В Docker: /app/data/game_platform.db
# Локально: ./data/game_platform.db
DATABASE_DIR = os.getenv("DATABASE_DIR", "./data")
os.makedirs(DATABASE_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATABASE_DIR}/game_platform.db"

engine = create_engine(
    DATABASE_URL,
    echo=True,
    connect_args={"check_same_thread": False}
)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session