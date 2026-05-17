# app/services/adaptive_difficulty.py - ПОЛНОСТЬЮ РАБОЧАЯ ВЕРСИЯ

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
        "max_difficulty": "easy"
    },
    "intermediate": {
        "min_games": 10,
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
        Простая рабочая версия
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
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "games_needed": 10,
                "required_win_rate": 60
            }
        
        # Получаем ВСЕ игры пользователя
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        
        # Подсчитываем статистику по ВСЕМ играм
        total_games = len(sudoku_games)
        completed_games = sum(1 for g in sudoku_games if g.is_completed)
        win_rate_all = (completed_games / total_games * 100) if total_games > 0 else 0
        
        # Статистика по сложностям
        easy_games = [g for g in sudoku_games if g.difficulty == "easy"]
        medium_games = [g for g in sudoku_games if g.difficulty == "medium"]
        hard_games = [g for g in sudoku_games if g.difficulty == "hard"]
        
        easy_total = len(easy_games)
        easy_completed = sum(1 for g in easy_games if g.is_completed)
        easy_win_rate = (easy_completed / easy_total * 100) if easy_total > 0 else 0
        
        medium_total = len(medium_games)
        medium_completed = sum(1 for g in medium_games if g.is_completed)
        medium_win_rate = (medium_completed / medium_total * 100) if medium_total > 0 else 0
        
        # Статистика по последним 10 играм для повышения
        last_10_games = sudoku_games[:10]  # Первые 10 (самые новые)
        last_10_wins = sum(1 for g in last_10_games if g.is_completed)
        last_10_total = len(last_10_games)
        last_10_win_rate = (last_10_wins / last_10_total * 100) if last_10_total > 0 else 0
        
        # Определяем скилл на основе последних 10 игр на easy
        last_10_easy = [g for g in last_10_games if g.difficulty == "easy"]
        last_10_easy_wins = sum(1 for g in last_10_easy if g.is_completed)
        last_10_easy_total = len(last_10_easy)
        last_10_easy_win_rate = (last_10_easy_wins / last_10_easy_total * 100) if last_10_easy_total > 0 else 0
        
        # Простая логика определения скилла
        if easy_total >= 10 and last_10_easy_win_rate >= 60:
            skill = "intermediate"
            if medium_total >= 10:
                last_10_medium = [g for g in last_10_games if g.difficulty == "medium"]
                last_10_medium_wins = sum(1 for g in last_10_medium if g.is_completed)
                last_10_medium_total = len(last_10_medium)
                last_10_medium_win_rate = (last_10_medium_wins / last_10_medium_total * 100) if last_10_medium_total > 0 else 0
                if last_10_medium_win_rate >= 60:
                    skill = "advanced"
        else:
            skill = "beginner"
        
        # Доступные сложности
        allowed_difficulties = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"])
        max_difficulty = PROMOTION_THRESHOLDS[skill]["max_difficulty"]
        
        # Автоматическая адаптация
        final_difficulty = requested_difficulty
        was_adjusted = False
        
        if DIFFICULTY_LEVELS.get(requested_difficulty, 1) > DIFFICULTY_LEVELS.get(max_difficulty, 1):
            final_difficulty = max_difficulty
            was_adjusted = True
        
        # Расчет прогресса
        if skill == "beginner":
            needed_for_next = max(0, 10 - last_10_easy_total)
            wins_needed = max(0, 6 - last_10_easy_wins)
            current_win_rate = last_10_easy_win_rate
            games_analyzed = last_10_easy_total
        elif skill == "intermediate":
            last_10_medium = [g for g in last_10_games if g.difficulty == "medium"]
            medium_wins = sum(1 for g in last_10_medium if g.is_completed)
            medium_total = len(last_10_medium)
            needed_for_next = max(0, 10 - medium_total)
            wins_needed = max(0, 6 - medium_wins)
            current_win_rate = (medium_wins / medium_total * 100) if medium_total > 0 else 0
            games_analyzed = medium_total
        else:
            needed_for_next = 0
            wins_needed = 0
            current_win_rate = 100
            games_analyzed = 10
        
        logger.info(f"Player {vk_user_id}: total_games={total_games}, skill={skill}, last10_win_rate={last_10_win_rate:.1f}%")
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "allowed_difficulties": allowed_difficulties,
            
            # Полная статистика (ВСЕ игры)
            "total_games": total_games,
            "completed_games": completed_games,
            "win_rate_all": round(win_rate_all, 1),
            
            # Статистика по сложностям
            "stats_by_difficulty": {
                "easy": {
                    "total": easy_total,
                    "completed": easy_completed,
                    "win_rate": round(easy_win_rate, 1)
                },
                "medium": {
                    "total": medium_total,
                    "completed": medium_completed,
                    "win_rate": round(medium_win_rate, 1)
                },
                "hard": {
                    "total": len(hard_games),
                    "completed": sum(1 for g in hard_games if g.is_completed),
                    "win_rate": round((sum(1 for g in hard_games if g.is_completed) / len(hard_games) * 100), 1) if hard_games else 0
                }
            },
            
            # Статистика для повышения (последние игры)
            "promotion_stats": {
                "last_10_games_total": last_10_total,
                "last_10_games_wins": last_10_wins,
                "last_10_win_rate": round(last_10_win_rate, 1),
                "required_win_rate": 60,
                "wins_needed_for_next_level": wins_needed,
                "games_needed_for_next_level": needed_for_next,
                "current_progress": f"{last_10_easy_wins if skill == 'beginner' else (last_10_medium_wins if skill == 'intermediate' else 10)}/10"
            },
            
            "max_difficulty": max_difficulty
        }