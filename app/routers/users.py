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


@router.get("/{vk_user_id}/stats")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить статистику игрока (судоку + пазлы)"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        raise HTTPException(404, "User not found")
    
    # Получаем все игры пользователя
    sudoku_games = session.exec(
        select(SudokuGame).where(SudokuGame.user_id == user.id)
    ).all()
    
    puzzle_games = session.exec(
        select(PuzzleGame).where(PuzzleGame.user_id == user.id)
    ).all()
    
    # Объединяем все игры для общей статистики
    all_games = sudoku_games + puzzle_games
    completed = [g for g in all_games if g.is_completed]
    
    # Статистика по судоку (со сложностью)
    sudoku_completed = [g for g in sudoku_games if g.is_completed]
    
    return {
        # Общая статистика по всем играм
        "total_games": len(all_games),
        "completed_games": len(completed),
        "win_rate": len(completed)/len(all_games) if all_games else 0,
        "rating": user.rating,  # общий рейтинг пользователя
        
        # Статистика по типам игр
        "games_by_type": {
            "sudoku": {
                "total": len(sudoku_games),
                "completed": len(sudoku_completed)
            },
            "puzzle": {
                "total": len(puzzle_games),
                "completed": len([g for g in puzzle_games if g.is_completed])
            }
        },
        
        # Статистика по сложности (только для судоку, так как у пазлов другая логика)
        "sudoku_by_difficulty": {
            "easy": len([g for g in sudoku_games if g.difficulty == "easy"]),
            "medium": len([g for g in sudoku_games if g.difficulty == "medium"]),
            "hard": len([g for g in sudoku_games if g.difficulty == "hard"])
        },
        
        # Статистика по пазлам (по сложности, если есть)
        "puzzle_by_difficulty": {
            "easy": len([g for g in puzzle_games if g.difficulty == "easy"]),
            "medium": len([g for g in puzzle_games if g.difficulty == "medium"]),
            "hard": len([g for g in puzzle_games if g.difficulty == "hard"])
        }
    }