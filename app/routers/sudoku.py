from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
import httpx
import json
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from ..db import get_session
from ..models import User, SudokuGame

# URL нового ИИ-сервиса
AI_SERVICE_URL = "http://91.227.68.140:8000"

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Вспомогательная функция для получения/создания пользователя ---
async def get_or_create_user(vk_user_id: str, session: Session) -> User:
    user = session.exec(select(User).where(User.vk_user_id == vk_user_id)).first()
    if not user:
        user = User(vk_user_id=vk_user_id, username=f"Player_{vk_user_id[:5]}")
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info(f"Created new user with VK ID: {vk_user_id}")
    return user

# --- Эндпоинт для создания новой игры ---
@router.post("/sudoku/new")
async def new_sudoku_game(
    vk_user_id: str,
    difficulty: str = "medium",
    session: Session = Depends(get_session)
):
    # 1. Получаем или создаём пользователя
    user = await get_or_create_user(vk_user_id, session)
    
    # 2. Запрашиваем новую головоломку у ИИ-сервиса
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # ПРАВИЛЬНЫЙ ФОРМАТ ЗАПРОСА (как в примере curl)
            request_body = {
                "game_type": "sudoku",
                "difficulty": difficulty,
                "player_skill": "beginner",  # или "intermediate", "expert"
                "prompt": "",  # можно оставить пустым
                "custom_params": {}
            }
            
            logger.info(f"Sending request to AI: {request_body}")
            
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json=request_body
            )
            response.raise_for_status()
            data = response.json()
            
            # Извлекаем puzzle и solution из правильной структуры ответа
            # Ответ приходит в формате: {"content_id": "...", "game_type": "...", "data": {"puzzle": [...], "solution": [...]}}
            puzzle_grid = data["data"]["puzzle"]
            solution_grid = data["data"]["solution"]
            
            logger.info(f"Successfully generated sudoku with ID: {data.get('content_id')}")
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI service timeout")
    except Exception as e:
        logger.error(f"AI service error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {str(e)}")
    
    # 3. Сохраняем игру в БД
    new_game = SudokuGame(
        user_id=user.id,
        puzzle=json.dumps(puzzle_grid),
        solution=json.dumps(solution_grid),
        difficulty=difficulty,
        created_at=datetime.utcnow()
    )
    session.add(new_game)
    session.commit()
    session.refresh(new_game)
    
    # 4. Возвращаем клиенту только задачу (не решение!)
    return {
        "game_id": new_game.id,
        "puzzle": puzzle_grid,
        "difficulty": difficulty
    }

class BoardCheckRequest(BaseModel):
    current_board: List[List[int]]

# --- Эндпоинт для проверки решения ---
@router.post("/sudoku/{game_id}/check")
async def check_solution(
    game_id: int,
    vk_user_id: str,
    check_data: BoardCheckRequest,  # Получаем текущую сетку из тела запроса
    session: Session = Depends(get_session)
):
    # 1. Проверяем пользователя и игру
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # 2. Получаем сохранённое решение
    solution = json.loads(game.solution)
    current_board = check_data.current_board
    
    # 3. Проверяем, совпадает ли текущее состояние с решением
    is_correct = (current_board == solution)
    
    # 4. Дополнительная проверка на полноту (все клетки заполнены)
    is_complete = all(
        all(cell != 0 for cell in row)
        for row in current_board
    )
    
    # 5. Если решение правильное и игра ещё не завершена
    if is_correct and not game.is_completed:
        game.is_completed = True
        game.completed_at = datetime.utcnow()
        
        # Обновляем рейтинг пользователя (например, +10 очков)
        user.rating += 10
        session.add(user)
        session.add(game)
        session.commit()
        
        return {
            "is_correct": True,
            "is_complete": True,
            "message": "Поздравляем! Судоку решена правильно! +10 к рейтингу",
            "rating_earned": 10
        }
    elif is_correct and game.is_completed:
        return {
            "is_correct": True,
            "is_complete": True,
            "message": "Эта судоку уже была решена ранее",
            "rating_earned": 0
        }
    else:
        # Проверяем, есть ли ошибки
        return {
            "is_correct": False,
            "is_complete": is_complete,
            "message": "Решение неверное. Попробуйте ещё раз!",
            "rating_earned": 0
        }

# --- Эндпоинт для получения "судоку дня" ---
@router.get("/sudoku/daily")
async def get_daily_sudoku():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{AI_SERVICE_URL}/api/v1/generate/sudoku/daily")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Daily sudoku error: {e}")
        raise HTTPException(status_code=503, detail="Daily sudoku unavailable")

# --- Эндпоинт для проверки статуса игры (необязательно) ---
@router.get("/sudoku/{game_id}")
async def get_game_status(game_id: int, session: Session = Depends(get_session)):
    game = session.get(SudokuGame, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    return {
        "game_id": game.id,
        "is_completed": game.is_completed,
        "difficulty": game.difficulty,
        "created_at": game.created_at
    }

# app/routers/sudoku.py (добавить)

@router.post("/sudoku/{game_id}/move")
async def make_move(
    game_id: int,
    vk_user_id: str,
    row: int,
    col: int,
    value: int,
    session: Session = Depends(get_session)
):
    """Сделать ход (поставить цифру в клетку)"""
    # Проверяем пользователя и игру
    # Обновляем текущее состояние
    # Возвращаем обновлённую сетку
    pass

@router.get("/sudoku/{game_id}/state")
async def get_game_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить текущее состояние игры"""
    # Для восстановления игры после перезагрузки
    pass

@router.post("/sudoku/{game_id}/hint")
async def get_hint(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Получить подсказку (одну клетку)"""
    # 1. Получаем пользователя (создаём если нет)
    user = await get_or_create_user(vk_user_id, session)
    
    # 2. Получаем игру
    game = session.get(SudokuGame, game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    # Проверяем, что игра принадлежит этому пользователю
    if game.user_id != user.id:
        raise HTTPException(status_code=403, detail="Это не ваша игра")
    
    # 3. Преобразуем JSON-строки в массивы
    import json
    puzzle = json.loads(game.puzzle)  # исходная задача (с пустыми клетками)
    solution = json.loads(game.solution)  # полное решение
    
    # 4. Ищем первую пустую клетку (где в puzzle 0)
    for row in range(9):
        for col in range(9):
            if puzzle[row][col] == 0:  # пустая клетка
                # Возвращаем подсказку
                return {
                    "row": row,
                    "col": col,
                    "value": solution[row][col],
                    "message": f"В клетке [{row+1}, {col+1}] должна быть цифра {solution[row][col]}"
                }
    
    # 5. Если пустых клеток нет
    return {
        "message": "В этой игре нет пустых клеток!",
        "hint_available": False
    }


@router.post("/sudoku/{game_id}/validate")
async def validate_move(
    game_id: int,
    vk_user_id: str,
    row: int,
    col: int,
    value: int,
    session: Session = Depends(get_session)
):
    """Проверить, правильная ли цифра (без сохранения)"""
    # 1. Проверяем входные данные
    if not (0 <= row <= 8 and 0 <= col <= 8):
        raise HTTPException(status_code=400, detail="Некорректные координаты (должны быть от 0 до 8)")
    
    if not (1 <= value <= 9):
        raise HTTPException(status_code=400, detail="Цифра должна быть от 1 до 9")
    
    # 2. Получаем пользователя и игру
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    if game.user_id != user.id:
        raise HTTPException(status_code=403, detail="Это не ваша игра")
    
    # 3. Проверяем, что клетка была пустой в исходной задаче
    import json
    puzzle = json.loads(game.puzzle)
    if puzzle[row][col] != 0:
        return {
            "valid": False,
            "message": "Эта клетка была заполнена изначально, её нельзя менять"
        }
    
    # 4. Сравниваем с решением
    solution = json.loads(game.solution)
    is_valid = (solution[row][col] == value)
    
    return {
        "valid": is_valid,
        "row": row,
        "col": col,
        "value": value,
        "correct_value": solution[row][col] if not is_valid else None,
        "message": "✓ Правильно!" if is_valid else f"✗ Неправильно. Правильная цифра: {solution[row][col]}"
    }