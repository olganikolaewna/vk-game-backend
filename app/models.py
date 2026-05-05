from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime

# Модель пользователя (синхронизируем с VK)
class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    vk_user_id: str = Field(index=True, unique=True)  # ID из VK
    username: str = ""
    rating: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    games: List["SudokuGame"] = Relationship(back_populates="user")

# Модель для сохранения игры в судоку
class SudokuGame(SQLModel, table=True):
    __tablename__ = "sudoku_games"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    user: User = Relationship(back_populates="games")
    
    puzzle: str  # Исходная задача (JSON строка)
    solution: str  # Полное решение (JSON строка)
    difficulty: str
    is_completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None