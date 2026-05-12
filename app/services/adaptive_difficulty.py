# app/services/adaptive_difficulty.py
import logging
from typing import Optional, Dict, Any
from sqlmodel import Session, select, func
from datetime import datetime

from ..models import User, SudokuGame, PuzzleGame

logger = logging.getLogger(__name__)

DIFFICULTY_LEVELS = {
    "easy": 1,
    "medium": 2,
    "hard": 3,
    "expert": 4
}

LEVEL_TO_DIFFICULTY = {v: k for k, v in DIFFICULTY_LEVELS.items()}

class AdaptiveDifficulty:


    @staticmethod
    def get_player_skill(user_id: int, session: Session) -> Dict[str, Any]:
        """
        Определить скилл игрока на основе истории с УЧЁТОМ СЛОЖНОСТИ
        """
        stats = AdaptiveDifficulty.get_player_stats(user_id, session)
        total_games = stats.get("total_games", 0)
        
        # Нет игр → новичок
        if total_games == 0:
            return {
                "skill": "beginner",
                "source": "default",
                "confidence": 100,
                "reason": "New player, default skill: beginner",
                "games_played": 0,
                "win_rate": 0
            }
        
        # Меньше 3 игр → недостаточно данных, новичок
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Only {total_games} games played, assuming beginner",
                "games_played": total_games,
                "win_rate": stats["win_rate"]
            }
        
        # Новая логика: анализируем сложность побед
        games_by_diff = stats.get("games_by_difficulty", {})
        
        # Считаем "очки скилла" (чем сложнее победа, тем больше очков)
        skill_points = 0
        total_attempts = 0
        
        difficulty_scores = {
            "easy": 1,
            "medium": 2,
            "hard": 3,
            "expert": 4
        }
        
        for difficulty, diff_stats in games_by_diff.items():
            completed = diff_stats.get("completed", 0)
            total = diff_stats.get("total", 0)
            
            if total > 0:
                # Победы на этой сложности
                skill_points += completed * difficulty_scores.get(difficulty, 1)
                total_attempts += total
        
        # Средний "вес" победы
        avg_skill_score = skill_points / total_attempts if total_attempts > 0 else 0
        
        # Определяем уровень на основе среднего веса побед
        if avg_skill_score >= 3.5:  # В основном expert
            skill = "expert"
            confidence = 85
            reason = f"Expert player: avg score {avg_skill_score:.1f}"
        elif avg_skill_score >= 2.5:  # В основном hard
            skill = "advanced"
            confidence = 80
            reason = f"Advanced player: avg score {avg_skill_score:.1f}"
        elif avg_skill_score >= 1.5:  # В основном medium
            skill = "intermediate"
            confidence = 75
            reason = f"Intermediate player: avg score {avg_skill_score:.1f}"
        else:
            skill = "beginner"
            confidence = 80
            reason = f"Beginner player: avg score {avg_skill_score:.1f}"
        
        return {
            "skill": skill,
            "source": "auto_detected",
            "confidence": confidence,
            "reason": reason,
            "games_played": total_games,
            "completed_games": stats["completed_games"],
            "win_rate": stats["win_rate"],
            "avg_skill_score": round(avg_skill_score, 2)
        }