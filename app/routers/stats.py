# app/routers/stats.py
from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from datetime import datetime, date

from ..db import get_session
from ..models import User, SudokuGame

router = APIRouter(prefix="/api/v1/leaderboard", tags=["Leaderboard"])

@router.get("/stats/global")
async def get_global_stats(session: Session = Depends(get_session)):
    """Общая статистика платформы"""
    # Подсчёт всех пользователей
    total_users = session.exec(select(func.count(User.id))).one()
    
    # Подсчёт всех игр
    total_games = session.exec(select(func.count(SudokuGame.id))).one()
    
    # Игры за сегодня (с начала текущего дня)
    today_start = datetime.combine(date.today(), datetime.min.time())
    games_today = session.exec(
        select(func.count(SudokuGame.id))
        .where(SudokuGame.created_at >= today_start)
    ).one()
    
    # Активные пользователи сегодня (кто создавал игры)
    active_users = session.exec(
        select(func.count(func.distinct(SudokuGame.user_id)))
        .where(SudokuGame.created_at >= today_start)
    ).one()
    
    return {
        "total_users": total_users,
        "total_games": total_games,
        "games_today": games_today,
        "active_users": active_users
    }