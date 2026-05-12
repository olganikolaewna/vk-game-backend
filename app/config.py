import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # База данных
    DATABASE_URL = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/sudoku_db"
    )
    
    # AI сервис (оставляем как есть)
    AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://91.227.68.140:8000")
    
    # Другие настройки
    DEBUG = os.getenv("DEBUG", "True").lower() == "true"

settings = Settings()