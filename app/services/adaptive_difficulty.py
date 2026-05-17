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

# 🔥 НОВЫЕ ПОРОГИ ДЛЯ ПОВЫШЕНИЯ (более быстрый прогресс)
PROMOTION_THRESHOLDS = {
    "beginner": {
        "required_difficulty": "easy",
        "min_games": 6,           # 🔥 Было 10, стало 6
        "min_win_rate": 60,       # 🔥 Было 70, стало 60
        "next_skill": "intermediate"
    },
    "intermediate": {
        "required_difficulty": "medium",
        "min_games": 6,           # 🔥 Было 10, стало 6
        "min_win_rate": 60,       # 🔥 Было 60, осталось 60
        "next_skill": "advanced"
    }
}

# Доступные сложности для каждого скилла
SKILL_TO_ALLOWED_DIFFICULTIES = {
    "beginner": ["easy"],                    # Только easy
    "intermediate": ["easy", "medium"],      # easy и medium
    "advanced": ["easy", "medium", "hard"]   # любые
}

SKILL_TO_RECOMMENDED = {
    "beginner": "easy",
    "intermediate": "medium",
    "advanced": "hard"
}

class AdaptiveDifficulty:
    
    @staticmethod
    def get_player_stats_for_skill(user_id: int, session: Session) -> Dict[str, Any]:
        """
        🔥 ФИКС: Статистика ТОЛЬКО для определения скилла
        Считаем ТОЛЬКО игры на разрешенных сложностях для текущего уровня
        """
        # Получаем ВСЕ игры пользователя
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user_id)
        ).all()
        puzzle_games = session.exec(
            select(PuzzleGame).where(PuzzleGame.user_id == user_id)
        ).all()
        all_games = sudoku_games + puzzle_games
        
        if not all_games:
            return {
                "skill": "beginner",
                "games_on_easy": 0,
                "wins_on_easy": 0,
                "win_rate_on_easy": 0,
                "games_on_medium": 0,
                "wins_on_medium": 0,
                "win_rate_on_medium": 0,
                "total_games_all": 0
            }
        
        # 🔥 Считаем отдельно по каждой сложности (ВСЕ игры, без фильтрации)
        easy_games = [g for g in all_games if g.difficulty == "easy"]
        medium_games = [g for g in all_games if g.difficulty == "medium"]
        hard_games = [g for g in all_games if g.difficulty == "hard"]
        
        easy_wins = sum(1 for g in easy_games if g.is_completed)
        medium_wins = sum(1 for g in medium_games if g.is_completed)
        
        easy_win_rate = (easy_wins / len(easy_games) * 100) if easy_games else 0
        medium_win_rate = (medium_wins / len(medium_games) * 100) if medium_games else 0
        
        # 🔥 ОПРЕДЕЛЕНИЕ СКИЛЛА (только на основе easy и medium)
        # Начинаем с beginner
        skill = "beginner"
        
        # Проверяем возможность повышения до intermediate (на основе easy)
        if len(easy_games) >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]:
            skill = "intermediate"
        
        # Проверяем возможность повышения до advanced (на основе medium)
        if skill == "intermediate":
            if len(medium_games) >= PROMOTION_THRESHOLDS["intermediate"]["min_games"] and medium_win_rate >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]:
                skill = "advanced"
        
        logger.info(f"Player {user_id}: easy={len(easy_games)}/{easy_wins} ({easy_win_rate:.1f}%), "
                   f"medium={len(medium_games)}/{medium_wins} ({medium_win_rate:.1f}%), skill={skill}")
        
        return {
            "skill": skill,
            "games_on_easy": len(easy_games),
            "wins_on_easy": easy_wins,
            "win_rate_on_easy": round(easy_win_rate, 1),
            "games_on_medium": len(medium_games),
            "wins_on_medium": medium_wins,
            "win_rate_on_medium": round(medium_win_rate, 1),
            "games_on_hard": len(hard_games),
            "total_games_all": len(all_games),
            "easy_games_needed": max(0, PROMOTION_THRESHOLDS["beginner"]["min_games"] - len(easy_games)),
            "medium_games_needed": max(0, PROMOTION_THRESHOLDS["intermediate"]["min_games"] - len(medium_games))
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
        🔥 ОСНОВНОЙ МЕТОД
        Если игрок пытается играть на запрещенной сложности - возвращаем ошибку через was_adjusted
        """
        from ..models import User
        
        requested_difficulty = requested_difficulty.lower()
        
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            return {
                "difficulty": "easy",
                "was_adjusted": True if requested_difficulty != "easy" else False,
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "games_played": 0,
                "win_rate": 0,
                "reason": "New user"
            }
        
        # Получаем статистику и скилл
        stats = AdaptiveDifficulty.get_player_stats_for_skill(user.id, session)
        skill = stats["skill"]
        
        # Проверяем, разрешена ли запрошенная сложность
        allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"])
        is_allowed = requested_difficulty in allowed
        
        if not is_allowed and auto_adjust:
            # 🔥 ИГРА НЕ БУДЕТ СОЗДАНА! Возвращаем was_adjusted=True
            return {
                "difficulty": requested_difficulty,  # Запрошенная, но недоступная
                "was_adjusted": True,  # 🔥 Сигнал для фронтенда - игра не будет создана
                "skill_level": skill,
                "allowed_difficulties": allowed,
                "games_played": stats["games_on_easy"],
                "win_rate": stats["win_rate_on_easy"],
                "games_needed": stats["easy_games_needed"] if skill == "beginner" else stats["medium_games_needed"],
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill == "beginner" else PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"],
                "reason": f"Skill '{skill}' cannot play '{requested_difficulty}'. Need {stats['easy_games_needed'] if skill == 'beginner' else stats['medium_games_needed']} more games on {SKILL_TO_RECOMMENDED[skill]} with {PROMOTION_THRESHOLDS[skill]['min_win_rate']}% win rate"
            }
        
        # Игрок может играть на этой сложности
        return {
            "difficulty": requested_difficulty,
            "was_adjusted": False,
            "skill_level": skill,
            "allowed_difficulties": allowed,
            "games_played": stats["games_on_easy"] if skill == "beginner" else stats["games_on_medium"],
            "win_rate": stats["win_rate_on_easy"] if skill == "beginner" else stats["win_rate_on_medium"],
            "promotion_info": {
                "next_skill": PROMOTION_THRESHOLDS[skill]["next_skill"] if skill in PROMOTION_THRESHOLDS else None,
                "games_needed": stats["easy_games_needed"] if skill == "beginner" else stats["medium_games_needed"],
                "required_win_rate": PROMOTION_THRESHOLDS[skill]["min_win_rate"] if skill in PROMOTION_THRESHOLDS else 0,
                "current_win_rate": stats["win_rate_on_easy"] if skill == "beginner" else stats["win_rate_on_medium"]
            }
        }