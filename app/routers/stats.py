# app/routers/stats.py - ОЧИЩЕННАЯ ВЕРСИЯ

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, date, timedelta
from typing import Optional
import logging

from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Stats & Leaderboard"])


@router.get("/user/{vk_user_id}")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Статистика игрока (ВСЕ игры)"""
    user = session.exec(select(User).where(User.vk_user_id == str(vk_user_id))).first()
    if not user:
        raise HTTPException(404, "User not found")
    
    sudoku_games = session.exec(select(SudokuGame).where(SudokuGame.user_id == user.id)).all()
    
    total = len(sudoku_games)
    completed = sum(1 for g in sudoku_games if g.is_completed)
    
    # Статистика по сложностям
    easy_total = len([g for g in sudoku_games if g.difficulty == "easy"])
    easy_completed = len([g for g in sudoku_games if g.difficulty == "easy" and g.is_completed])
    medium_total = len([g for g in sudoku_games if g.difficulty == "medium"])
    medium_completed = len([g for g in sudoku_games if g.difficulty == "medium" and g.is_completed])
    
    return {
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        "total_games": total,
        "completed_games": completed,
        "win_rate": round(completed / total * 100, 1) if total > 0 else 0,
        "by_difficulty": {
            "easy": {"total": easy_total, "completed": easy_completed, "win_rate": round(easy_completed / easy_total * 100, 1) if easy_total > 0 else 0},
            "medium": {"total": medium_total, "completed": medium_completed, "win_rate": round(medium_completed / medium_total * 100, 1) if medium_total > 0 else 0}
        }
    }


@router.get("/stats/global")
async def get_global_stats(session: Session = Depends(get_session)):
    """Общая статистика платформы"""
    total_users = session.exec(select(func.count(User.id))).one()
    total_games = session.exec(select(func.count(SudokuGame.id))).one()
    completed_games = session.exec(select(func.count(SudokuGame.id)).where(SudokuGame.is_completed == True)).one()
    
    return {
        "total_users": total_users,
        "total_games": total_games,
        "completed_games": completed_games,
        "completion_rate": round(completed_games / total_games * 100, 1) if total_games > 0 else 0
    }


@router.get("/leaderboard")
async def get_leaderboard(limit: int = 10, session: Session = Depends(get_session)):
    """Топ игроков по рейтингу"""
    users = session.exec(select(User).order_by(User.rating.desc()).limit(limit)).all()
    
    result = []
    for idx, user in enumerate(users):
        games_count = session.exec(select(func.count(SudokuGame.id)).where(SudokuGame.user_id == user.id)).one()
        result.append({
            "rank": idx + 1,
            "vk_user_id": user.vk_user_id,
            "username": user.username,
            "rating": user.rating,
            "games_played": games_count
        })
    
    return {"leaderboard": result}