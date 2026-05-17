# app/services/adaptive_difficulty.py
import logging
from typing import Optional, Dict, Any, List
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

# Пороги для повышения уровня (основаны на мастерстве, а не общем win-rate)
PROMOTION_THRESHOLDS = {
    "beginner": {
        "required_difficulty": "easy",
        "min_games": 10,           # минимум игр на easy
        "min_win_rate": 70,        # минимальный win-rate на easy (в процентах)
        "next_skill": "intermediate"
    },
    "intermediate": {
        "required_difficulty": "medium",
        "min_games": 10,           # минимум игр на medium
        "min_win_rate": 60,        # минимальный win-rate на medium (в процентах)
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
    def get_difficulty_mastery(stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        Рассчитывает мастерство на каждой сложности отдельно
        Это позволяет видеть реальный прогресс без влияния попыток на других сложностях
        """
        games_by_diff = stats.get("games_by_difficulty", {})
        
        mastery = {}
        for difficulty in ["easy", "medium", "hard"]:
            diff_stats = games_by_diff.get(difficulty, {"total": 0, "completed": 0})
            total = diff_stats.get("total", 0)
            completed = diff_stats.get("completed", 0)
            win_rate = (completed / total * 100) if total > 0 else 0
            
            # Определяем уровень мастерства
            if total == 0:
                mastery_level = "not_played"
            elif win_rate >= 70:
                mastery_level = "excellent"
            elif win_rate >= 50:
                mastery_level = "good"
            elif win_rate >= 30:
                mastery_level = "learning"
            else:
                mastery_level = "struggling"
            
            mastery[difficulty] = {
                "total_games": total,
                "completed_games": completed,
                "win_rate": round(win_rate, 2),
                "mastery_level": mastery_level,
                "games_needed_for_next_level": max(0, PROMOTION_THRESHOLDS.get("beginner", {}).get("min_games", 10) - total) if difficulty == "easy" else (
                    max(0, PROMOTION_THRESHOLDS.get("intermediate", {}).get("min_games", 10) - total) if difficulty == "medium" else 0
                )
            }
        
        return mastery
    
    @staticmethod
    def calculate_skill_level(stats: Dict[str, Any]) -> Dict[str, Any]:
        """
        Рассчитать уровень скилла на основе МАСТЕРСТВА на каждой сложности
        Больше не наказывает за попытки играть на более высоких уровнях
        """
        total_games = stats.get("total_games", 0)
        win_rate = stats.get("win_rate", 0)
        
        # Получаем мастерство на каждой сложности
        mastery = AdaptiveDifficulty.get_difficulty_mastery(stats)
        
        easy_mastery = mastery.get("easy", {})
        medium_mastery = mastery.get("medium", {})
        hard_mastery = mastery.get("hard", {})
        
        easy_win_rate = easy_mastery.get("win_rate", 0)
        easy_games = easy_mastery.get("total_games", 0)
        medium_win_rate = medium_mastery.get("win_rate", 0)
        medium_games = medium_mastery.get("total_games", 0)
        
        # Недостаточно данных
        if total_games < 3:
            return {
                "skill": "beginner",
                "source": "insufficient_data",
                "confidence": 60,
                "reason": f"Only {total_games} recent games played, assuming beginner",
                "games_played": total_games,
                "win_rate": win_rate,
                "mastery": mastery
            }
        
        # ЛОГИКА ПОВЫШЕНИЯ (только на основе мастерства на текущем уровне)
        
        # Проверка: достаточно ли мастерства на easy для повышения до intermediate?
        if (easy_games >= PROMOTION_THRESHOLDS["beginner"]["min_games"] and 
            easy_win_rate >= PROMOTION_THRESHOLDS["beginner"]["min_win_rate"]):
            # Игрок мастер на easy, повышаем до intermediate
            return {
                "skill": "intermediate",
                "source": "mastered_easy",
                "confidence": 85,
                "reason": f"Mastered easy: {easy_win_rate:.0f}% win rate over {easy_games} games",
                "games_played": total_games,
                "win_rate": win_rate,
                "mastery": mastery,
                "promotion_from": "beginner"
            }
        
        # Проверка: достаточно ли мастерства на medium для повышения до advanced?
        if (medium_games >= PROMOTION_THRESHOLDS["intermediate"]["min_games"] and 
            medium_win_rate >= PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]):
            # Игрок мастер на medium, повышаем до advanced
            return {
                "skill": "advanced",
                "source": "mastered_medium",
                "confidence": 85,
                "reason": f"Mastered medium: {medium_win_rate:.0f}% win rate over {medium_games} games",
                "games_played": total_games,
                "win_rate": win_rate,
                "mastery": mastery,
                "promotion_from": "intermediate"
            }
        
        # Если есть игры на medium, но мастерство не достигнуто
        if medium_games > 0 and medium_win_rate < PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"]:
            return {
                "skill": "intermediate" if medium_games >= 3 else "beginner",
                "source": "learning_medium",
                "confidence": 70,
                "reason": f"Learning medium: {medium_win_rate:.0f}% win rate over {medium_games} games. Need {PROMOTION_THRESHOLDS['intermediate']['min_games']} games with {PROMOTION_THRESHOLDS['intermediate']['min_win_rate']}% win rate for advanced",
                "games_played": total_games,
                "win_rate": win_rate,
                "mastery": mastery
            }
        
        # Если есть игры на hard, но мастерство на medium не достигнуто
        if hard_mastery.get("total_games", 0) > 0 and medium_win_rate < 50:
            return {
                "skill": "intermediate",
                "source": "exploring_hard",
                "confidence": 65,
                "reason": f"Exploring hard but need to master medium first. Medium: {medium_win_rate:.0f}% over {medium_games} games",
                "games_played": total_games,
                "win_rate": win_rate,
                "mastery": mastery
            }
        
        # По умолчанию - beginner с пояснением требований
        easy_games_needed = max(0, PROMOTION_THRESHOLDS["beginner"]["min_games"] - easy_games)
        
        if easy_games_needed > 0:
            reason = f"Need {easy_games_needed} more games on easy with {PROMOTION_THRESHOLDS['beginner']['min_win_rate']}% win rate to reach intermediate"
        else:
            reason = f"Need {PROMOTION_THRESHOLDS['beginner']['min_win_rate']}% win rate on easy. Current: {easy_win_rate:.0f}% over {easy_games} games"
        
        return {
            "skill": "beginner",
            "source": "auto_detected",
            "confidence": 75,
            "reason": reason,
            "games_played": total_games,
            "win_rate": win_rate,
            "mastery": mastery,
            "easy_win_rate": round(easy_win_rate, 1),
            "medium_win_rate": round(medium_win_rate, 1),
            "easy_games_needed": easy_games_needed,
            "medium_games_needed": max(0, PROMOTION_THRESHOLDS["intermediate"]["min_games"] - medium_games)
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
        
        Новая логика (раздельный win-rate по сложностям):
        - Новичок (beginner): может играть ТОЛЬКО easy
        - Средний (intermediate): может играть easy и medium
        - Продвинутый (advanced): может играть любые уровни
        
        Чтобы повысить уровень:
        - beginner → intermediate: 10 игр на easy с 70% win-rate
        - intermediate → advanced: 10 игр на medium с 60% win-rate
        
        Важно: Win-rate считается отдельно для каждой сложности,
        попытки играть на более высоких уровнях не влияют на мастерство на текущем
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
                "allowed_difficulties": ["easy"],
                "mastery": {
                    "easy": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"},
                    "medium": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"},
                    "hard": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"}
                }
            }
        
        # Если клиент прислал скилл - используем его
        if client_skill:
            skill_info = {
                "skill": client_skill,
                "source": "client",
                "confidence": 100,
                "reason": "Client provided skill",
                "games_played": None,
                "win_rate": None,
                "mastery": {}
            }
        else:
            # Определяем скилл автоматически по последним играм
            stats = AdaptiveDifficulty.get_player_stats(user.id, session, recent_games_limit)
            skill_info = AdaptiveDifficulty.calculate_skill_level(stats)
            
            logger.info(f"Player {vk_user_id}: analyzed {stats['games_analyzed']} recent games "
                       f"(total: {stats['total_games_all_time']}), "
                       f"overall_win_rate: {stats['win_rate']}%, skill: {skill_info['skill']}, "
                       f"reason: {skill_info['reason']}")
            
            # Логируем мастерство по сложностям
            if "mastery" in skill_info:
                for diff, mastery in skill_info["mastery"].items():
                    logger.info(f"  {diff}: {mastery['win_rate']:.0f}% ({mastery['completed_games']}/{mastery['total_games']}) - {mastery['mastery_level']}")
        
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
                    adjust_reason = f"Skill '{skill}' allows '{requested_difficulty}' (max: {SKILL_TO_ALLOWED_DIFFICULTIES.get(skill, ['easy'])[-1]})"
        else:
            adjust_reason = f"Auto-adjust disabled, using requested difficulty: {requested_difficulty}"
        
        # Получаем мастерство для ответа
        mastery = skill_info.get("mastery", {})
        if not mastery and not client_skill:
            # Если мастерства нет, получаем его отдельно
            stats = AdaptiveDifficulty.get_player_stats(user.id, session, recent_games_limit)
            mastery = AdaptiveDifficulty.get_difficulty_mastery(stats)
        
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
            "mastery": mastery,  # Добавляем мастерство по сложностям в ответ
            "promotion_info": {
                "next_skill": "intermediate" if skill == "beginner" else ("advanced" if skill == "intermediate" else None),
                "required_games": PROMOTION_THRESHOLDS["beginner"]["min_games"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_games"] if skill == "intermediate" else 0),
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"] if skill == "intermediate" else 0),
                "required_difficulty": "easy" if skill == "beginner" else ("medium" if skill == "intermediate" else None),
                "easy_mastery": mastery.get("easy", {}),
                "medium_mastery": mastery.get("medium", {}),
                "hard_mastery": mastery.get("hard", {})
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
                "allowed_difficulties": ["easy"],
                "mastery": {
                    "easy": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"},
                    "medium": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"},
                    "hard": {"total_games": 0, "completed_games": 0, "win_rate": 0, "mastery_level": "not_played"}
                }
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
            "mastery": skill_info.get("mastery", {}),
            "promotion_info": {
                "next_skill": "intermediate" if skill_info["skill"] == "beginner" else ("advanced" if skill_info["skill"] == "intermediate" else None),
                "required_games": PROMOTION_THRESHOLDS["beginner"]["min_games"] if skill_info["skill"] == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_games"] if skill_info["skill"] == "intermediate" else 0),
                "required_win_rate": PROMOTION_THRESHOLDS["beginner"]["min_win_rate"] if skill_info["skill"] == "beginner" else (PROMOTION_THRESHOLDS["intermediate"]["min_win_rate"] if skill_info["skill"] == "intermediate" else 0),
                "required_difficulty": "easy" if skill_info["skill"] == "beginner" else ("medium" if skill_info["skill"] == "intermediate" else None),
                "easy_win_rate": skill_info.get("easy_win_rate"),
                "medium_win_rate": skill_info.get("medium_win_rate")
            }
        }