from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
import httpx
import json
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from app.db import get_session  # обнови импорт
from app.models import User, SudokuGame  # обнови импорт
from app.config import settings  # новый импорт
from ..services.adaptive_difficulty import AdaptiveDifficulty

import random

logger = logging.getLogger(__name__)
router = APIRouter()

# Теперь используем настройки
AI_SERVICE_URL = settings.AI_SERVICE_URL

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
    player_skill: Optional[str] = None,  # Опционально, система определит сама
    session: Session = Depends(get_session)
):
    """
    Создать новую игру Судоку.
    
    Логика:
    - Если игрок новый (0 игр) → skill = "beginner"
    - Если игрок сыграл <3 игр → skill = "beginner" (недостаточно данных)
    - Если игрок сыграл >=3 игр → рассчитываем skill на основе win_rate
    - Новичок может играть ЛЮБУЮ сложность (easy, medium, hard, expert)
    """
    user = await get_or_create_user(vk_user_id, session)
    
    # Получаем адаптированную сложность
    from ..services.adaptive_difficulty import AdaptiveDifficulty
    
    adaptation = await AdaptiveDifficulty.get_adaptive_difficulty(
        vk_user_id=vk_user_id,
        requested_difficulty=difficulty,
        session=session,
        client_skill=player_skill
    )
    
    adjusted_difficulty = adaptation["difficulty"]
    detected_skill = adaptation["skill_level"]
    
    logger.info(f"Game creation: user={vk_user_id}, "
                f"requested={difficulty}, "
                f"skill={detected_skill} (source: {adaptation['skill_source']}), "
                f"reason={adaptation['reason']}")
    
    # Запрашиваем у ИИ-сервиса (или мок)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            request_body = {
                "game_type": "sudoku",
                "difficulty": adjusted_difficulty,
                "player_skill": detected_skill,  # ← Всегда передаём скилл!
                "prompt": "",
                "custom_params": {}
            }
            
            logger.info(f"Sending to AI: {request_body}")
            
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json=request_body
            )
            response.raise_for_status()
            data = response.json()
            
            puzzle_grid = data["data"]["puzzle"]
            solution_grid = data["data"]["solution"]
            
    except Exception as e:
        logger.error(f"AI service error: {e}")
        raise HTTPException(status_code=503, detail=f"AI service unavailable: {str(e)}")
    
    # Сохраняем игру
    new_game = SudokuGame(
        user_id=user.id,
        puzzle=json.dumps(puzzle_grid),
        solution=json.dumps(solution_grid),
        difficulty=adjusted_difficulty,
        created_at=datetime.utcnow()
    )
    session.add(new_game)
    session.commit()
    session.refresh(new_game)
    
    # Возвращаем результат
    return {
        "game_id": new_game.id,
        "puzzle": puzzle_grid,
        "difficulty": adjusted_difficulty,
        "player_skill_used": detected_skill,  # ← Какой скилл был передан AI сервису
        "adaptation": {
            "requested": difficulty,
            "was_adjusted": adaptation["was_adjusted"],
            "detected_skill": detected_skill,
            "skill_source": adaptation["skill_source"],
            "confidence": adaptation["confidence"],
            "reason": adaptation["reason"],
            "skill_reason": adaptation.get("skill_reason"),
            "games_played": adaptation["games_played"],
            "win_rate": adaptation["win_rate"]
        }
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
    Получить подсказку (случайная пустая клетка)
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
        
        # 🔍 Собираем все пустые клетки (которые должен заполнить игрок)
        empty_cells = []
        for row in range(9):
            for col in range(9):
                # Клетка считается пустой, если:
                # 1. Это не изначальная клетка (puzzle[row][col] == 0)
                # 2. Игрок её ещё не заполнил (current_board[row][col] == 0)
                # 3. ИЛИ игрок заполнил неправильно (current_board[row][col] != solution[row][col])
                if puzzle[row][col] == 0:
                    if current_board[row][col] == 0:
                        # Совсем пустая клетка
                        empty_cells.append({
                            "row": row,
                            "col": col,
                            "current_value": 0,
                            "correct_value": solution[row][col],
                            "reason": "empty"
                        })
                    elif current_board[row][col] != solution[row][col]:
                        # Заполнено неправильно - тоже кандидат на подсказку
                        empty_cells.append({
                            "row": row,
                            "col": col,
                            "current_value": current_board[row][col],
                            "correct_value": solution[row][col],
                            "reason": "wrong"
                        })
        
        if not empty_cells:
            # Проверяем, может игра уже решена?
            is_complete = all(
                all(current_board[row][col] != 0 for col in range(9))
                for row in range(9)
            )
            if is_complete:
                return {
                    "success": False,
                    "message": "Все клетки заполнены! Проверьте решение через кнопку 'Проверить'.",
                    "hint_available": False
                }
            else:
                return {
                    "success": False,
                    "message": "Нет доступных клеток для подсказки",
                    "hint_available": False
                }
        
        # 🎲 Выбираем случайную клетку из пустых
        random_cell = random.choice(empty_cells)
        row = random_cell["row"]
        col = random_cell["col"]
        correct_value = random_cell["correct_value"]
        
        # Формируем сообщение в зависимости от ситуации
        if random_cell["reason"] == "wrong":
            message = f"Подсказка: в клетке [{row}, {col}] сейчас стоит {random_cell['current_value']}, но правильно должно быть {correct_value}"
        else:
            message = f"Подсказка: в клетку [{row}, {col}] нужно поставить цифру {correct_value}"
        
        # ✨ Опционально: можно сразу заполнить подсказку в current_board
        # (раскомментируй если хочешь автоматически ставить подсказку)
        # if random_cell["reason"] == "empty":
        #     current_board[row][col] = correct_value
        #     game.current_board = json.dumps(current_board)
        #     session.add(game)
        #     session.commit()
        #     message += " (Подсказка автоматически применена)"
        
        return {
            "success": True,
            "row": row,
            "col": col,
            "value": correct_value,
            "current_value": random_cell.get("current_value"),
            "was_wrong": random_cell["reason"] == "wrong",
            "message": message,
            "hint_available": True,
            "total_empty_cells": len(empty_cells)
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