from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

@router.get("/{vk_user_id}/profile")
async def get_user_profile(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить профиль пользователя"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        "created_at": user.created_at
    }

@router.put("/{vk_user_id}/username")
async def update_username(
    vk_user_id: str,
    new_username: str,
    session: Session = Depends(get_session)
):
    """Изменить имя пользователя"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    user.username = new_username
    session.add(user)
    session.commit()
    return {"message": "Username updated"}


# app/routers/users.py (или где у вас эндпоинты)

@router.get("/api/v1/users/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    recent_games_limit: int = 20,  # Новый параметр
    session: Session = Depends(get_session)
):
    """
    Получить статистику пользователя
    
    Args:
        user_id: ID пользователя
        recent_games_limit: Сколько последних игр учитывать (0 - все игры)
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем все игры
    sudoku_games = session.exec(
        select(SudokuGame)
        .where(SudokuGame.user_id == user_id)
        .order_by(SudokuGame.created_at.desc())
    ).all()
    
    # Если нужно только последние N игр
    if recent_games_limit > 0:
        sudoku_games = sudoku_games[:recent_games_limit]
    
    total_games = len(sudoku_games)
    completed_games = sum(1 for g in sudoku_games if g.is_completed)
    win_rate = completed_games / total_games if total_games > 0 else 0
    
    # Статистика по сложности
    sudoku_by_difficulty = {}
    for game in sudoku_games:
        diff = game.difficulty
        sudoku_by_difficulty[diff] = sudoku_by_difficulty.get(diff, 0) + 1
    
    return {
        "total_games": total_games,
        "completed_games": completed_games,
        "win_rate": win_rate,
        "rating": user.rating,
        "games_by_type": {
            "sudoku": {"total": total_games, "completed": completed_games},
            "puzzle": {"total": 0, "completed": 0}
        },
        "sudoku_by_difficulty": sudoku_by_difficulty,
        "puzzle_by_difficulty": {},
        "stats_by_period": {  # Добавляем статистику по периодам
            "last_10_games": _get_stats_for_last_n_games(sudoku_games, 10),
            "last_20_games": _get_stats_for_last_n_games(sudoku_games, 20),
            "last_50_games": _get_stats_for_last_n_games(sudoku_games, 50),
            "all_games": {"total": total_games, "completed": completed_games, "win_rate": win_rate}
        }
    }

def _get_stats_for_last_n_games(games: List, n: int) -> Dict:
    """Статистика по последним N играм"""
    recent = games[:n]
    total = len(recent)
    completed = sum(1 for g in recent if g.is_completed)
    win_rate = completed / total if total > 0 else 0
    
    return {
        "total": total,
        "completed": completed,
        "win_rate": round(win_rate, 2)
    }