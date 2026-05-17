# app/services/adaptive_difficulty.py

import logging
from typing import Optional, Dict, Any
from sqlmodel import Session, select
from datetime import datetime

from ..models import User, SudokuGame, PuzzleGame

logger = logging.getLogger(__name__)

DIFFICULTY_LEVELS = {
    "easy": 1,
    "medium": 2,
    "hard": 3
}

# Пороги для повышения уровня (6 побед из 10 = 60%)
PROMOTION_THRESHOLDS = {
    "beginner": {
        "min_games": 6,
        "min_win_rate": 60,
        "next_skill": "intermediate",
        "max_difficulty": "easy"
    },
    "intermediate": {
        "min_games": 6,
        "min_win_rate": 60,
        "next_skill": "advanced",
        "max_difficulty": "medium"
    },
    "advanced": {
        "min_games": 0,
        "min_win_rate": 0,
        "next_skill": None,
        "max_difficulty": "hard"
    }
}

# Доступные сложности для каждого скилла
SKILL_TO_ALLOWED_DIFFICULTIES = {
    "beginner": ["easy"],
    "intermediate": ["easy", "medium"],
    "advanced": ["easy", "medium", "hard"]
}

class AdaptiveDifficulty:
    
    @staticmethod
    async def get_adaptive_difficulty(
        vk_user_id: str,
        requested_difficulty: str,
        session: Session,
        client_skill: Optional[str] = None,
        auto_adjust: bool = True
    ) -> Dict[str, Any]:
        """
        🔥 Автоматически переводит игрока на разрешенный уровень
        """
        from ..models import User
        
        requested_difficulty = requested_difficulty.lower()
        
        # Получаем пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            # Новый игрок - только easy
            final_difficulty = "easy" if requested_difficulty != "easy" else "easy"
            return {
                "difficulty": final_difficulty,
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "games_played": 0,
                "win_rate": 0,
                "games_needed": 6,
                "required_win_rate": 60
            }
        
        # Получаем статистику игрока
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        
        # Анализируем игры по сложностям
        easy_games = [g for g in sudoku_games if g.difficulty == "easy"]
        medium_games = [g for g in sudoku_games if g.difficulty == "medium"]
        
        easy_wins = sum(1 for g in easy_games if g.is_completed)
        easy_total = len(easy_games)
        easy_win_rate = (easy_wins / easy_total * 100) if easy_total > 0 else 0
        
        medium_wins = sum(1 for g in medium_games if g.is_completed)
        medium_total = len(medium_games)
        medium_win_rate = (medium_wins / medium_total * 100) if medium_total > 0 else 0
        
        # 🔥 Определяем скилл игрока
        skill = "beginner"
        
        # Проверяем, может ли игрок быть intermediate
        if easy_total >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]:
            skill = "intermediate"
        
        # Проверяем, может ли игрок быть advanced
        if skill == "intermediate":
            if medium_total >= PROMOTION_THRESHOLDS["intermediate"]["min_games"] and medium_win_rate >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]:
                skill = "advanced"
        
        # 🔥 Получаем максимальную доступную сложность
        max_difficulty = PROMOTION_THRESHOLDS[skill]["max_difficulty"]
        allowed_difficulties = SKILL_TO_ALLOWED_DIFFICULTIES[skill]
        
        # 🔥 Автоматически выбираем сложность
        final_difficulty = requested_difficulty
        was_adjusted = False
        
        # Если запрошенная сложность выше максимальной - понижаем
        if DIFFICULTY_LEVELS[requested_difficulty] > DIFFICULTY_LEVELS[max_difficulty]:
            final_difficulty = max_difficulty
            was_adjusted = True
        # Если запрошенная сложность ниже - оставляем (игрок может играть на легких уровнях)
        
        # Подсчитываем, сколько еще нужно игр для повышения
        games_needed = 0
        required_win_rate = 0
        
        if skill == "beginner":
            games_needed = max(0, PROMOTION_THRESHOLDS["beginner"]["min_games"] - easy_total)
            required_win_rate = PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]
            current_win_rate = easy_win_rate
        elif skill == "intermediate":
            games_needed = max(0, PROMOTION_THRESHOLDS["intermediate"]["min_games"] - medium_total)
            required_win_rate = PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]
            current_win_rate = medium_win_rate
        else:
            games_needed = 0
            required_win_rate = 0
            current_win_rate = 0
        
        logger.info(f"Player {vk_user_id}: skill={skill}, requested={requested_difficulty}, final={final_difficulty}, adjusted={was_adjusted}")
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "allowed_difficulties": allowed_difficulties,
            "games_played": easy_total if skill == "beginner" else medium_total,
            "win_rate": round(current_win_rate, 1),
            "games_needed": games_needed,
            "required_win_rate": required_win_rate,
            "max_difficulty": max_difficulty,
            "details": {
                "easy_games": easy_total,
                "easy_wins": easy_wins,
                "easy_win_rate": round(easy_win_rate, 1),
                "medium_games": medium_total,
                "medium_wins": medium_wins,
                "medium_win_rate": round(medium_win_rate, 1)
            }
        }
    
    @staticmethod
    async def get_recommended_difficulty(
        vk_user_id: str,
        session: Session
    ) -> Dict[str, Any]:
        """Получить рекомендуемую сложность"""
        from ..models import User
        
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            return {
                "recommended_difficulty": "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "games_played": 0,
                "win_rate": 0
            }
        
        # Получаем статистику
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        
        easy_games = [g for g in sudoku_games if g.difficulty == "easy"]
        easy_wins = sum(1 for g in easy_games if g.is_completed)
        easy_total = len(easy_games)
        easy_win_rate = (easy_wins / easy_total * 100) if easy_total > 0 else 0
        
        medium_games = [g for g in sudoku_games if g.difficulty == "medium"]
        medium_wins = sum(1 for g in medium_games if g.is_completed)
        medium_total = len(medium_games)
        medium_win_rate = (medium_wins / medium_total * 100) if medium_total > 0 else 0
        
        # Определяем скилл
        if easy_total >= 6 and easy_win_rate >= 60:
            if medium_total >= 6 and medium_win_rate >= 60:
                skill = "advanced"
                recommended = "hard"
                allowed = ["easy", "medium", "hard"]
            else:
                skill = "intermediate"
                recommended = "medium"
                allowed = ["easy", "medium"]
        else:
            skill = "beginner"
            recommended = "easy"
            allowed = ["easy"]
        
        return {
            "recommended_difficulty": recommended,
            "skill_level": skill,
            "allowed_difficulties": allowed,
            "games_played": easy_total if skill == "beginner" else medium_total,
            "win_rate": round(easy_win_rate if skill == "beginner" else medium_win_rate, 1),
            "next_level": {
                "required_games": 6,
                "required_win_rate": 60,
                "games_played": easy_total if skill == "beginner" else medium_total,
                "current_win_rate": round(easy_win_rate if skill == "beginner" else medium_win_rate, 1),
                "games_needed": max(0, 6 - (easy_total if skill == "beginner" else medium_total))
            } if skill != "advanced" else None
        }