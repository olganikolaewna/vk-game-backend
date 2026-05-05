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

# ============================================
# Модели запросов/ответов
# ============================================

class PuzzleStateRequest(BaseModel):
    """Модель для сохранения состояния пазла"""
    pieces_state: List[Dict[str, Any]]
    # Формат: [{"piece_id": 0, "x": 100, "y": 150, "placed": true}, ...]


class PuzzleStateResponse(BaseModel):
    """Модель ответа с состоянием пазла"""
    game_id: int
    has_saved_progress: bool
    image_url: str
    width: int
    height: int
    pieces_rows: int
    pieces_cols: int
    current_state: Optional[List[Dict[str, Any]]] = None
    is_completed: bool
    created_at: datetime


class PuzzleNewResponse(BaseModel):
    """Модель ответа при создании новой игры"""
    game_id: int
    content_id: str
    image_url: str
    width: int
    height: int
    pieces_rows: int
    pieces_cols: int
    difficulty: str


class PuzzleCompleteResponse(BaseModel):
    """Модель ответа при завершении игры"""
    success: bool
    score_earned: int
    message: str


class SaveStateResponse(BaseModel):
    """Модель ответа при сохранении состояния"""
    success: bool
    message: str
    saved_at: str


class ClearStateResponse(BaseModel):
    """Модель ответа при очистке состояния"""
    success: bool
    message: str


# ============================================
# Вспомогательные функции
# ============================================

async def get_or_create_user(vk_user_id: str, session: Session) -> User:
    """Получить или создать пользователя по VK ID"""
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        user = User(vk_user_id=vk_user_id, username=f"Player_{vk_user_id[:5]}")
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


def get_default_pieces_state(rows: int, cols: int) -> List[Dict[str, Any]]:
    """Создать начальное состояние пазла (все кусочки перемешаны)"""
    pieces_state = []
    total_pieces = rows * cols
    
    # Создаем начальные позиции (перемешанные)
    import random
    positions = list(range(total_pieces))
    random.shuffle(positions)
    
    for piece_id in range(total_pieces):
        pieces_state.append({
            "piece_id": piece_id,
            "x": 0,  # начальная позиция (будет установлена клиентом)
            "y": 0,
            "placed": False,
            "original_position": positions[piece_id]
        })
    
    return pieces_state


# ============================================
# Основные эндпоинты
# ============================================

@router.post("/puzzle/new", response_model=PuzzleNewResponse)
async def new_puzzle_game(
    vk_user_id: str,
    difficulty: str = "medium",
    session: Session = Depends(get_session)
):
    """
    Создать новую игру-пазл.
    Генерирует изображение через внешний ИИ-сервис.
    """
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
            
            # Создаём начальное состояние пазла
            pieces_rows = puzzle_data.get("pieces_rows", 3)
            pieces_cols = puzzle_data.get("pieces_cols", 4)
            initial_state = get_default_pieces_state(pieces_rows, pieces_cols)
            
            # Сохраняем игру в БД
            new_game = PuzzleGame(
                user_id=user.id,
                content_id=data["content_id"],
                image_data=puzzle_data["image_url"],
                width=puzzle_data["width"],
                height=puzzle_data["height"],
                pieces_rows=pieces_rows,
                pieces_cols=pieces_cols,
                difficulty=difficulty,
                current_state=json.dumps(initial_state),  # ← Инициализируем
                created_at=datetime.utcnow()
            )
            session.add(new_game)
            session.commit()
            session.refresh(new_game)
            
            logger.info(f"Created new puzzle game {new_game.id} for user {vk_user_id}")
            
            return {
                "game_id": new_game.id,
                "content_id": new_game.content_id,
                "image_url": puzzle_data["image_url"],
                "width": puzzle_data["width"],
                "height": puzzle_data["height"],
                "pieces_rows": pieces_rows,
                "pieces_cols": pieces_cols,
                "difficulty": difficulty
            }
            
    except httpx.TimeoutException:
        logger.error("Puzzle generation timeout")
        raise HTTPException(status_code=504, detail="AI service timeout")
    except httpx.HTTPStatusError as e:
        logger.error(f"AI service error: {e}")
        raise HTTPException(status_code=503, detail=f"AI service error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Puzzle generation error: {e}")
        raise HTTPException(status_code=503, detail=f"Puzzle service unavailable: {str(e)}")


@router.get("/puzzle/{game_id}")
async def get_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Получить базовую информацию об игре-пазл
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    saved_state = json.loads(game.current_state) if game.current_state else None
    
    return {
        "game_id": game.id,
        "image_url": game.image_data,
        "width": game.width,
        "height": game.height,
        "pieces_rows": game.pieces_rows,
        "pieces_cols": game.pieces_cols,
        "is_completed": game.is_completed,
        "has_saved_progress": saved_state is not None and len(saved_state) > 0,
        "created_at": game.created_at
    }


@router.get("/puzzle/{game_id}/load-state")
async def load_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Загрузить сохранённое состояние пазла.
    Вызывается при открытии игры для восстановления прогресса.
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
        "current_state": saved_state,  # ← переименовано с saved_state
        "is_completed": game.is_completed,
        "created_at": game.created_at
    }


@router.post("/puzzle/{game_id}/save-state")
async def save_puzzle_state(
    game_id: int,
    vk_user_id: str,
    state_data: PuzzleStateRequest,
    session: Session = Depends(get_session)
):
    """
    Сохранить текущее состояние пазла.
    Вызывается после каждого хода или при закрытии игры.
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


@router.delete("/puzzle/{game_id}/clear-state")
async def clear_puzzle_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Очистить сохранённое состояние.
    Позволяет начать игру заново с перемешанными кусочками.
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.is_completed:
        raise HTTPException(status_code=400, detail="Game already completed")
    
    # Создаём новое перемешанное состояние
    new_state = get_default_pieces_state(game.pieces_rows, game.pieces_cols)
    game.current_state = json.dumps(new_state)
    session.add(game)
    session.commit()
    
    logger.info(f"Cleared puzzle state for game {game_id}, user {vk_user_id}")
    
    return {
        "success": True,
        "message": "Progress cleared, new shuffled state created"
    }


@router.post("/puzzle/{game_id}/complete", response_model=PuzzleCompleteResponse)
async def complete_puzzle(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Завершить пазл.
    Проверяет, что все кусочки на правильных местах,
    и начисляет очки пользователю.
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(PuzzleGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.is_completed:
        return {
            "success": True,
            "score_earned": 0,
            "message": "Puzzle already completed"
        }
    
    # Загружаем сохранённое состояние
    saved_state = json.loads(game.current_state) if game.current_state else []
    
    if not saved_state:
        raise HTTPException(status_code=400, detail="No saved progress found")
    
    # Проверяем, что все кусочки на правильных местах
    all_correct = True
    pieces_per_row = game.pieces_cols
    piece_width = game.width // game.pieces_cols
    piece_height = game.height // game.pieces_rows
    
    for piece in saved_state:
        piece_id = piece.get("piece_id")
        current_x = piece.get("x", -1)
        current_y = piece.get("y", -1)
        placed = piece.get("placed", False)
        
        # Вычисляем правильные координаты для этого кусочка
        correct_x = (piece_id % pieces_per_row) * piece_width
        correct_y = (piece_id // pieces_per_row) * piece_height
        
        # Проверяем, что кусочек на месте и помечен как placed
        if not placed or abs(current_x - correct_x) > 5 or abs(current_y - correct_y) > 5:
            all_correct = False
            break
    
    if not all_correct:
        raise HTTPException(status_code=400, detail="Pieces are not in correct positions")
    
    # Начисляем очки
    game.is_completed = True
    game.completed_at = datetime.utcnow()
    
    difficulty_scores = {"easy": 15, "medium": 30, "hard": 50}
    score = difficulty_scores.get(game.difficulty, 20)
    user.rating += score
    
    session.add(user)
    session.add(game)
    session.commit()
    
    logger.info(f"Puzzle {game_id} completed by user {vk_user_id}, earned {score} points")
    
    return {
        "success": True,
        "score_earned": score,
        "message": f"Пазл решён! +{score} к рейтингу"
    }


# ============================================
# Дополнительный эндпоинт для получения всех игр пользователя
# ============================================

@router.get("/puzzle/user/games")
async def get_user_puzzle_games(
    vk_user_id: str,
    limit: int = 10,
    include_completed: bool = True,
    session: Session = Depends(get_session)
):
    """
    Получить список всех игр-пазлов пользователя.
    """
    user = await get_or_create_user(vk_user_id, session)
    
    query = select(PuzzleGame).where(PuzzleGame.user_id == user.id)
    
    if not include_completed:
        query = query.where(PuzzleGame.is_completed == False)
    
    query = query.order_by(PuzzleGame.created_at.desc()).limit(limit)
    
    games = session.exec(query).all()
    
    return {
        "user_id": user.id,
        "vk_user_id": vk_user_id,
        "total_games": len(games),
        "games": [
            {
                "game_id": game.id,
                "created_at": game.created_at,
                "is_completed": game.is_completed,
                "completed_at": game.completed_at,
                "difficulty": game.difficulty,
                "has_progress": game.current_state is not None and game.current_state != "[]"
            }
            for game in games
        ]
    }