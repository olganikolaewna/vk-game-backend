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

# Карта: скилл игрока → рекомендуемая сложность
SKILL_TO_DIFFICULTY = {
    "beginner": "easy",
    "intermediate": "medium",
    "advanced": "hard",
    "expert": "hard"
}

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
        
        # Находим последнюю игру
        last_game = None
        all_games_sorted = sorted(all_games, key=lambda g: g.created_at, reverse=True)
        if all_games_sorted:
            last_game = all_games_sorted[0].difficulty
        
        return {
            "total_games": total_games,
            "completed_games": completed_count,
            "win_rate": round(win_rate, 2),
            "games_by_difficulty": games_by_difficulty,
            "last_game_difficulty": last_game
        }
    
    @staticmethod
    def calculate_skill_level(stats: Dict[str, Any]) -> Dict[str, Any]:
        """Рассчитать уровень скилла на основе статистики"""
        total_games = stats.get("total_games", 0)
        win_rate = stats.get("win_rate", 0)
        games_by_diff = stats.get("games_by_difficulty", {})
        
        # Недостаточно данных
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Only {total_games} games played, assuming beginner",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # Если винрейт очень низкий (< 10%) — новичок
        if win_rate < 10 and total_games >= 5:
            return {
                "skill": "beginner",
                "source": "low_win_rate",
                "confidence": 85,
                "reason": f"Win rate only {win_rate}% over {total_games} games",
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
        
        # Определение скилла на основе среднего балла
        if avg_skill_score >= 2.8:
            skill = "expert"
            confidence = 85
            reason = f"Expert player: avg score {avg_skill_score:.1f}"
        elif avg_skill_score >= 2.0:
            skill = "advanced"
            confidence = 80
            reason = f"Advanced player: avg score {avg_skill_score:.1f}"
        elif avg_skill_score >= 1.3:
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
        client_skill: Optional[str] = None,
        auto_adjust: bool = True
    ) -> Dict[str, Any]:
        """
        Основной метод - возвращает адаптированную сложность
        
        Параметры:
            vk_user_id: ID пользователя VK
            requested_difficulty: Запрошенная сложность (easy/medium/hard/expert)
            session: Сессия БД
            client_skill: Скилл от клиента (опционально)
            auto_adjust: Автоматически изменять сложность (по умолчанию True)
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
                "win_rate": 0,
                "requested_difficulty": requested_difficulty
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
        
        skill = skill_info["skill"]
        final_difficulty = requested_difficulty
        was_adjusted = False
        adjust_reason = ""
        
        # ========== АВТОМАТИЧЕСКАЯ АДАПТАЦИЯ СЛОЖНОСТИ ==========
        if auto_adjust:
            recommended = SKILL_TO_DIFFICULTY.get(skill, "medium")
            requested_level = DIFFICULTY_LEVELS.get(requested_difficulty, 2)
            recommended_level = DIFFICULTY_LEVELS.get(recommended, 2)
            
            # Понижаем сложность, если игрок слабее запрошенного уровня
            if recommended_level < requested_level:
                final_difficulty = recommended
                was_adjusted = True
                adjust_reason = f"Skill '{skill}' is lower than requested '{requested_difficulty}', adjusted down to '{recommended}'"
            
            # Повышаем сложность, если игрок сильнее запрошенного уровня
            elif recommended_level > requested_level:
                # Но не повышаем выше hard для новичков
                if skill == "expert" and requested_difficulty == "hard":
                    # Эксперт просит hard — оставляем hard
                    pass
                elif skill in ["advanced", "expert"] and requested_difficulty in ["easy", "medium"]:
                    final_difficulty = recommended
                    was_adjusted = True
                    adjust_reason = f"Skill '{skill}' is higher than requested '{requested_difficulty}', adjusted up to '{recommended}'"
        else:
            adjust_reason = f"Auto-adjust disabled, using requested difficulty: {requested_difficulty}"
        
        # Формируем финальное сообщение
        if not adjust_reason:
            adjust_reason = f"Skill '{skill}' matches requested difficulty '{requested_difficulty}'"
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill_info["skill"],
            "skill_source": skill_info["source"],
            "confidence": skill_info["confidence"],
            "reason": adjust_reason,
            "detailed_reason": skill_info["reason"],
            "games_played": skill_info.get("games_played", 0),
            "completed_games": skill_info.get("completed_games", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "avg_skill_score": skill_info.get("avg_skill_score"),
            "requested_difficulty": requested_difficulty
        }
    
    @staticmethod
    async def get_recommended_difficulty(
        vk_user_id: str,
        session: Session
    ) -> Dict[str, Any]:
        """
        Получить только рекомендуемую сложность (без запроса от клиента)
        Используется для отправки рекомендаций пользователю
        """
        from ..models import User
        
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            return {
                "recommended_difficulty": "easy",
                "skill_level": "beginner",
                "reason": "New user",
                "games_played": 0
            }
        
        stats = AdaptiveDifficulty.get_player_stats(user.id, session)
        skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
        recommended = SKILL_TO_DIFFICULTY.get(skill_info["skill"], "medium")
        
        return {
            "recommended_difficulty": recommended,
            "skill_level": skill_info["skill"],
            "reason": skill_info["reason"],
            "games_played": skill_info.get("games_played", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "avg_skill_score": skill_info.get("avg_skill_score")
        }