# app/routers/users.py - ОЧИЩЕННАЯ ВЕРСИЯ

from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

router = APIRouter(prefix="/api/v1/users", tags=["Users"])


@router.get("/{vk_user_id}/profile")
async def get_user_profile(vk_user_id: str, session: Session = Depends(get_session)):
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
async def update_username(vk_user_id: str, new_username: str, session: Session = Depends(get_session)):
    """Изменить имя пользователя"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.username = new_username
    session.add(user)
    session.commit()
    return {"message": "Username updated"}


@router.get("/{user_id}/detailed-stats")
async def get_user_detailed_stats(user_id: int, session: Session = Depends(get_session)):
    """Детальная статистика пользователя"""
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    
    games = session.exec(select(SudokuGame).where(SudokuGame.user_id == user_id)).all()
    
    total = len(games)
    completed = sum(1 for g in games if g.is_completed)
    
    # Последние 10 игр
    last_10 = games[:10]
    last_10_wins = sum(1 for g in last_10 if g.is_completed)
    
    return {
        "user_id": user.id,
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        "total_games": total,
        "completed_games": completed,
        "win_rate_total": round(completed / total * 100, 1) if total > 0 else 0,
        "last_10_games": {
            "total": len(last_10),
            "wins": last_10_wins,
            "win_rate": round(last_10_wins / len(last_10) * 100, 1) if last_10 else 0
        }
    }