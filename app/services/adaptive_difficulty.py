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
    def get_player_stats(user_id: int, session: Session) -> Dict[str, Any]:
        """Получить полную статистику игрока"""
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user_id)
        ).all()
        
        puzzle_games = session.exec(
            select(PuzzleGame).where(PuzzleGame.user_id == user_id)
        ).all()
        
        all_games = sudoku_games + puzzle_games
        total_games = len(all_games)
        
        if total_games == 0:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "games_by_difficulty": {},
                "last_game_difficulty": None
            }
        
        completed_games = [g for g in all_games if g.is_completed]
        completed_count = len(completed_games)
        win_rate = (completed_count / total_games) * 100
        
        games_by_difficulty = {}
        for game in all_games:
            diff = game.difficulty
            if diff not in games_by_difficulty:
                games_by_difficulty[diff] = {"total": 0, "completed": 0}
            games_by_difficulty[diff]["total"] += 1
            if game.is_completed:
                games_by_difficulty[diff]["completed"] += 1
        
        return {
            "total_games": total_games,
            "completed_games": completed_count,
            "win_rate": round(win_rate, 2),
            "games_by_difficulty": games_by_difficulty,
            "last_game_difficulty": None
        }
    
    @staticmethod
    def calculate_skill_level(stats: Dict[str, Any]) -> Dict[str, Any]:
        """Рассчитать уровень скилла на основе статистики"""
        total_games = stats.get("total_games", 0)
        win_rate = stats.get("win_rate", 0)
        games_by_diff = stats.get("games_by_difficulty", {})
        
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Only {total_games} games played, assuming beginner",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # Расчёт скилла на основе сложности побед
        skill_points = 0
        total_points = 0
        
        difficulty_scores = {
            "easy": 1,
            "medium": 2,
            "hard": 3,
            "expert": 4
        }
        
        for difficulty, diff_stats in games_by_diff.items():
            completed = diff_stats.get("completed", 0)
            total = diff_stats.get("total", 0)
            score = difficulty_scores.get(difficulty, 2)
            
            skill_points += completed * score
            total_points += total * score
        
        avg_skill_score = skill_points / total_points if total_points > 0 else 0
        
        if avg_skill_score >= 2.5:
            skill = "advanced"
            confidence = 85
            reason = f"Advanced player: avg score {avg_skill_score:.1f}"
        elif avg_skill_score >= 1.5:
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
            "win_rate": win_rate,
            "avg_skill_score": round(avg_skill_score, 2)
        }
    
    @staticmethod
    async def get_adaptive_difficulty(
        vk_user_id: str,
        requested_difficulty: str,
        session: Session,
        client_skill: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Основной метод - возвращает адаптированную сложность
        """
        from ..models import User
        
        requested_difficulty = requested_difficulty.lower()
        
        # Получаем пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        # Если пользователь не существует
        if not user:
            return {
                "difficulty": requested_difficulty,
                "was_adjusted": False,
                "skill_level": "beginner",
                "skill_source": "default",
                "confidence": 100,
                "reason": "New user, skill set to beginner",
                "games_played": 0,
                "win_rate": 0
            }
        
        # Если клиент прислал скилл - используем его
        if client_skill:
            skill_info = {
                "skill": client_skill,
                "source": "client",
                "confidence": 100,
                "reason": "Client provided skill",
                "games_played": None,
                "win_rate": None
            }
        else:
            # Определяем скилл автоматически
            stats = AdaptiveDifficulty.get_player_stats(user.id, session)
            skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
        
        return {
            "difficulty": requested_difficulty,  # Не меняем сложность
            "was_adjusted": False,
            "skill_level": skill_info["skill"],
            "skill_source": skill_info["source"],
            "confidence": skill_info["confidence"],
            "reason": f"{skill_info['reason']} - using requested difficulty: {requested_difficulty}",
            "games_played": skill_info.get("games_played", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "avg_skill_score": skill_info.get("avg_skill_score")
        }