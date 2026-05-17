# app/routers/users.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from typing import Optional, List, Dict
from datetime import datetime

from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

def _get_stats_for_last_n_games(games: list, n: int) -> dict:
    """Статистика по последним N играм"""
    recent = games[:n]
    total = len(recent)
    completed = sum(1 for g in recent if g.is_completed)
    win_rate = (completed / total * 100) if total > 0 else 0
    
    return {
        "total": total,
        "completed": completed,
        "win_rate": round(win_rate, 2)
    }

@router.get("/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    recent_games_limit: int = Query(0, description="0 - все игры, >0 - последние N"),
    session: Session = Depends(get_session)
):
    """Получить статистику пользователя"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем ВСЕ игры
    sudoku_games = session.exec(
        select(SudokuGame)
        .where(SudokuGame.user_id == user_id)
        .order_by(SudokuGame.created_at.desc())
    ).all()
    
    all_games = sudoku_games
    all_games.sort(key=lambda g: g.created_at, reverse=True)
    
    total_all_games = len(all_games)
    
    # Выбираем игры для статистики
    if recent_games_limit > 0:
        games_for_stats = all_games[:recent_games_limit]
    else:
        games_for_stats = all_games
    
    total_games = len(games_for_stats)
    completed_games = sum(1 for g in games_for_stats if g.is_completed)
    win_rate = (completed_games / total_games * 100) if total_games > 0 else 0
    
    # Статистика по сложности
    sudoku_by_difficulty = {}
    for game in sudoku_games:
        diff = game.difficulty
        sudoku_by_difficulty[diff] = sudoku_by_difficulty.get(diff, 0) + 1
    
    return {
        "user_id": user.id,
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        
        "total_games_all_time": total_all_games,
        "completed_games_all_time": sum(1 for g in all_games if g.is_completed),
        "win_rate_all_time": round(sum(1 for g in all_games if g.is_completed) / total_all_games * 100, 2) if total_all_games > 0 else 0,
        
        "stats_period": {
            "games_analyzed": total_games,
            "completed_analyzed": completed_games,
            "win_rate": round(win_rate, 2),
            "limit_type": "all_games" if recent_games_limit == 0 else f"last_{recent_games_limit}_games"
        },
        
        "games_by_type": {
            "sudoku": {
                "total": len(sudoku_games),
                "completed": sum(1 for g in sudoku_games if g.is_completed),
                "by_difficulty": sudoku_by_difficulty
            },
            "puzzle": {
                "total": 0,
                "completed": 0,
                "by_difficulty": {}
            }
        },
        
        "stats_by_period": {
            "last_10_games": _get_stats_for_last_n_games(all_games, 10),
            "last_20_games": _get_stats_for_last_n_games(all_games, 20),
            "last_50_games": _get_stats_for_last_n_games(all_games, 50),
            "all_games": {
                "total": total_all_games,
                "completed": sum(1 for g in all_games if g.is_completed),
                "win_rate": round(sum(1 for g in all_games if g.is_completed) / total_all_games * 100, 2) if total_all_games > 0 else 0
            }
        }
    }

@router.get("/{user_id}/profile")
async def get_user_profile(
    user_id: int,
    session: Session = Depends(get_session)
):
    """Получить профиль пользователя"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "id": user.id,
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        "created_at": user.created_at
    }

@router.put("/{user_id}/username")
async def update_username(
    user_id: int,
    new_username: str,
    session: Session = Depends(get_session)
):
    """Изменить имя пользователя"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    user.username = new_username
    session.add(user)
    session.commit()
    return {"message": "Username updated", "username": new_username}