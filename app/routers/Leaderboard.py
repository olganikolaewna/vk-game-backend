# app/routers/leaderboard.py (новый файл)
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, SudokuGame

router = APIRouter(prefix="/api/v1/leaderboard", tags=["Leaderboard"])

@router.get("/api/v1/leaderboard")
async def get_leaderboard(
    game_type: str = "sudoku",  # на будущее для других игр
    limit: int = 10,
    session: Session = Depends(get_session)
):
    """Топ игроков по рейтингу"""
    users = session.exec(
        select(User)
        .order_by(User.rating.desc())
        .limit(limit)
    ).all()
    
    return [
        {
            "vk_user_id": u.vk_user_id,
            "username": u.username,
            "rating": u.rating,
            "games_played": len(u.games)
        }
        for u in users
    ]

@router.get("/api/v1/leaderboard/daily")
async def get_daily_leaderboard(
    session: Session = Depends(get_session)
):
    """Топ игроков за сегодня"""
    from datetime import date, datetime
    today_start = datetime.combine(date.today(), datetime.min.time())
    
    # Сложный запрос с группировкой
    # Возвращает игроков с наибольшим количеством побед сегодня
    pass