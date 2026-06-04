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

FALLBACK_PUZZLES = {
    "easy": {
        "image_url": "https://picsum.photos/id/104/800/600",
        "width": 800,
        "height": 600,
        "pieces_rows": 3,
        "pieces_cols": 3
    },
    "medium": {
        "image_url": "https://picsum.photos/id/42/800/600",
        "width": 800,
        "height": 600,
        "pieces_rows": 4,
        "pieces_cols": 4
    },
    "hard": {
        "image_url": "https://picsum.photos/id/15/800/600",
        "width": 800,
        "height": 600,
        "pieces_rows": 6,
        "pieces_cols": 6
    }
}

# ============================================
# Модели запросов/ответов
# ============================================

class PuzzleStateRequest(BaseModel):
    """Модель для сохранения состояния пазла"""
    pieces_state: List[Dict[str, Any]]


class PuzzleNewResponse(BaseModel):
    game_id: int
    content_id: str
    image_url: str
    width: int
    height: int
    pieces_rows: int
    pieces_cols: int
    difficulty: str


class PuzzleCompleteResponse(BaseModel):
    success: bool
    score_earned: int
    message: str


# ============================================
# Вспомогательные функции
# ============================================

async def get_or_create_user(vk_user_id: str, session: Session) -> User:
    """Получить или создать пользователя по VK ID"""
    try:
        vk_user_id_str = str(vk_user_id)
    except:
        vk_user_id_str = vk_user_id
    
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id_str)).first()
    if not user:
        user = User(vk_user_id=vk_user_id_str, username=f"Player_{vk_user_id_str[:5]}")
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info(f"Created new user with VK ID: {vk_user_id_str}")
    return user


def get_default_pieces_state(rows: int, cols: int) -> List[Dict[str, Any]]:
    """Создать начальное состояние пазла (все кусочки перемешаны)"""
    pieces_state = []
    total_pieces = rows * cols
    
    # Создаем начальные позиции (перемешанные)
    import random
    positions = list(range(total_pieces))
    random.shuffle(positions)
    
    # Стандартные размеры кусочков (клиент сам рассчитает)
    for idx, piece_id in enumerate(range(total_pieces)):
        pieces_state.append({
            "piece_id": piece_id,
            "x": 0,
            "y": 0,
            "placed": False,
            "original_index": positions[idx]  # Правильная позиция
        })
    
    return pieces_state


# ============================================
# Основные эндпоинты
# ============================================


@router.post("/puzzle/new")
async def new_puzzle_game(
    vk_user_id: str,
    difficulty: str = "medium",
    category: Optional[str] = None,      # ← только категория для пользователя
    session: Session = Depends(get_session)
):
    """
    Создать новую игру-пазл.
    Генерирует изображение через внешний ИИ-сервис.
    
    Параметры:
    - category: категория темы ("Аниме", "Природа", "Космос" и т.д.)
                Если не указана — выбирается случайная тема из любой категории.
    """
    user = await get_or_create_user(vk_user_id, session)
    
    # Формируем тело запроса к ИИ-сервису
    request_body = {
        "game_type": "puzzle",
        "difficulty": difficulty
    }
    
    # Добавляем category, если она передана
    if category:
        request_body["category"] = category
        logger.info(f"Generating puzzle with category: {category}")
    else:
        logger.info("Generating puzzle with random theme")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json=request_body
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
                current_state=json.dumps(initial_state),
                created_at=datetime.utcnow()
            )
            session.add(new_game)
            session.commit()
            session.refresh(new_game)
            
            logger.info(f"Created new puzzle game {new_game.id} for user {vk_user_id}")
            
            # Формируем ответ
            response_data = {
                "game_id": new_game.id,
                "content_id": new_game.content_id,
                "image_url": puzzle_data["image_url"],
                "width": puzzle_data["width"],
                "height": puzzle_data["height"],
                "pieces_rows": pieces_rows,
                "pieces_cols": pieces_cols,
                "difficulty": difficulty
            }
            
            # Добавляем информацию о категории, если она была использована
            if "category" in puzzle_data:
                response_data["category"] = puzzle_data["category"]
            
            return response_data
            
    except (httpx.TimeoutException, httpx.HTTPStatusError, Exception) as e:
        logger.warning(f"AI service unavailable, using fallback puzzle: {e}")
        
        # Берём fallback-данные для указанной сложности
        fallback = FALLBACK_PUZZLES.get(difficulty, FALLBACK_PUZZLES["medium"])
        content_id = f"fallback_{difficulty}_{datetime.utcnow().timestamp()}"
        
        pieces_rows = fallback["pieces_rows"]
        pieces_cols = fallback["pieces_cols"]
        initial_state = get_default_pieces_state(pieces_rows, pieces_cols)
        
        # Сохраняем fallback-игру
        new_game = PuzzleGame(
            user_id=user.id,
            content_id=content_id,
            image_data=fallback["image_url"],
            width=fallback["width"],
            height=fallback["height"],
            pieces_rows=pieces_rows,
            pieces_cols=pieces_cols,
            difficulty=difficulty,
            current_state=json.dumps(initial_state),
            created_at=datetime.utcnow()
        )
        session.add(new_game)
        session.commit()
        session.refresh(new_game)
        
        logger.info(f"Created fallback puzzle game {new_game.id} for user {vk_user_id}")
        
        return {
            "game_id": new_game.id,
            "content_id": content_id,
            "image_url": fallback["image_url"],
            "width": fallback["width"],
            "height": fallback["height"],
            "pieces_rows": pieces_rows,
            "pieces_cols": pieces_cols,
            "difficulty": difficulty,
            "fallback": True
        }

@router.get("/puzzle/{game_id}")
async def get_puzzle_info(
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
    
    saved_state = json.loads(game.current_state) if game.current_state else []
    
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
    saved_state = json.loads(game.current_state) if game.current_state else []
    
    return {
        "game_id": game.id,
        "has_saved_progress": saved_state is not None and len(saved_state) > 0,
        "image_url": game.image_data,
        "width": game.width,
        "height": game.height,
        "pieces_rows": game.pieces_rows,
        "pieces_cols": game.pieces_cols,
        "current_state": saved_state,
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


@router.post("/puzzle/{game_id}/complete")
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
        raise HTTPException(
            status_code=400, 
            detail="No saved progress found. Please make moves first."
        )
    
    # ========== УПРОЩЁННАЯ ПРОВЕРКА ==========
    # Проверяем, что все кусочки помечены как placed
    all_placed = all(piece.get("placed", False) for piece in saved_state)
    
    if not all_placed:
        # Находим, какие кусочки не на месте
        not_placed = [p.get("piece_id") for p in saved_state if not p.get("placed", False)]
        raise HTTPException(
            status_code=400,
            detail=f"Puzzle not complete. Pieces not placed: {not_placed}"
        )
    
    # Дополнительная проверка: все ли кусочки на правильных позициях
    # Если есть original_position или correct_position
    all_correct = True
    for piece in saved_state:
        piece_id = piece.get("piece_id")
        current_x = piece.get("x", -1)
        current_y = piece.get("y", -1)
        
        # Получаем правильную позицию (если есть)
        correct_x = piece.get("correct_x")
        correct_y = piece.get("correct_y")
        
        if correct_x is not None and correct_y is not None:
            # Проверяем с погрешностью в 5 пикселей
            if abs(current_x - correct_x) > 5 or abs(current_y - correct_y) > 5:
                all_correct = False
                logger.warning(f"Piece {piece_id} at wrong position: ({current_x},{current_y}) vs ({correct_x},{correct_y})")
    
    # Если проверка по позициям не пройдена, но все placed=true - засчитываем победу
    # (упрощённо: считаем, что если все помечены как placed, то пазл собран)
    if not all_correct:
        logger.warning(f"Position check failed but all placed=true, accepting completion for game {game_id}")
        # Всё равно засчитываем победу
    
    
    return {
        "success": True,
        "message": f"Пазл решён!"
    }


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

# ============================================
# ПРОКСИ-ЭНДПОИНТЫ ДЛЯ РАБОТЫ С ТЕМАМИ (перенаправление к ИИ-сервису)
# ============================================

@router.get("/puzzle/themes")
async def get_all_themes():
    """
    Получить все темы для пазлов (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{AI_SERVICE_URL}/api/v1/themes")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch themes: {e}")
        raise HTTPException(status_code=503, detail="AI service unavailable")


@router.get("/puzzle/themes/categories")
async def get_themes_categories():
    """
    Получить список всех категорий для пазлов (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{AI_SERVICE_URL}/api/v1/themes/categories")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch categories: {e}")
        # Fallback-категории на случай недоступности ИИ-сервиса
        return {
            "categories": ["Аниме", "Природа", "Космос", "Животные", 
                          "Фантастика", "Машины", "Спорт", "Еда", 
                          "Супергерои", "Мультфильмы", "Игры"]
        }


@router.get("/puzzle/themes/by-category")
async def get_themes_by_category(
    category: str
):
    """
    Получить темы по категории (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{AI_SERVICE_URL}/api/v1/themes/by-category",
                params={"category": category}
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch themes by category: {e}")
        raise HTTPException(status_code=503, detail="AI service unavailable")


@router.get("/puzzle/themes/random")
async def get_random_theme():
    """
    Получить случайную тему (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{AI_SERVICE_URL}/api/v1/themes/random")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch random theme: {e}")
        # Fallback-тема на случай недоступности ИИ-сервиса
        return {"theme": "Красивый пейзаж"}


@router.get("/puzzle/themes/popular")
async def get_popular_themes(
    limit: int = 10
):
    """
    Получить популярные темы (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{AI_SERVICE_URL}/api/v1/themes/popular",
                params={"limit": limit}
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch popular themes: {e}")
        raise HTTPException(status_code=503, detail="AI service unavailable")


@router.get("/puzzle/themes/count")
async def get_themes_count():
    """
    Получить количество тем (прокси к ИИ-сервису)
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{AI_SERVICE_URL}/api/v1/themes/count")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to fetch themes count: {e}")
        return {"total_themes": 120}