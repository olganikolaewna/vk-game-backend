# app/services/adaptive_difficulty.py - СОКРАЩЕННАЯ РАБОЧАЯ ВЕРСИЯ

import logging
from typing import Optional, Dict, Any
from sqlmodel import Session, select

from ..models import User, SudokuGame

logger = logging.getLogger(__name__)

DIFFICULTY_LEVELS = {"easy": 1, "medium": 2, "hard": 3}

SKILL_TO_ALLOWED_DIFFICULTIES = {
    "beginner": ["easy"],
    "intermediate": ["easy", "medium"],
    "advanced": ["easy", "medium", "hard"]
}

PROMOTION_THRESHOLDS = {
    "beginner": {"min_games": 6, "min_win_rate": 60, "next_skill": "intermediate", "max_difficulty": "easy"},
    "intermediate": {"min_games": 6, "min_win_rate": 60, "next_skill": "advanced", "max_difficulty": "medium"},
    "advanced": {"min_games": 0, "min_win_rate": 0, "next_skill": None, "max_difficulty": "hard"}
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
        from ..models import User
        
        user = session.exec(select(User).where(User.vk_user_id == str(vk_user_id))).first()
        
        if not user:
            return {
                "difficulty": "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "total_games": 0,
                "win_rate": 0
            }
        
        # Получаем ВСЕ игры
        games = session.exec(select(SudokuGame).where(SudokuGame.user_id == user.id)).all()
        
        total_games = len(games)
        if total_games == 0:
            return {
                "difficulty": "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "total_games": 0,
                "win_rate": 0
            }
        
        # Статистика по сложностям
        easy_games = [g for g in games if g.difficulty == "easy" and g.is_completed]
        medium_games = [g for g in games if g.difficulty == "medium" and g.is_completed]
        
        easy_wins = len(easy_games)
        medium_wins = len(medium_games)
        easy_total = len([g for g in games if g.difficulty == "easy"])
        medium_total = len([g for g in games if g.difficulty == "medium"])
        
        easy_win_rate = (easy_wins / easy_total * 100) if easy_total > 0 else 0
        medium_win_rate = (medium_wins / medium_total * 100) if medium_total > 0 else 0
        
        # Определяем скилл
        if easy_total >= 6 and easy_win_rate >= 60:
            if medium_total >= 6 and medium_win_rate >= 60:
                skill = "advanced"
            else:
                skill = "intermediate"
        else:
            skill = "beginner"
        
        allowed = SKILL_TO_ALLOWED_DIFFICULTIES[skill]
        max_diff = PROMOTION_THRESHOLDS[skill]["max_difficulty"]
        
        # Адаптация
        final_difficulty = requested_difficulty
        was_adjusted = False
        if requested_difficulty not in allowed:
            final_difficulty = max_diff
            was_adjusted = True
        
        # Win-rate по последним 10 играм
        last_10 = games[:10]
        last_10_wins = sum(1 for g in last_10 if g.is_completed)
        last_10_win_rate = (last_10_wins / len(last_10) * 100) if last_10 else 0
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "allowed_difficulties": allowed,
            "total_games": total_games,
            "completed_games": sum(1 for g in games if g.is_completed),
            "win_rate_total": round((sum(1 for g in games if g.is_completed) / total_games * 100), 1),
            "win_rate_last_10": round(last_10_win_rate, 1),
            "promotion": {
                "wins_needed": max(0, 6 - last_10_wins),
                "games_analyzed": len(last_10)
            }
        }