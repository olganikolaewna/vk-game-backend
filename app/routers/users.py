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



# В файле users.py, исправьте эндпоинт:

@router.get("/api/v1/users/{user_id}/stats")
async def get_user_stats(
    user_id: int,
    recent_games_limit: int = 0,  # Изменяем: 0 = все игры, >0 = только последние N
    session: Session = Depends(get_session)
):
    """
    Получить статистику пользователя
    
    Args:
        user_id: ID пользователя
        recent_games_limit: 
            0 - все игры (полная статистика)
            >0 - только последние N игр
    """
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Получаем все игры пользователя
    sudoku_games = session.exec(
        select(SudokuGame)
        .where(SudokuGame.user_id == user_id)
        .order_by(SudokuGame.created_at.desc())
    ).all()
    
    puzzle_games = session.exec(
        select(PuzzleGame)
        .where(PuzzleGame.user_id == user_id)
        .order_by(PuzzleGame.created_at.desc())
    ).all()
    
    all_games = sudoku_games + puzzle_games
    all_games.sort(key=lambda g: g.created_at, reverse=True)
    
    total_all_games = len(all_games)  # Полное количество игр
    
    # Если нужно ограничить количество игр для расчёта
    if recent_games_limit > 0:
        games_for_stats = all_games[:recent_games_limit]
    else:
        games_for_stats = all_games  # Берём все игры
    
    total_games = len(games_for_stats)
    completed_games = sum(1 for g in games_for_stats if g.is_completed)
    win_rate = completed_games / total_games if total_games > 0 else 0
    
    # Статистика по сложности (только для выбранных игр)
    sudoku_by_difficulty = {}
    puzzle_by_difficulty = {}
    
    for game in games_for_stats:
        if isinstance(game, SudokuGame):
            diff = game.difficulty
            sudoku_by_difficulty[diff] = sudoku_by_difficulty.get(diff, 0) + 1
        else:  # PuzzleGame
            diff = game.difficulty
            puzzle_by_difficulty[diff] = puzzle_by_difficulty.get(diff, 0) + 1
    
    # Отдельная статистика по Sudoku и Puzzle для выбранных игр
    sudoku_in_stats = [g for g in games_for_stats if isinstance(g, SudokuGame)]
    puzzle_in_stats = [g for g in games_for_stats if isinstance(g, PuzzleGame)]
    
    return {
        "user_id": user.id,
        "vk_user_id": user.vk_user_id,
        "username": user.username,
        "rating": user.rating,
        
        # ПОЛНАЯ статистика (все игры пользователя)
        "total_games_all_time": total_all_games,
        "completed_games_all_time": sum(1 for g in all_games if g.is_completed),
        
        # Статистика по выбранному периоду/лимиту
        "stats_period": {
            "games_analyzed": total_games,
            "completed_analyzed": completed_games,
            "win_rate": round(win_rate, 2),
            "limit_type": "all_games" if recent_games_limit == 0 else f"last_{recent_games_limit}_games"
        },
        
        "games_by_type": {
            "sudoku": {
                "total": len(sudoku_in_stats),
                "completed": sum(1 for g in sudoku_in_stats if g.is_completed),
                "by_difficulty": sudoku_by_difficulty
            },
            "puzzle": {
                "total": len(puzzle_in_stats),
                "completed": sum(1 for g in puzzle_in_stats if g.is_completed),
                "by_difficulty": puzzle_by_difficulty
            }
        },
        
        "stats_by_period": {  # Разная статистика для разных периодов
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

def _get_stats_for_last_n_games(games: List, n: int) -> Dict:
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