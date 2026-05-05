# app/routers/stats.py
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func
from datetime import datetime, date
from typing import Optional

from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

router = APIRouter(prefix="/api/v1", tags=["Stats & Leaderboard"])

# ========== СТАТИСТИКА ПЛАТФОРМЫ ==========

@router.get("/stats/global")
async def get_global_stats(session: Session = Depends(get_session)):
    """Общая статистика платформы"""
    total_users = session.exec(select(func.count(User.id))).one()
    
    total_sudoku = session.exec(select(func.count(SudokuGame.id))).one()
    total_puzzles = session.exec(select(func.count(PuzzleGame.id))).one()
    
    today_start = datetime.combine(date.today(), datetime.min.time())
    
    sudoku_today = session.exec(
        select(func.count(SudokuGame.id)).where(SudokuGame.created_at >= today_start)
    ).one()
    puzzles_today = session.exec(
        select(func.count(PuzzleGame.id)).where(PuzzleGame.created_at >= today_start)
    ).one()
    
    return {
        "total_users": total_users,
        "total_games": total_sudoku + total_puzzles,
        "games_by_type": {
            "sudoku": total_sudoku,
            "puzzle": total_puzzles
        },
        "games_today": sudoku_today + puzzles_today,
    }


@router.get("/stats/user/{vk_user_id}")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Статистика конкретного игрока"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        return {"error": "User not found"}
    
    sudoku_games = session.exec(
        select(SudokuGame).where(SudokuGame.user_id == user.id)
    ).all()
    puzzle_games = session.exec(
        select(PuzzleGame).where(PuzzleGame.user_id == user.id)
    ).all()
    
    all_games = sudoku_games + puzzle_games
    completed = [g for g in all_games if g.is_completed]
    
    return {
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        "total_games": len(all_games),
        "completed_games": len(completed),
        "win_rate": len(completed)/len(all_games) if all_games else 0,
        "games_by_type": {
            "sudoku": len(sudoku_games),
            "puzzle": len(puzzle_games)
        }
    }


# ========== ТАБЛИЦА ЛИДЕРОВ ==========

@router.get("/leaderboard")
async def get_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session)
):
    """Топ игроков по рейтингу"""
    users = session.exec(
        select(User).order_by(User.rating.desc()).limit(limit)
    ).all()
    
    result = []
    for user in users:
        total_games = 0
        total_games += session.exec(select(func.count(SudokuGame.id)).where(SudokuGame.user_id == user.id)).one()
        total_games += session.exec(select(func.count(PuzzleGame.id)).where(PuzzleGame.user_id == user.id)).one()
        
        result.append({
            "rank": len(result) + 1,
            "vk_user_id": user.vk_user_id,
            "username": user.username,
            "rating": user.rating,
            "games_played": total_games
        })
    
    return result


@router.get("/leaderboard/daily")
async def get_daily_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session)
):
    """Топ игроков за сегодня по количеству побед"""
    today_start = datetime.combine(date.today(), datetime.min.time())
    users = session.exec(select(User)).all()
    
    results = []
    for user in users:
        completed_today = 0
        completed_today += session.exec(
            select(func.count(SudokuGame.id))
            .where(SudokuGame.user_id == user.id, SudokuGame.is_completed == True, SudokuGame.completed_at >= today_start)
        ).one()
        completed_today += session.exec(
            select(func.count(PuzzleGame.id))
            .where(PuzzleGame.user_id == user.id, PuzzleGame.is_completed == True, PuzzleGame.completed_at >= today_start)
        ).one()
        
        if completed_today > 0:
            results.append({
                "vk_user_id": user.vk_user_id,
                "username": user.username,
                "wins_today": completed_today
            })
    
    results.sort(key=lambda x: x["wins_today"], reverse=True)
    return results[:limit]