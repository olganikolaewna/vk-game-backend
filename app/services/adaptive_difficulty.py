# app/services/adaptive_difficulty.py - ИСПРАВЛЕННАЯ ВЕРСИЯ

import logging
from typing import Optional, Dict, Any
from sqlmodel import Session, select

from ..models import User, SudokuGame

logger = logging.getLogger(__name__)

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
        ИСПРАВЛЕННАЯ ВЕРСИЯ - правильно считает win-rate
        """
        from ..models import User
        
        # 1. Получаем пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            return {
                "difficulty": "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0
            }
        
        # 2. Получаем ВСЕ игры пользователя
        games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        
        total_games = len(games)
        if total_games == 0:
            return {
                "difficulty": "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "allowed_difficulties": ["easy"],
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0
            }
        
        # 3. Считаем завершенные игры
        completed_games_list = [g for g in games if g.is_completed]
        completed_games = len(completed_games_list)
        win_rate = round(completed_games / total_games * 100, 1) if total_games > 0 else 0
        
        # 4. Считаем игры по сложностям (только завершенные для win-rate)
        easy_games = [g for g in games if g.difficulty == "easy"]
        medium_games = [g for g in games if g.difficulty == "medium"]
        
        easy_completed = [g for g in easy_games if g.is_completed]
        medium_completed = [g for g in medium_games if g.is_completed]
        
        easy_win_rate = round(len(easy_completed) / len(easy_games) * 100, 1) if easy_games else 0
        medium_win_rate = round(len(medium_completed) / len(medium_games) * 100, 1) if medium_games else 0
        
        # 5. Считаем последние 10 ЗАВЕРШЕННЫХ игр
        completed_games_sorted = sorted(completed_games_list, key=lambda g: g.created_at, reverse=True)
        last_10_completed = completed_games_sorted[:10]
        last_10_wins = len([g for g in last_10_completed if g.is_completed])
        last_10_total = len(last_10_completed)
        last_10_win_rate = round(last_10_wins / last_10_total * 100, 1) if last_10_total > 0 else 0
        
        # 6. Определяем уровень (на основе последних 10 завершенных игр на easy)
        last_10_easy = [g for g in last_10_completed if g.difficulty == "easy"]
        last_10_easy_wins = len([g for g in last_10_easy if g.is_completed])
        last_10_easy_total = len(last_10_easy)
        last_10_easy_win_rate = round(last_10_easy_wins / last_10_easy_total * 100, 1) if last_10_easy_total > 0 else 0
        
        # Простая логика
        if len(easy_games) >= 10 and last_10_easy_win_rate >= 60:
            skill = "intermediate"
            allowed = ["easy", "medium"]
            max_difficulty = "medium"
            
            # Проверка для advanced
            last_10_medium = [g for g in last_10_completed if g.difficulty == "medium"]
            last_10_medium_wins = len([g for g in last_10_medium if g.is_completed])
            last_10_medium_total = len(last_10_medium)
            last_10_medium_win_rate = round(last_10_medium_wins / last_10_medium_total * 100, 1) if last_10_medium_total > 0 else 0
            
            if len(medium_games) >= 10 and last_10_medium_win_rate >= 60:
                skill = "advanced"
                allowed = ["easy", "medium", "hard"]
                max_difficulty = "hard"
        else:
            skill = "beginner"
            allowed = ["easy"]
            max_difficulty = "easy"
        
        # 7. Определяем финальную сложность
        final_difficulty = requested_difficulty
        was_adjusted = False
        
        if requested_difficulty not in allowed:
            final_difficulty = max_difficulty
            was_adjusted = True
        
        # 8. Логируем для отладки
        logger.info(f"=== STATS for user {vk_user_id} ===")
        logger.info(f"Total games: {total_games}, Completed: {completed_games}, Win rate: {win_rate}%")
        logger.info(f"Easy: {len(easy_games)} games, {len(easy_completed)} wins ({easy_win_rate}%)")
        logger.info(f"Medium: {len(medium_games)} games, {len(medium_completed)} wins ({medium_win_rate}%)")
        logger.info(f"Last 10 completed: {last_10_total} games, {last_10_wins} wins ({last_10_win_rate}%)")
        logger.info(f"Last 10 easy: {last_10_easy_total} games, {last_10_easy_wins} wins ({last_10_easy_win_rate}%)")
        logger.info(f"Skill: {skill}, Requested: {requested_difficulty}, Final: {final_difficulty}")
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "allowed_difficulties": allowed,
            "total_games": total_games,
            "completed_games": completed_games,
            "win_rate": win_rate,
            "stats_by_difficulty": {
                "easy": {
                    "total": len(easy_games),
                    "completed": len(easy_completed),
                    "win_rate": easy_win_rate
                },
                "medium": {
                    "total": len(medium_games),
                    "completed": len(medium_completed),
                    "win_rate": medium_win_rate
                }
            },
            "last_10_completed_games": {
                "total": last_10_total,
                "wins": last_10_wins,
                "win_rate": last_10_win_rate
            }
        }