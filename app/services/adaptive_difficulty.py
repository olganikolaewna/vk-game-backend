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

PROMOTION_THRESHOLDS = {
    "beginner": {
        "min_games": 10,
        "min_win_rate": 60,
        "next_skill": "intermediate",
        "max_difficulty": "easy",
        "window_size": 10
    },
    "intermediate": {
        "min_games": 10,
        "min_win_rate": 60,
        "next_skill": "advanced",
        "max_difficulty": "medium",
        "window_size": 10
    },
    "advanced": {
        "min_games": 0,
        "min_win_rate": 0,
        "next_skill": None,
        "max_difficulty": "hard",
        "window_size": 10
    }
}

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
        Возвращает адаптированную сложность
        win-rate для повышения считается по последним 10 играм
        но показывает и полную статистику
        """
        from ..models import User
        
        requested_difficulty = requested_difficulty.lower()
        
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            return {
                "difficulty": "easy" if requested_difficulty != "easy" else "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "total_games_all_time": 0,
                "win_rate_all_time": 0,
                "games_played_last_10": 0,
                "win_rate_last_10": 0,
                "games_needed": 10,
                "required_win_rate": 60
            }
        
        # 🔥 Получаем ВСЕ игры пользователя
        sudoku_games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user.id)
            .order_by(SudokuGame.created_at.desc())
        ).all()
        
        # ========== ПОЛНАЯ СТАТИСТИКА (для отображения) ==========
        easy_games_all = [g for g in sudoku_games if g.difficulty == "easy"]
        medium_games_all = [g for g in sudoku_games if g.difficulty == "medium"]
        hard_games_all = [g for g in sudoku_games if g.difficulty == "hard"]
        
        easy_wins_all = sum(1 for g in easy_games_all if g.is_completed)
        easy_total_all = len(easy_games_all)
        easy_win_rate_all = (easy_wins_all / easy_total_all * 100) if easy_total_all > 0 else 0
        
        medium_wins_all = sum(1 for g in medium_games_all if g.is_completed)
        medium_total_all = len(medium_games_all)
        medium_win_rate_all = (medium_wins_all / medium_total_all * 100) if medium_total_all > 0 else 0
        
        # ========== СТАТИСТИКА ДЛЯ ПОВЫШЕНИЯ (последние 10 игр) ==========
        window_size = 10
        last_easy_games = easy_games_all[:window_size]
        last_medium_games = medium_games_all[:window_size]
        
        easy_wins_last10 = sum(1 for g in last_easy_games if g.is_completed)
        easy_total_last10 = len(last_easy_games)
        easy_win_rate_last10 = (easy_wins_last10 / easy_total_last10 * 100) if easy_total_last10 > 0 else 0
        
        medium_wins_last10 = sum(1 for g in last_medium_games if g.is_completed)
        medium_total_last10 = len(last_medium_games)
        medium_win_rate_last10 = (medium_wins_last10 / medium_total_last10 * 100) if medium_total_last10 > 0 else 0
        
        # 🔥 Определяем скилл НА ОСНОВЕ ПОСЛЕДНИХ 10 ИГР
        skill = "beginner"
        
        if easy_total_all >= PROMOTION_THRESHOLDS["beginner"]["min_games"]:
            if easy_win_rate_last10 >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]:
                skill = "intermediate"
        
        if skill == "intermediate" and medium_total_all >= PROMOTION_THRESHOLDS["intermediate"]["min_games"]:
            if medium_win_rate_last10 >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]:
                skill = "advanced"
        
        # Определяем доступные сложности
        max_difficulty = PROMOTION_THRESHOLDS[skill]["max_difficulty"]
        allowed_difficulties = SKILL_TO_ALLOWED_DIFFICULTIES[skill]
        
        # Автоматическая адаптация
        final_difficulty = requested_difficulty
        was_adjusted = False
        
        if DIFFICULTY_LEVELS[requested_difficulty] > DIFFICULTY_LEVELS[max_difficulty]:
            final_difficulty = max_difficulty
            was_adjusted = True
        
        # Расчет прогресса для следующего уровня
        if skill == "beginner":
            current_wins_last10 = easy_wins_last10
            current_total_last10 = easy_total_last10
            current_win_rate_last10 = easy_win_rate_last10
            games_needed = max(0, PROMOTION_THRESHOLDS["beginner"]["min_games"] - easy_total_last10)
            required_win_rate = PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]
        elif skill == "intermediate":
            current_wins_last10 = medium_wins_last10
            current_total_last10 = medium_total_last10
            current_win_rate_last10 = medium_win_rate_last10
            games_needed = max(0, PROMOTION_THRESHOLDS["intermediate"]["min_games"] - medium_total_last10)
            required_win_rate = PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]
        else:
            current_wins_last10 = 10
            current_total_last10 = 10
            current_win_rate_last10 = 100
            games_needed = 0
            required_win_rate = 0
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "allowed_difficulties": allowed_difficulties,
            
            # 🔥 ПОЛНАЯ СТАТИСТИКА (для отображения на фронтенде)
            "total_stats": {
                "easy": {
                    "total": easy_total_all,
                    "completed": easy_wins_all,
                    "win_rate": round(easy_win_rate_all, 1)
                },
                "medium": {
                    "total": medium_total_all,
                    "completed": medium_wins_all,
                    "win_rate": round(medium_win_rate_all, 1)
                },
                "hard": {
                    "total": len(hard_games_all),
                    "completed": sum(1 for g in hard_games_all if g.is_completed),
                    "win_rate": round((sum(1 for g in hard_games_all if g.is_completed) / len(hard_games_all) * 100), 1) if hard_games_all else 0
                },
                "all_games": {
                    "total": len(sudoku_games),
                    "completed": sum(1 for g in sudoku_games if g.is_completed),
                    "win_rate": round((sum(1 for g in sudoku_games if g.is_completed) / len(sudoku_games) * 100), 1) if sudoku_games else 0
                }
            },
            
            # 🔥 СТАТИСТИКА ДЛЯ ПОВЫШЕНИЯ (последние 10 игр)
            "promotion_stats": {
                "window_size": window_size,
                "games_analyzed": current_total_last10,
                "wins": current_wins_last10,
                "win_rate": round(current_win_rate_last10, 1),
                "required_win_rate": required_win_rate,
                "games_needed_to_next_level": games_needed,
                "wins_needed": max(0, int(required_win_rate / 100 * window_size) - current_wins_last10),
                "progress_percentage": min(100, (current_wins_last10 / window_size * 100)) if current_total_last10 >= window_size else current_win_rate_last10
            },
            
            "max_difficulty": max_difficulty,
            "next_level": PROMOTION_THRESHOLDS[skill]["next_skill"] if skill in PROMOTION_THRESHOLDS else None
        }