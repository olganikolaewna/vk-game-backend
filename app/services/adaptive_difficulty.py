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

LEVEL_TO_DIFFICULTY = {v: k for k, v in DIFFICULTY_LEVELS.items()}

# Доступные сложности для каждого скилла (строгая прогрессия)
SKILL_TO_ALLOWED_DIFFICULTIES = {
    "beginner": ["easy"],                    # Только easy
    "intermediate": ["easy", "medium"],      # easy и medium
    "advanced": ["easy", "medium", "hard"]   # любые
}

# Рекомендуемая сложность
SKILL_TO_RECOMMENDED = {
    "beginner": "easy",
    "intermediate": "medium",
    "advanced": "hard"
}

# Пороги для повышения уровня (только для рекомендуемой сложности!)
PROMOTION_THRESHOLDS = {
    "beginner": {
        "required_difficulty": "easy",
        "min_games": 10,
        "min_win_rate": 70,
        "next_skill": "intermediate"
    },
    "intermediate": {
        "required_difficulty": "medium",
        "min_games": 10,
        "min_win_rate": 60,
        "next_skill": "advanced"
    }
}

class AdaptiveDifficulty:
    
    @staticmethod
    def get_player_stats_for_skill_calculation(user_id: int, session: Session, recent_games_limit: int = 20) -> Dict[str, Any]:
        """
        🔥 ФИКС: Статистика ТОЛЬКО для определения скилла
        Учитываются ТОЛЬКО игры на РЕКОМЕНДОВАННОЙ сложности для текущего уровня
        Игры на других сложностях НЕ влияют на скилл!
        """
        # Получаем все игры пользователя
        sudoku_games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user_id)
            .order_by(SudokuGame.created_at.desc())
        ).all()
        
        puzzle_games = session.exec(
            select(PuzzleGame)
            .where(PuzzleGame.user_id == user_id)
            .order_by(PuzzleGame.created_at.desc())
        ).all()
        
        all_games = sudoku_games + puzzle_games
        all_games.sort(key=lambda g: g.created_at, reverse=True)
        
        total_games_all_time = len(all_games)
        
        if total_games_all_time == 0:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "games_by_difficulty": {},
                "total_games_all_time": 0,
                "games_for_skill": []
            }
        
        # 🔥 Определяем примерный скилл на основе игр на easy (начальная оценка)
        # Смотрим только игры на easy для начальной оценки
        easy_games = [g for g in all_games if g.difficulty == "easy"]
        easy_completed = sum(1 for g in easy_games if g.is_completed)
        easy_total = len(easy_games)
        easy_win_rate = (easy_completed / easy_total * 100) if easy_total > 0 else 0
        
        # Начальная оценка скилла
        if easy_total >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]:
            # Игрок показал мастерство на easy, значит минимум intermediate
            current_skill_estimate = "intermediate"
        else:
            current_skill_estimate = "beginner"
        
        # 🔥 Для расчёта скилла используем ТОЛЬКО игры на РЕКОМЕНДОВАННОЙ сложности для текущей оценки
        # Это ключевое исправление!
        recommended_difficulty = SKILL_TO_RECOMMENDED.get(current_skill_estimate, "easy")
        
        # Берем только игры на рекомендуемой сложности
        relevant_games = [g for g in all_games if g.difficulty == recommended_difficulty]
        relevant_games = relevant_games[:recent_games_limit]  # Последние N игр
        
        total_games = len(relevant_games)
        completed_games = sum(1 for g in relevant_games if g.is_completed)
        win_rate = (completed_games / total_games * 100) if total_games > 0 else 0
        
        # Статистика по сложностям
        games_by_difficulty = {}
        for game in all_games[:recent_games_limit]:
            diff = game.difficulty
            if diff not in games_by_difficulty:
                games_by_difficulty[diff] = {"total": 0, "completed": 0}
            games_by_difficulty[diff]["total"] += 1
            if game.is_completed:
                games_by_difficulty[diff]["completed"] += 1
        
        return {
            "total_games": total_games,  # Игры только на рекомендуемой сложности
            "completed_games": completed_games,
            "win_rate": round(win_rate, 2),
            "games_by_difficulty": games_by_difficulty,
            "total_games_all_time": total_games_all_time,
            "games_for_skill": relevant_games,
            "recommended_difficulty_used": recommended_difficulty,
            "easy_stats": {
                "total": easy_total,
                "completed": easy_completed,
                "win_rate": round(easy_win_rate, 2)
            }
        }
    
    @staticmethod
    def calculate_skill_level(stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        🔥 ФИКС: Рассчитать уровень скилла
        Скилл определяется ТОЛЬКО по играм на рекомендуемой сложности
        Игры на других сложностях НЕ влияют на понижение скилла!
        """
        total_games = stats.get("total_games", 0)
        win_rate = stats.get("win_rate", 0)
        
        # Недостаточно данных
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Need at least 3 games on recommended difficulty. Current: {total_games} games",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # Проверка для повышения до intermediate (на основе easy игр)
        easy_stats = stats.get("easy_stats", {})
        easy_total = easy_stats.get("total", 0)
        easy_win_rate = easy_stats.get("win_rate", 0)
        
        if (easy_total >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and 
            easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]):
            return {
                "skill": "intermediate",
                "source": "auto_detected",
                "confidence": 85,
                "reason": f"Mastered easy: {easy_stats['completed']}/{easy_total} wins ({easy_win_rate:.0f}%)",
                "games_played": easy_total,
                "win_rate": easy_win_rate
            }
        
        # Проверка для повышения до advanced (на основе medium игр)
        # Для этого нужно, чтобы игрок уже был intermediate
        medium_games = [g for g in stats.get("games_for_skill", []) if g.difficulty == "medium"]
        medium_total = len(medium_games)
        medium_completed = sum(1 for g in medium_games if g.is_completed)
        medium_win_rate = (medium_completed / medium_total * 100) if medium_total > 0 else 0
        
        if (medium_total >= PROMOTION_THRESHOLDS["intermediate"]["min_games"] and 
            medium_win_rate >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]):
            return {
                "skill": "advanced",
                "source": "auto_detected",
                "confidence": 85,
                "reason": f"Mastered medium: {medium_completed}/{medium_total} wins ({medium_win_rate:.0f}%)",
                "games_played": medium_total,
                "win_rate": medium_win_rate
            }
        
        # По умолчанию - beginner (никогда не понижаем скилл из-за игр на других сложностях!)
        return {
            "skill": "beginner",
            "source": "auto_detected",
            "confidence": 75,
            "reason": f"Need {PROMOTION_THRESHOLDS['beginner']['min_games'] - easy_total} more easy games with {PROMOTION_THRESHOLDS['beginner']['min_win_rate']}% win rate to advance",
            "games_played": easy_total,
            "win_rate": easy_win_rate,
            "easy_win_rate": round(easy_win_rate, 1),
            "easy_games_needed": max(0, PROMOTION_THRESHOLDS['beginner']['min_games'] - easy_total),
            "easy_games_completed": easy_total,
            "easy_wins": easy_stats.get("completed", 0)
        }
    
    @staticmethod
    def can_play_difficulty(skill: str, requested_difficulty: str) -> tuple[bool, str]:
        """
        Проверяет, может ли игрок с данным скиллом играть на запрошенной сложности
        """
        allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"])
        
        if requested_difficulty in allowed:
            return True, f"Skill '{skill}' allows '{requested_difficulty}'"
        else:
            max_allowed = allowed[-1] if allowed else "easy"
            return False, f"Skill '{skill}' cannot play '{requested_difficulty}', max allowed: {max_allowed}"
    
    @staticmethod
    async def get_adaptive_difficulty(
        vk_user_id: str,
        requested_difficulty: str,
        session: Session,
        client_skill: Optional[str] = None,
        auto_adjust: bool = True,
        recent_games_limit: int = 20
    ) -> Dict[str, Any]:
        """
        🔥 ОСНОВНОЙ МЕТОД: Возвращает адаптированную сложность
        
        Новая логика:
        1. Скилл игрока НИКОГДА не понижается от игры на более сложных уровнях
        2. Скилл определяется ТОЛЬКО по играм на рекомендуемой сложности
        3. Игрок может играть на ЛЮБОЙ разрешённой сложности без понижения скилла
        4. Количество игр для статистики скилла - ТОЛЬКО игры на рекомендуемой сложности
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
                "difficulty": "easy",
                "was_adjusted": True if requested_difficulty != "easy" else False,
                "skill_level": "beginner",
                "skill_source": "default",
                "confidence": 100,
                "reason": "New user, can only play easy difficulty",
                "games_played": 0,
                "win_rate": 0,
                "requested_difficulty": requested_difficulty,
                "allowed_difficulties": ["easy"]
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
            # Определяем скилл ТОЛЬКО по играм на рекомендуемой сложности
            stats = AdaptiveDifficulty.get_player_stats_for_skill_calculation(user.id, session, recent_games_limit)
            skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
            
            logger.info(f"Player {vk_user_id}: skill={skill_info['skill']}, "
                       f"games_on_recommended={stats['total_games']}, "
                       f"win_rate_on_recommended={stats['win_rate']}%, "
                       f"total_all_games={stats['total_games_all_time']}")
        
        skill = skill_info["skill"]
        final_difficulty = requested_difficulty
        was_adjusted = False
        adjust_reason = ""
        
        # Адаптация сложности (но НЕ скилла!)
        if auto_adjust:
            can_play, reason = AdaptiveDifficulty.can_play_difficulty(skill, requested_difficulty)
            
            if not can_play:
                allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"])
                final_difficulty = allowed[-1]
                was_adjusted = True
                adjust_reason = reason + f" Adjusted down to '{final_difficulty}'"
                
                logger.info(f"Adjusted difficulty for {vk_user_id}: requested {requested_difficulty} -> {final_difficulty} (skill: {skill})")
            else:
                # Игрок может играть на этой сложности
                if requested_difficulty == SKILL_TO_RECOMMENDED.get(skill, "easy"):
                    adjust_reason = f"Skill '{skill}' matches requested difficulty '{requested_difficulty}'"
                else:
                    adjust_reason = f"Skill '{skill}' allows '{requested_difficulty}' (player chose {'easier' if DIFFICULTY_LEVELS[requested_difficulty] < DIFFICULTY_LEVELS[SKILL_TO_RECOMMENDED.get(skill, 'easy')] else 'harder'} difficulty)"
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "skill_source": skill_info["source"],
            "confidence": skill_info["confidence"],
            "reason": adjust_reason,
            "detailed_reason": skill_info["reason"],
            "games_played": skill_info.get("games_played", 0),
            "completed_games": skill_info.get("completed_games", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "requested_difficulty": requested_difficulty,
            "allowed_difficulties": SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"]),
            "recommended": SKILL_TO_RECOMMENDED.get(skill, "easy"),
            "total_games_all_time": stats.get("total_games_all_time", 0),
            "promotion_info": {
                "next_skill": "intermediate" if skill == "beginner" else ("advanced" if skill == "intermediate" else None),
                "required_games": PROMOTION_THRESHOLDS["beginner"]["min_games"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_games"] if skill == "intermediate" else 0),
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"] if skill == "intermediate" else 0),
                "easy_win_rate": skill_info.get("easy_win_rate"),
                "easy_games_needed": skill_info.get("easy_games_needed"),
                "easy_games_completed": skill_info.get("easy_games_completed"),
                "easy_wins": skill_info.get("easy_wins")
            }
        }
    
    @staticmethod
    async def get_recommended_difficulty(
        vk_user_id: str,
        session: Session,
        recent_games_limit: int = 20
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
                "reason": "New user",
                "games_played": 0,
                "allowed_difficulties": ["easy"]
            }
        
        stats = AdaptiveDifficulty.get_player_stats_for_skill_calculation(user.id, session, recent_games_limit)
        skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
        recommended = SKILL_TO_RECOMMENDED.get(skill_info["skill"], "easy")
        allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill_info["skill"], ["easy"])
        
        return {
            "recommended_difficulty": recommended,
            "skill_level": skill_info["skill"],
            "reason": skill_info["reason"],
            "games_played": skill_info.get("games_played", 0),
            "total_games_all_time": stats.get("total_games_all_time", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "allowed_difficulties": allowed
        }