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
    🔥 Автоматически переводит на разрешенный уровень
    🔥 Адаптация работает на основе последних 20 игр
    """
    user = await get_or_create_user(vk_user_id, session)
    
    # Получаем адаптированную сложность (считаем по последним 20 играм)

    
    adaptation = await AdaptiveDifficulty.get_adaptive_difficulty(
        vk_user_id=vk_user_id,
        requested_difficulty=difficulty,
        session=session,
        client_skill=player_skill,
        auto_adjust=True,           # Явно включаем адаптацию
        recent_games_limit=20       # Анализируем последние 20 игр
    )
    
    # Берем итоговую сложность (уже адаптированную)
    final_difficulty = adaptation["difficulty"]
    was_adjusted = adaptation["was_adjusted"]
    skill_level = adaptation["skill_level"]
    allowed_difficulties = adaptation.get("allowed_difficulties", ["easy"])
    
    if was_adjusted:
        logger.info(f"User {vk_user_id} (skill: {skill_level}) requested {difficulty}, auto-adjusted to {final_difficulty}")
    
    logger.info(f"Creating game: user={vk_user_id}, difficulty={final_difficulty}, skill={skill_level}, "
                f"analyzed {adaptation.get('games_analyzed', 0)} recent games")
    
    # Запрашиваем у ИИ-сервиса
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            request_body = {
                "game_type": "sudoku",
                "difficulty": final_difficulty,
                "player_skill": skill_level,
                "prompt": "",
                "custom_params": {}
            }
            
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
        # Заглушка
        puzzle_grid = [[0 for _ in range(9)] for _ in range(9)]
        solution_grid = [[0 for _ in range(9)] for _ in range(9)]
        for i in range(9):
            puzzle_grid[i][i] = i + 1
            solution_grid[i][i] = i + 1
    
    # Сохраняем игру
    new_game = SudokuGame(
        user_id=user.id,
        puzzle=json.dumps(puzzle_grid),
        solution=json.dumps(solution_grid),
        difficulty=final_difficulty,
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
        "difficulty": final_difficulty,
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
            "message": f"Автоматически изменено с {difficulty} на {final_difficulty}" if was_adjusted else f"Игра создана на уровне {final_difficulty}"
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



import random
from datetime import datetime

@router.post("/sudoku/{game_id}/hint")
async def get_hint(
    game_id: int,
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Получить подсказку (случайная пустая клетка + засчитывается как ход)
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
                # 2. Игрок её ещё не заполнил правильно
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
        
        # 🎲 Если есть неправильные клетки, даём подсказку для них (приоритет)
        # Иначе — для пустых
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
        
        # 🎲 Выбираем случайную клетку (гарантированно разную при каждом запросе)
        random_cell = random.choice(empty_cells)
        row = random_cell["row"]
        col = random_cell["col"]
        correct_value = random_cell["correct_value"]
        
        # 💾 СОХРАНЯЕМ ПОДСКАЗКУ КАК ХОД
        # Обновляем доску правильным значением
        current_board[row][col] = correct_value
        game.current_board = json.dumps(current_board)
        
        # Обновляем время последнего хода
        game.last_move_at = datetime.utcnow()
        
        # Проверяем, не завершена ли игра после подсказки
        is_complete = all(
            all(current_board[r][c] != 0 for c in range(9))
            for r in range(9)
        )
        
        is_win = False
        rating_earned = 0
        
        if is_complete:
            # Проверяем, всё ли правильно
            if current_board == solution:
                is_win = True
                game.is_completed = True
                game.completed_at = datetime.utcnow()
                
                # Начисляем очки (но меньше, чем за самостоятельное решение)
                difficulty_scores = {"easy": 8, "medium": 15, "hard": 25}
                rating_earned = difficulty_scores.get(game.difficulty, 10)
                user.rating += rating_earned
                session.add(user)
                
                message = f"🎉 Подсказка привела к победе! +{rating_earned} к рейтингу (с подсказкой)"
            else:
                message = "Все клетки заполнены, но есть ошибки. Проверьте решение!"
        else:
            # Формируем сообщение в зависимости от ситуации
            if random_cell["reason"] == "wrong":
                message = f"🔍 Подсказка: клетка [{row}, {col}] исправлена с {random_cell['current_value']} на {correct_value}"
            else:
                message = f"🔍 Подсказка: в клетку [{row}, {col}] поставлена цифра {correct_value}"
        
        # Сохраняем изменения
        session.add(game)
        session.commit()
        
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
            "rating_earned": rating_earned
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