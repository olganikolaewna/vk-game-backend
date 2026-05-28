from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select
import httpx
import json
import logging
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

from app.db import get_session
from app.models import User, SudokuGame
from app.config import settings
from app.services.adaptive_difficulty import AdaptiveDifficulty  # ← Абсолютный импорт

import random

logger = logging.getLogger(__name__)
router = APIRouter()

# Теперь используем настройки
AI_SERVICE_URL = settings.AI_SERVICE_URL

FALLBACK_SUDOKU = {
    "puzzle": [
        [5, 3, 0, 0, 7, 0, 0, 0, 0],
        [6, 0, 0, 1, 9, 5, 0, 0, 0],
        [0, 9, 8, 0, 0, 0, 0, 6, 0],
        [8, 0, 0, 0, 6, 0, 0, 0, 3],
        [4, 0, 0, 8, 0, 3, 0, 0, 1],
        [7, 0, 0, 0, 2, 0, 0, 0, 6],
        [0, 6, 0, 0, 0, 0, 2, 8, 0],
        [0, 0, 0, 4, 1, 9, 0, 0, 5],
        [0, 0, 0, 0, 8, 0, 0, 7, 9]
    ],
    "solution": [
        [5, 3, 4, 6, 7, 8, 9, 1, 2],
        [6, 7, 2, 1, 9, 5, 3, 4, 8],
        [1, 9, 8, 3, 4, 2, 5, 6, 7],
        [8, 5, 9, 7, 6, 1, 4, 2, 3],
        [4, 2, 6, 8, 5, 3, 7, 9, 1],
        [7, 1, 3, 9, 2, 4, 8, 5, 6],
        [9, 6, 1, 5, 3, 7, 2, 8, 4],
        [2, 8, 7, 4, 1, 9, 6, 3, 5],
        [3, 4, 5, 2, 8, 6, 1, 7, 9]
    ]
}

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
        user = User(
            vk_user_id=vk_user_id_str, 
            username=f"Player_{vk_user_id_str[:5]}",
            skill_level="beginner"  # ← Явно устанавливаем начальный уровень
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info(f"Created new user with VK ID: {vk_user_id_str} (skill: beginner)")
    return user


# ============================================
# Основные эндпоинты
# ============================================

@router.post("/sudoku/new")
async def new_sudoku_game(
    vk_user_id: str,
    difficulty: str = "medium",
    player_skill: Optional[str] = None,
    session: Session = Depends(get_session)
):
    """
    Создать новую игру Судоку

    """
    user = await get_or_create_user(vk_user_id, session)
    
    # Получаем адаптированную сложность (считаем по последним 20 играм)

    player_skill = user.skill_level
    logger.info(f"player_skill not provided, using from DB: {player_skill}")
    
    adaptation = await AdaptiveDifficulty.get_adaptive_difficulty(
        vk_user_id=vk_user_id,
        requested_difficulty=difficulty,
        session=session,
        client_skill=player_skill,
        auto_adjust=True,           
        recent_games_limit=20       
    )
    
    requested_for_ai = adaptation["difficulty"]
    skill_level = adaptation["skill_level"]
    allowed_difficulties = adaptation.get("allowed_difficulties", ["easy"])
    was_adjusted = adaptation["was_adjusted"]


    # Запрашиваем у ИИ-сервиса
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            request_body = {
                "game_type": "sudoku",
                "difficulty": requested_for_ai,
                "player_skill": skill_level,
                "prompt": "",
                "custom_params": {}
            }
            
            logger.info(f"📤 Full request body: {request_body}")
    
            response = await client.post(
                f"{AI_SERVICE_URL}/api/v1/generate",
                json=request_body
            )
            response.raise_for_status()
            data = response.json()
            logger.info(f"📥 AI response difficulty: {data.get('difficulty')}")
            
            puzzle_grid = data["data"]["puzzle"]
            solution_grid = data["data"]["solution"]

            actual_difficulty = data.get("difficulty", requested_for_ai)
            
    except Exception as e:
        logger.error(f"AI service error, using fallback: {e}")
    
        puzzle_grid = FALLBACK_SUDOKU["puzzle"]
        solution_grid = FALLBACK_SUDOKU["solution"]
    
    # Сохраняем игру
    new_game = SudokuGame(
        user_id=user.id,
        puzzle=json.dumps(puzzle_grid),
        solution=json.dumps(solution_grid),
        difficulty=actual_difficulty,
        created_at=datetime.utcnow()
    )
    session.add(new_game)
    session.commit()
    session.refresh(new_game)
    
    # Получаем информацию о прогрессе для следующего уровня
    promotion_info = adaptation.get("promotion_info", {})
    games_needed = promotion_info.get("easy_games_needed", 0) or promotion_info.get("medium_games_needed", 0)
    
    return {
        "game_id": new_game.id,
        "puzzle": puzzle_grid,
        "difficulty": actual_difficulty,
        "player_skill_used": skill_level,
        "was_adjusted": was_adjusted,
        "requested_difficulty": difficulty if was_adjusted else None,
        "allowed_difficulties": allowed_difficulties,
        "adaptation": {
            "skill_level": skill_level,
            "games_analyzed": adaptation.get("games_analyzed", 0),
            "total_games_all_time": adaptation.get("total_games_all_time", 0),
            "win_rate_recent": adaptation.get("win_rate", 0),
            "easy_win_rate": adaptation.get("easy_win_rate"),
            "medium_win_rate": adaptation.get("medium_win_rate"),
            "games_needed_for_next_level": games_needed,
            "required_win_rate": promotion_info.get("required_win_rate", 0),
            "next_skill": promotion_info.get("next_skill"),
            "reason": adaptation.get("reason", ""),
            "detailed_reason": adaptation.get("detailed_reason", ""),
            "message": f"Автоматически изменено с {difficulty} на {actual_difficulty}" if was_adjusted else f"Игра создана на уровне {actual_difficulty}"
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
    Проверяет правильность хода и сохраняет его в current_board.
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
    solution = json.loads(game.solution)
    current_board = json.loads(game.current_board) if game.current_board else json.loads(game.puzzle)
    
    # Проверка: можно ли менять эту клетку (изначально заданные клетки нельзя менять)
    if puzzle[row][col] != 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Клетка [{row}, {col}] была заполнена изначально, её нельзя менять"
        )
    
    
    
    # Проверка правильности хода (только для вставки цифры)
    is_valid = (solution[row][col] == value)
    
    if not is_valid:
        # Неправильный ход — не сохраняем, возвращаем ошибку
        cells_filled = sum(1 for row_board in current_board for cell in row_board if cell != 0)
        return {
            "success": False,
            "valid": False,
            "message": f"✗ Неправильно. Правильная цифра: {solution[row][col]}",
            "row": row,
            "col": col,
            "value": value,
            "correct_value": solution[row][col],
            "cleared": False,
            "cells_filled": cells_filled,
            "cells_total": 81,
            "is_completed": False
        }
    
    # Сохраняем правильный ход
    current_board[row][col] = value
    game.current_board = json.dumps(current_board)
    
    # Подсчитываем заполненные клетки
    cells_filled = sum(1 for row_board in current_board for cell in row_board if cell != 0)
    
    # Проверяем, не завершена ли игра
    is_game_completed = False
    message = f"✓ Правильно! Цифра {value} поставлена в клетку [{row}, {col}]"
    
    # Если все клетки заполнены, НО проверку правильности делает эндпоинт /check
    # Здесь только сохраняем ход, но не завершаем игру автоматически
    # (это можно оставить как опцию, но лучше чтобы /check занимался завершением)
    
    session.add(game)
    session.commit()
    
    return {
        "success": True,
        "valid": True,
        "message": message,
        "row": row,
        "col": col,
        "value": value,
        "cleared": False,
        "cells_filled": cells_filled,
        "cells_total": 81,
        "is_completed": game.is_completed
    }

# ============================================
# ИСПРАВЛЕННЫЙ ЭНДПОИНТ HINT
# ============================================



import random
from datetime import datetime

@router.post("/sudoku/{game_id}/hint")
async def get_hint(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Получить подсказку 
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
        
        # Собираем все пустые клетки
        empty_cells = []
        for row in range(9):
            for col in range(9):
                if puzzle[row][col] == 0:
                    if current_board[row][col] == 0:
                        empty_cells.append({
                            "row": row,
                            "col": col,
                            "current_value": 0,
                            "correct_value": solution[row][col],
                            "reason": "empty"
                        })
                    elif current_board[row][col] != solution[row][col]:
                        empty_cells.append({
                            "row": row,
                            "col": col,
                            "current_value": current_board[row][col],
                            "correct_value": solution[row][col],
                            "reason": "wrong"
                        })
        
        if not empty_cells:
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
        
        random_cell = random.choice(empty_cells)
        row = random_cell["row"]
        col = random_cell["col"]
        correct_value = random_cell["correct_value"]
        
        # Сохраняем подсказку как ход
        current_board[row][col] = correct_value
        game.current_board = json.dumps(current_board)
        game.last_move_at = datetime.utcnow()
        
        # Проверяем, не завершена ли игра после подсказки
        is_complete = all(
            all(current_board[r][c] != 0 for c in range(9))
            for r in range(9)
        )
        
        is_win = False
        rating_earned = 0
        skill_update = None
        
        if is_complete:
            # Проверяем, всё ли правильно
            if current_board == solution:
                is_win = True
                game.is_completed = True
                game.completed_at = datetime.utcnow()
                
                # Начисляем очки (меньше, чем за самостоятельное решение)
                difficulty_scores = {"easy": 8, "medium": 15, "hard": 25}
                rating_earned = difficulty_scores.get(game.difficulty, 10)
                user.rating += rating_earned
                
                # 🔥 КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Обновляем уровень навыка
                from app.services.adaptive_difficulty import AdaptiveDifficulty
                skill_update = AdaptiveDifficulty.update_skill_level(user.id, session)
                logger.info(f"Skill update from hint win: {skill_update}")
                
                session.add(user)
                
                message = f"🎉 Подсказка привела к победе! +{rating_earned} к рейтингу (с подсказкой)"
            else:
                message = "Все клетки заполнены, но есть ошибки. Проверьте решение!"
        else:
            if random_cell["reason"] == "wrong":
                message = f"🔍 Подсказка: клетка [{row}, {col}] исправлена с {random_cell['current_value']} на {correct_value}"
            else:
                message = f"🔍 Подсказка: в клетку [{row}, {col}] поставлена цифра {correct_value}"
        
        # Сохраняем изменения
        session.add(game)
        session.commit()
        
        # Если игра завершена, обновляем объект пользователя
        if is_win:
            session.refresh(user)
        
        # Подсчитываем оставшиеся пустые клетки
        remaining_empty = sum(
            1 for r in range(9) for c in range(9)
            if puzzle[r][c] == 0 and current_board[r][c] == 0
        )
        
        return {
            "success": True,
            "row": row,
            "col": col,
            "value": correct_value,
            "was_wrong": random_cell["reason"] == "wrong",
            "message": message,
            "hint_available": True,
            "remaining_empty_cells": remaining_empty,
            "is_game_over": is_win,
            "rating_earned": rating_earned,
            "skill_update": skill_update  # ← Добавляем информацию об обновлении уровня
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка при получении подсказки: {str(e)}")

# ============================================
# ЭНДПОИНТ ДЛЯ ПРОВЕРКИ РЕШЕНИЯ
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
        
        skill_update = AdaptiveDifficulty.update_skill_level(user.id, session)
        
        session.add(user)
        session.add(game)
        session.commit()
        
        logger.info(f"Sudoku {game_id} completed by user {vk_user_id}, earned {rating_earned} points")
        
        return {
            "is_correct": True,
            "is_complete": True,
            "message": f"🎉 Поздравляем! Судоку решена правильно! +{rating_earned} к рейтингу",
            "rating_earned": rating_earned,
            "skill_update": skill_update  # ← Добавляем информацию об обновлении уровня
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
# ОСТАЛЬНЫЕ ЭНДПОИНТЫ
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


