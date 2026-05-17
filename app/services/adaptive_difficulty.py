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

# Пороги для повышения уровня
PROMOTION_THRESHOLDS = {
    "beginner": {
        "required_difficulty": "easy",
        "min_games": 10,           # минимум игр для повышения
        "min_win_rate": 70,        # минимальный win-rate (в процентах)
        "next_skill": "intermediate"
    },
    "intermediate": {
        "required_difficulty": "medium",
        "min_games": 10,           # минимум игр на medium
        "min_win_rate": 60,        # минимальный win-rate на medium
        "next_skill": "advanced"
    }
}

class AdaptiveDifficulty:
    
    @staticmethod
    def get_player_stats(user_id: int, session: Session, recent_games_limit: int = 20) -> Dict[str, Any]:
        """Получить статистику игрока (только последние N игр)"""
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
        
        recent_games = all_games[:recent_games_limit]
        total_games = len(recent_games)
        
        if total_games == 0:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "games_by_difficulty": {},
                "last_game_difficulty": None,
                "games_analyzed": 0,
                "total_games_all_time": len(all_games)
            }
        
        completed_games = [g for g in recent_games if g.is_completed]
        completed_count = len(completed_games)
        win_rate = (completed_count / total_games) * 100
        
        games_by_difficulty = {}
        for game in recent_games:
            diff = game.difficulty
            if diff not in games_by_difficulty:
                games_by_difficulty[diff] = {"total": 0, "completed": 0}
            games_by_difficulty[diff]["total"] += 1
            if game.is_completed:
                games_by_difficulty[diff]["completed"] += 1
        
        last_game = recent_games[0].difficulty if recent_games else None
        
        return {
            "total_games": total_games,
            "completed_games": completed_count,
            "win_rate": round(win_rate, 2),
            "games_by_difficulty": games_by_difficulty,
            "last_game_difficulty": last_game,
            "games_analyzed": total_games,
            "total_games_all_time": len(all_games)
        }
    
    @staticmethod
    def calculate_skill_level(stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        Рассчитать уровень скилла на основе статистики последних игр
        Использует строгую прогрессию: нужно мастерство на текущем уровне
        """
        total_games = stats.get("total_games", 0)
        win_rate = stats.get("win_rate", 0)
        games_by_diff = stats.get("games_by_difficulty", {})
        
        # Недостаточно данных
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Only {total_games} recent games played, assuming beginner",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # Получаем статистику по easy и medium
        easy_stats = games_by_diff.get("easy", {"total": 0, "completed": 0})
        medium_stats = games_by_diff.get("medium", {"total": 0, "completed": 0})
        hard_stats = games_by_diff.get("hard", {"total": 0, "completed": 0})
        
        easy_win_rate = (easy_stats["completed"] / easy_stats["total"] * 100) if easy_stats["total"] > 0 else 0
        medium_win_rate = (medium_stats["completed"] / medium_stats["total"] * 100) if medium_stats["total"] > 0 else 0
        
        # Логика определения скилла (с повышением)
        # Начинаем с beginner, проверяем можно ли повысить
        
        # Проверка: достаточно ли игр на easy для повышения до intermediate?
        if (easy_stats["total"] >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and 
            easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]):
            # Игрок мастер на easy, проверяем не пора ли стать intermediate
            return {
                "skill": "intermediate",
                "source": "auto_detected",
                "confidence": 85,
                "reason": f"Mastered easy: {easy_stats['completed']}/{easy_stats['total']} wins ({easy_win_rate:.0f}%)",
                "games_played": total_games,
                "win_rate": win_rate,
                "promotion_from": "beginner"
            }
        
        # Проверка: достаточно ли игр на medium для повышения до advanced?
        if (medium_stats["total"] >= PROMOTION_THRESHOLDS["intermediate"]["min_games"] and 
            medium_win_rate >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]):
            # Игрок мастер на medium, повышаем до advanced
            return {
                "skill": "advanced",
                "source": "auto_detected",
                "confidence": 85,
                "reason": f"Mastered medium: {medium_stats['completed']}/{medium_stats['total']} wins ({medium_win_rate:.0f}%)",
                "games_played": total_games,
                "win_rate": win_rate,
                "promotion_from": "intermediate"
            }
        
        # Если есть победы на hard, но нет мастерства на medium - всё равно intermediate
        if hard_stats["total"] > 0 and medium_stats["total"] < PROMOTION_THRESHOLDS["intermediate"]["min_games"]:
            return {
                "skill": "intermediate",
                "source": "auto_detected",
                "confidence": 70,
                "reason": f"Playing hard but need {PROMOTION_THRESHOLDS['intermediate']['min_games']} medium games for promotion",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # Если есть игры на medium, но мало побед
        if medium_stats["total"] >= 5 and medium_win_rate < 40:
            return {
                "skill": "beginner",
                "source": "low_medium_win_rate",
                "confidence": 80,
                "reason": f"Struggling with medium ({medium_win_rate:.0f}% win rate), demoted to beginner",
                "games_played": total_games,
                "win_rate": win_rate
            }
        
        # По умолчанию - beginner
        return {
            "skill": "beginner",
            "source": "auto_detected",
            "confidence": 75,
            "reason": f"Need {PROMOTION_THRESHOLDS['beginner']['min_games']} easy games with {PROMOTION_THRESHOLDS['beginner']['min_win_rate']}% win rate to advance",
            "games_played": total_games,
            "win_rate": win_rate,
            "easy_win_rate": round(easy_win_rate, 1),
            "medium_win_rate": round(medium_win_rate, 1),
            "easy_games_needed": max(0, PROMOTION_THRESHOLDS['beginner']['min_games'] - easy_stats["total"]),
            "medium_games_needed": max(0, PROMOTION_THRESHOLDS['intermediate']['min_games'] - medium_stats["total"])
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
        Основной метод - возвращает адаптированную сложность
        
        Новая логика (строгая прогрессия):
        - Новичок (beginner): может играть ТОЛЬКО easy
        - Средний (intermediate): может играть easy и medium
        - Продвинутый (advanced): может играть любые уровни
        
        Чтобы повысить уровень:
        - beginner → intermediate: 10 побед на easy (70% win-rate)
        - intermediate → advanced: 10 побед на medium (60% win-rate)
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
                "difficulty": "easy",  # Новый игрок может играть только easy
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
            # Определяем скилл автоматически по последним играм
            stats = AdaptiveDifficulty.get_player_stats(user.id, session, recent_games_limit)
            skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
            
            logger.info(f"Player {vk_user_id}: analyzed {stats['games_analyzed']} recent games "
                       f"(total: {stats['total_games_all_time']}), "
                       f"win_rate: {stats['win_rate']}%, skill: {skill_info['skill']}, "
                       f"reason: {skill_info['reason']}")
        
        skill = skill_info["skill"]
        final_difficulty = requested_difficulty
        was_adjusted = False
        adjust_reason = ""
        
        # ========== СТРОГАЯ АДАПТАЦИЯ ==========
        if auto_adjust:
            # Проверяем, может ли игрок играть на запрошенной сложности
            can_play, reason = AdaptiveDifficulty.can_play_difficulty(skill, requested_difficulty)
            
            if not can_play:
                # Понижаем до максимально доступной сложности
                allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"])
                final_difficulty = allowed[-1]  # Берем максимальную доступную
                was_adjusted = True
                adjust_reason = reason + f" Adjusted down to '{final_difficulty}'"
            else:
                # Игрок может играть на этой сложности
                recommended = SKILL_TO_RECOMMENDED.get(skill, "easy")
                
                if requested_difficulty == recommended:
                    adjust_reason = f"Skill '{skill}' matches requested difficulty '{requested_difficulty}'"
                elif DIFFICULTY_LEVELS[requested_difficulty] < DIFFICULTY_LEVELS[recommended]:
                    # Игрок выбрал уровень ниже - уважаем выбор
                    adjust_reason = f"Skill '{skill}' recommends '{recommended}', but player chose easier '{requested_difficulty}' - respecting choice"
                else:
                    adjust_reason = f"Skill '{skill}' allows '{requested_difficulty}' (max: {allowed[-1]})"
        else:
            adjust_reason = f"Auto-adjust disabled, using requested difficulty: {requested_difficulty}"
        
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
            "requested_difficulty": requested_difficulty,
            "allowed_difficulties": SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ["easy"]),
            "recommended": SKILL_TO_RECOMMENDED.get(skill, "easy"),
            "games_analyzed": skill_info.get("games_played", 0),
            "total_games_all_time": skill_info.get("total_games_all_time", 0),
            "promotion_info": {
                "next_skill": "intermediate" if skill == "beginner" else ("advanced" if skill == "intermediate" else None),
                "required_games": PROMOTION_THRESHOLDS["beginner"]["min_games"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_games"] if skill == "intermediate" else 0),
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"] if skill == "intermediate" else 0),
                "easy_win_rate": skill_info.get("easy_win_rate"),
                "medium_win_rate": skill_info.get("medium_win_rate"),
                "easy_games_needed": skill_info.get("easy_games_needed"),
                "medium_games_needed": skill_info.get("medium_games_needed")
            }
        }
    
    @staticmethod
    async def get_recommended_difficulty(
        vk_user_id: str,
        session: Session,
        recent_games_limit: int = 20
    ) -> Dict[str, Any]:
        """
        Получить только рекомендуемую сложность
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
                "games_played": 0,
                "allowed_difficulties": ["easy"]
            }
        
        stats = AdaptiveDifficulty.get_player_stats(user.id, session, recent_games_limit)
        skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
        recommended = SKILL_TO_RECOMMENDED.get(skill_info["skill"], "easy")
        allowed = SKILL_TO_ALLOWED_DIFFICULTIES.get(skill_info["skill"], ["easy"])
        
        return {
            "recommended_difficulty": recommended,
            "skill_level": skill_info["skill"],
            "reason": skill_info["reason"],
            "games_played": skill_info.get("games_played", 0),
            "games_analyzed": stats.get("games_analyzed", 0),
            "total_games_all_time": stats.get("total_games_all_time", 0),
            "win_rate": skill_info.get("win_rate", 0),
            "allowed_difficulties": allowed,
            "promotion_info": {
                "next_skill": "intermediate" if skill_info["skill"] == "beginner" else ("advanced" if skill_info["skill"] == "intermediate" else None),
                "required_games": PROMOTION_THRESHOLDS["beginner"]["min_games"] if skill_info["skill"] == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_games"] if skill_info["skill"] == "intermediate" else 0),
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill_info["skill"] == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"] if skill_info["skill"] == "intermediate" else 0),
                "easy_win_rate": skill_info.get("easy_win_rate"),
                "medium_win_rate": skill_info.get("medium_win_rate")
            }
        }