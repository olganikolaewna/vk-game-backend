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


@router.get("/api/v1/users/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    session: Session = Depends(get_session)
):
    """
    Получить общую статистику пользователя для отображения во фронте
    Показывает ВСЕ игры (не только последние)
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем ВСЕ игры
    sudoku_games = session.exec(
        select(SudokuGame)
        .where(SudokuGame.user_id == user_id)
    ).all()
    
    total_games = len(sudoku_games)
    completed_games = sum(1 for g in sudoku_games if g.is_completed)
    win_rate = (completed_games / total_games * 100) if total_games > 0 else 0
    
    # Статистика по сложности (ВСЕ игры)
    sudoku_by_difficulty = {}
    for game in sudoku_games:
        diff = game.difficulty
        sudoku_by_difficulty[diff] = sudoku_by_difficulty.get(diff, 0) + 1
    
    # Статистика по сложности с завершениями
    sudoku_completed_by_difficulty = {}
    for game in sudoku_games:
        if game.is_completed:
            diff = game.difficulty
            sudoku_completed_by_difficulty[diff] = sudoku_completed_by_difficulty.get(diff, 0) + 1
    
    return {
        "total_games": total_games,
        "completed_games": completed_games,
        "win_rate": round(win_rate, 2),
        "rating": user.rating,
        "games_by_difficulty": {
            "total": sudoku_by_difficulty,
            "completed": sudoku_completed_by_difficulty
        },
        "last_game": {
            "difficulty": sudoku_games[0].difficulty if sudoku_games else None,
            "completed": sudoku_games[0].is_completed if sudoku_games else None,
            "date": sudoku_games[0].created_at.isoformat() if sudoku_games else None
        }
    }