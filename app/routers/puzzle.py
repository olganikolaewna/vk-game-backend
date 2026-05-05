from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
import httpx
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from ..db import get_session
from ..models import User, PuzzleGame

AI_SERVICE_URL = "http://91.227.68.140:8000"
logger = logging.getLogger(__name__)
router = APIRouter()

# Модель для сохранения состояния пазла
class PuzzleStateRequest(BaseModel):
    pieces_state: List[Dict[str, Any]]  # Массив с позициями всех кусочков
    # Формат: [{"piece_id": 0, "x": 100, "y": 150, "placed": true}, ...]

async def get_or_create_user(vk_user_id: str, session: Session) -> User:
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        user = User(vk_user_id=vk_user_id, username=f"Player_{vk_user_id[:5]}")
        session.add(user)
        session.commit()
        session.refresh(user)
    return user

@router.post("/puzzle/new")
async def new_puzzle_game(
    vk_user_id: str,
    difficulty: str = "medium",
    session: Session = Depends(get_session)
):
    """Создать новую игру-пазл"""
    user = await get_or_create_user(vk_user_id, session)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json={
                    "game_type": "puzzle",
                    "difficulty": difficulty
                }
            )
            response.raise_for_status()
            data = response.json()
            
            # Извлекаем данные пазла
            puzzle_data = data["data"]
            
            # Сохраняем игру в БД
            new_game = PuzzleGame(
                user_id=user.id,
                content_id=data["content_id"],
                image_data=puzzle_data["image_url"],
                width=puzzle_data["width"],
                height=puzzle_data["height"],
                pieces_rows=puzzle_data.get("pieces_rows", 3),
                pieces_cols=puzzle_data.get("pieces_cols", 4),
                difficulty=difficulty,
                created_at=datetime.utcnow()
            )
            session.add(new_game)
            session.commit()
            session.refresh(new_game) 
            
            return {
                "game_id": new_game.id,
                "image_url": puzzle_data["image_url"],
                "width": puzzle_data["width"],
                "height": puzzle_data["height"],
                "pieces_rows": puzzle_data.get("pieces_rows", 3),
                "pieces_cols": puzzle_data.get("pieces_cols", 4),
                "difficulty": difficulty
            }
            
    except Exception as e:
        logger.error(f"Puzzle generation error: {e}")
        raise HTTPException(status_code=503, detail=f"Puzzle service unavailable: {str(e)}")

# ========== НОВЫЕ ЭНДПОИНТЫ ДЛЯ СОХРАНЕНИЯ ПРОГРЕССА ==========

@router.post("/puzzle/{game_id}/save-state")
async def save_puzzle_state(
    game_id: int,
    vk_user_id: str,
    state_data: PuzzleStateRequest,
    session: Session = Depends(get_session)
):
    """
    Сохранить текущее состояние пазла (прогресс игрока)
    Вызывается после каждого хода или при закрытии игры
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.is_completed:
        raise HTTPException(status_code=400, detail="Game already completed")
    
    # Сохраняем состояние в БД
    game.current_state = json.dumps(state_data.pieces_state)
    session.add(game)
    session.commit()
    
    logger.info(f"Saved puzzle state for game {game_id}, user {vk_user_id}")
    
    return {
        "success": True,
        "message": "Progress saved successfully",
        "saved_at": datetime.utcnow().isoformat()
    }

@router.get("/puzzle/{game_id}/load-state")
async def load_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Загрузить сохранённое состояние пазла
    Вызывается при открытии игры
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Загружаем сохранённое состояние
    saved_state = json.loads(game.current_state) if game.current_state else None
    
    return {
        "game_id": game.id,
        "has_saved_progress": saved_state is not None and len(saved_state) > 0,
        "image_url": game.image_data,
        "width": game.width,
        "height": game.height,
        "pieces_rows": game.pieces_rows,
        "pieces_cols": game.pieces_cols,
        "saved_state": saved_state,  # null или массив с позициями кусочков
        "is_completed": game.is_completed,
        "created_at": game.created_at
    }

@router.delete("/puzzle/{game_id}/clear-state")
async def clear_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Очистить сохранённое состояние (начать игру заново)
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Очищаем сохранённое состояние
    game.current_state = "[]"
    session.add(game)
    session.commit()
    
    return {
        "success": True,
        "message": "Saved progress cleared"
    }

# ========== КОНЕЦ НОВЫХ ЭНДПОИНТОВ ==========

@router.get("/puzzle/{game_id}")
async def get_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить состояние игры-пазл (базовая информация)"""
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    return {
        "game_id": game.id,
        "image_url": game.image_data,
        "width": game.width,
        "height": game.height,
        "pieces_rows": game.pieces_rows,
        "pieces_cols": game.pieces_cols,
        "is_completed": game.is_completed,
        "has_saved_progress": bool(game.current_state and game.current_state != "[]"),
        "created_at": game.created_at
    }

@router.post("/puzzle/{game_id}/complete")
async def complete_puzzle(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.is_completed:
        return {"success": True, "message": "Puzzle already completed"}
    
    # Загружаем сохранённое состояние пазла
    saved_state = json.loads(game.current_state) if game.current_state else []
    
    # Проверяем, что все кусочки на своих местах
    # В saved_state должна храниться информация о позициях каждого кусочка
    all_placed = True
    for piece in saved_state:
        if not piece.get("placed", False):
            all_placed = False
            break
    
    # Также можно проверить, что все кусочки собраны в правильном порядке
    # Для этого нужно хранить эталонное расположение
    
    if not all_placed:
        raise HTTPException(status_code=400, detail="Puzzle is not fully assembled")
    
    # Начисляем очки
    game.is_completed = True
    game.completed_at = datetime.utcnow()
    
    difficulty_scores = {"easy": 15, "medium": 30, "hard": 50}
    score = difficulty_scores.get(game.difficulty, 20)
    user.rating += score
    
    session.add(user)
    session.add(game)
    session.commit()
    
    return {
        "success": True,
        "score_earned": score,
        "message": f"Пазл решён! +{score} к рейтингу"
    }