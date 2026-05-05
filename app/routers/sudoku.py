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

# ============================================
# Модели запросов/ответов
# ============================================

class SudokuSaveRequest(BaseModel):
    current_board: List[List[int]]


class BoardCheckRequest(BaseModel):
    current_board: List[List[int]]


# ============================================
# Вспомогательные функции
# ============================================

async def get_or_create_user(vk_user_id: str, session: Session) -> User:
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


# ============================================
# Основные эндпоинты
# ============================================

@router.post("/sudoku/new")
async def new_sudoku_game(
    vk_user_id: str,
    difficulty: str = "medium",
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            request_body = {
                "game_type": "sudoku",
                "difficulty": difficulty,
                "player_skill": "beginner",
                "prompt": "",
                "custom_params": {}
            }
            
            logger.info(f"Sending request to AI: {request_body}")
            
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json=request_body
            )
            response.raise_for_status()
            data = response.json()
            
            puzzle_grid = data["data"]["puzzle"]
            solution_grid = data["data"]["solution"]
            
            logger.info(f"Successfully generated sudoku with ID: {data.get('content_id')}")
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="AI service timeout")
    except Exception as e:
        logger.error(f"AI service error: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {str(e)}")
    
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
    
    return {
        "game_id": new_game.id,
        "puzzle": puzzle_grid,
        "difficulty": difficulty
    }


# ============================================
# ИСПРАВЛЕННЫЙ ЭНДПОИНТ MOVE (без проверки)
# ============================================

@router.post("/sudoku/{game_id}/move")
async def make_move(
    game_id: int,
    vk_user_id: str,
    row: int,
    col: int,
    value: int,
    session: Session = Depends(get_session)
):
    """
    Сделать ход в судоку.
    Сохраняет значение в current_board БЕЗ проверки правильности.
    Проверка правильности выполняется только в эндпоинте /check.
    """
    # Валидация входных данных
    if not (0 <= row <= 8 and 0 <= col <= 8):
        raise HTTPException(status_code=400, detail="Некорректные координаты (должны быть от 0 до 8)")
    
    if not (1 <= value <= 9):
        raise HTTPException(status_code=400, detail="Цифра должна быть от 1 до 9")
    
    # Проверяем пользователя и игру
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    if game.user_id != user.id:
        raise HTTPException(status_code=403, detail="Это не ваша игра")
    
    if game.is_completed:
        raise HTTPException(status_code=400, detail="Игра уже завершена")
    
    # Загружаем данные игры
    puzzle = json.loads(game.puzzle)
    current_board = json.loads(game.current_board) if game.current_board else json.loads(game.puzzle)
    
    # Проверка: можно ли менять эту клетку (изначально заданные клетки нельзя менять)
    if puzzle[row][col] != 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Клетка [{row}, {col}] была заполнена изначально, её нельзя менять"
        )
    
    # Сохраняем ход (даже если значение 0 — стирание)
    if value == 0:
        current_board[row][col] = 0
        message = f"Клетка [{row}, {col}] очищена"
    else:
        current_board[row][col] = value
        message = f"Цифра {value} поставлена в клетку [{row}, {col}]"
    
    # Обновляем доску
    game.current_board = json.dumps(current_board)
    session.add(game)
    session.commit()
    
    # Подсчитываем количество заполненных клеток
    cells_filled = sum(1 for row_board in current_board for cell in row_board if cell != 0)
    
    return {
        "success": True,
        "message": message,
        "row": row,
        "col": col,
        "value": value if value != 0 else None,
        "cleared": (value == 0),
        "cells_filled": cells_filled,
        "cells_total": 81,
        "is_completed": game.is_completed
    }


# ============================================
# ИСПРАВЛЕННЫЙ ЭНДПОИНТ HINT
# ============================================

@router.post("/sudoku/{game_id}/hint")
async def get_hint(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Получить подсказку (значение для первой пустой клетки)
    """
    try:
        # Получаем или создаём пользователя
        user = await get_or_create_user(vk_user_id, session)
        
        # Получаем игру
        game = session.get(SudokuGame, game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Игра не найдена")
        
        if game.user_id != user.id:
            raise HTTPException(status_code=403, detail="Это не ваша игра")
        
        if game.is_completed:
            return {
                "success": False,
                "message": "Игра уже завершена, подсказки не требуются",
                "hint_available": False
            }
        
        # Загружаем данные
        puzzle = json.loads(game.puzzle)
        solution = json.loads(game.solution)
        current_board = json.loads(game.current_board) if game.current_board else json.loads(game.puzzle)
        
        # Ищем первую пустую клетку (где в puzzle 0 и в current_board 0)
        for row in range(9):
            for col in range(9):
                if puzzle[row][col] == 0 and current_board[row][col] == 0:
                    return {
                        "success": True,
                        "row": row,
                        "col": col,
                        "value": solution[row][col],
                        "message": f"Подсказка: в клетку [{row}, {col}] нужно поставить цифру {solution[row][col]}",
                        "hint_available": True
                    }
        
        # Если пустых клеток нет
        return {
            "success": True,
            "message": "В этой игре нет пустых клеток! Возможно, игра уже решена?",
            "hint_available": False
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении подсказки: {str(e)}")


# ============================================
# ЭНДПОИНТ ДЛЯ ПРОВЕРКИ РЕШЕНИЯ (ТОЛЬКО ЗДЕСЬ ПРОВЕРЯЕМ)
# ============================================

@router.post("/sudoku/{game_id}/check")
async def check_solution(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Проверить решение судоку.
    Берет current_board из БД и сверяет с solution.
    ТОЛЬКО ЗДЕСЬ происходит проверка правильности!
    """
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Получаем решение и текущую доску из БД
    solution = json.loads(game.solution)
    
    # Если нет сохранённого прогресса, используем начальную доску (puzzle)
    if game.current_board:
        current_board = json.loads(game.current_board)
    else:
        current_board = json.loads(game.puzzle)
    
    # Проверяем, совпадает ли текущее состояние с решением
    is_correct = (current_board == solution)
    
    # Проверяем, все ли клетки заполнены
    is_complete = all(
        all(cell != 0 for cell in row)
        for row in current_board
    )
    
    # Если решение правильное и игра ещё не завершена
    if is_correct and not game.is_completed:
        game.is_completed = True
        game.completed_at = datetime.utcnow()
        
        # Начисляем очки в зависимости от сложности
        difficulty_scores = {"easy": 15, "medium": 30, "hard": 50}
        rating_earned = difficulty_scores.get(game.difficulty, 20)
        user.rating += rating_earned
        
        session.add(user)
        session.add(game)
        session.commit()
        
        logger.info(f"Sudoku {game_id} completed by user {vk_user_id}, earned {rating_earned} points")
        
        return {
            "is_correct": True,
            "is_complete": True,
            "message": f"🎉 Поздравляем! Судоку решена правильно! +{rating_earned} к рейтингу",
            "rating_earned": rating_earned
        }
    elif is_correct and game.is_completed:
        return {
            "is_correct": True,
            "is_complete": True,
            "message": "Эта судоку уже была решена ранее",
            "rating_earned": 0
        }
    else:
        # Находим первую ошибку для подсказки
        first_error = None
        if not is_correct:
            for row in range(9):
                for col in range(9):
                    current_val = current_board[row][col]
                    if current_val != 0 and current_val != solution[row][col]:
                        first_error = {
                            "row": row, 
                            "col": col, 
                            "current": current_val, 
                            "correct": solution[row][col]
                        }
                        break
                if first_error:
                    break
        
        # Подсчитываем количество правильных и неправильных клеток
        correct_cells = 0
        wrong_cells = 0
        empty_cells = 0
        
        for row in range(9):
            for col in range(9):
                if current_board[row][col] == 0:
                    empty_cells += 1
                elif current_board[row][col] == solution[row][col]:
                    correct_cells += 1
                else:
                    wrong_cells += 1
        
        return {
            "is_correct": False,
            "is_complete": is_complete,
            "message": "Решение неверное. Попробуйте ещё раз!",
            "rating_earned": 0,
            "first_error": first_error,
            "stats": {
                "correct_cells": correct_cells,
                "wrong_cells": wrong_cells,
                "empty_cells": empty_cells,
                "total_filled": correct_cells + wrong_cells,
                "total_cells": 81
            }
        }


# ============================================
# ОСТАЛЬНЫЕ ЭНДПОИНТЫ (без изменений, но с обработкой ошибок)
# ============================================

@router.post("/sudoku/{game_id}/save-state")
async def save_sudoku_state(
    game_id: int,
    vk_user_id: str,
    save_data: SudokuSaveRequest,
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    if game.is_completed:
        raise HTTPException(status_code=400, detail="Game already completed")
    
    game.current_board = json.dumps(save_data.current_board)
    session.add(game)
    session.commit()
    
    logger.info(f"Saved sudoku state for game {game_id}, user {vk_user_id}")
    
    return {
        "success": True,
        "message": "Progress saved successfully",
        "saved_at": datetime.utcnow().isoformat()
    }


@router.get("/sudoku/{game_id}/load-state")
async def load_sudoku_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    saved_board = json.loads(game.current_board) if game.current_board else None
    puzzle = json.loads(game.puzzle)
    solution = json.loads(game.solution)
    
    return {
        "game_id": game.id,
        "has_saved_progress": saved_board is not None,
        "puzzle": puzzle,
        "solution": solution,
        "current_board": saved_board,
        "is_completed": game.is_completed,
        "difficulty": game.difficulty,
        "created_at": game.created_at
    }


@router.delete("/sudoku/{game_id}/clear-state")
async def clear_sudoku_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game.current_board = None
    session.add(game)
    session.commit()
    
    return {
        "success": True,
        "message": "Saved progress cleared"
    }


@router.get("/sudoku/{game_id}/state")
async def get_game_state(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game or game.user_id != user.id:
        raise HTTPException(status_code=404, detail="Game not found")
    
    saved_board = json.loads(game.current_board) if game.current_board else None
    puzzle = json.loads(game.puzzle)
    
    return {
        "game_id": game.id,
        "puzzle": puzzle,
        "current_board": saved_board or puzzle,
        "is_completed": game.is_completed,
        "difficulty": game.difficulty
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
    """
    Проверить, правильная ли цифра (без сохранения).
    Это отдельный эндпоинт для проверки конкретной клетки.
    """
    if not (0 <= row <= 8 and 0 <= col <= 8):
        raise HTTPException(status_code=400, detail="Некорректные координаты (должны быть от 0 до 8)")
    
    if not (1 <= value <= 9):
        raise HTTPException(status_code=400, detail="Цифра должна быть от 1 до 9")
    
    user = await get_or_create_user(vk_user_id, session)
    game = session.get(SudokuGame, game_id)
    
    if not game:
        raise HTTPException(status_code=404, detail="Игра не найдена")
    
    if game.user_id != user.id:
        raise HTTPException(status_code=403, detail="Это не ваша игра")
    
    puzzle = json.loads(game.puzzle)
    if puzzle[row][col] != 0:
        return {
            "valid": False,
            "message": "Эта клетка была заполнена изначально, её нельзя менять"
        }
    
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