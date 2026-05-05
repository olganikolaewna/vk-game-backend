from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from datetime import datetime

class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[int] = Field(default=None, primary_key=True)
    vk_user_id: str = Field(index=True, unique=True)
    username: str = ""
    rating: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    games: List["SudokuGame"] = Relationship(back_populates="user")
    puzzle_games: List["PuzzleGame"] = Relationship(back_populates="user")


class SudokuGame(SQLModel, table=True):
    __tablename__ = "sudoku_games"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    user: User = Relationship(back_populates="games")
    
    puzzle: str           # JSON: исходная задача
    solution: str         # JSON: полное решение
    current_board: Optional[str] = Field(default=None)  # ← ДОБАВИТЬ ЭТУ СТРОКУ
    difficulty: str
    is_completed: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


class PuzzleGame(SQLModel, table=True):
    __tablename__ = "puzzle_games"
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id")
    user: User = Relationship(back_populates="puzzle_games")
    
    content_id: str
    image_data: str
    width: int
    height: int
    pieces_rows: int
    pieces_cols: int
    difficulty: str
    is_completed: bool = False
    current_state: str = Field(default="[]")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None