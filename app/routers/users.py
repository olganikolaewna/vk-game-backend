# app/routers/users.py (новый файл)

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from ..db import get_session
from ..models import User, SudokuGame

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

@router.get("/{vk_user_id}/stats")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить статистику игрока"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    games = session.exec(
        select(SudokuGame).where(SudokuGame.user_id == user.id)
    ).all()
    
    completed = [g for g in games if g.is_completed]
    
    return {
        "total_games": len(games),
        "completed_games": len(completed),
        "win_rate": len(completed)/len(games) if games else 0,
        "best_score": max((g.score for g in completed), default=0),
        "games_by_difficulty": {
            "easy": len([g for g in games if g.difficulty == "easy"]),
            "medium": len([g for g in games if g.difficulty == "medium"]),
            "hard": len([g for g in games if g.difficulty == "hard"])
        }
    }