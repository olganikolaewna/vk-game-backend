# app/services/adaptive_difficulty.py
from sqlmodel import Session, select, func
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging
import json

logger = logging.getLogger(__name__)


class AdaptiveDifficulty:
    """Адаптивная система подбора сложности для Судоку"""
    
    # Пороги для перехода между уровнями
    SKILL_THRESHOLDS = {
        "beginner": {
            "next": "intermediate",
            "min_games": 10,           # Минимум игр для перехода
            "required_win_rate": 70,   # Нужный процент побед
            "allowed_difficulties": ["easy"]
        },
        "intermediate": {
            "next": "advanced",
            "min_games": 10,
            "required_win_rate": 60,
            "allowed_difficulties": ["easy", "medium"]
        },
        "advanced": {
            "next": None,
            "min_games": 0,
            "required_win_rate": 0,
            "allowed_difficulties": ["easy", "medium", "hard"]
        }
    }
    
    # Сложность по умолчанию для каждого уровня навыка
    DEFAULT_DIFFICULTY = {
        "beginner": "easy",
        "intermediate": "medium",
        "advanced": "hard"
    }
    
    @classmethod
    def get_user_stats(cls, user_id: int, session: Session, limit: int = None) -> Dict:
        """Получить статистику игрока по Судоку"""
        from ..models import SudokuGame
        
        query = select(SudokuGame).where(SudokuGame.user_id == user_id)
        
        if limit:
            query = query.order_by(SudokuGame.created_at.desc()).limit(limit)
        else:
            query = query.order_by(SudokuGame.created_at.desc())
        
        games = session.exec(query).all()
        
        if not games:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "games_by_difficulty": {},
                "recent_games": []
            }
        
        # Статистика по сложностям
        games_by_diff = {}
        completed_by_diff = {}
        
        for game in games:
            diff = game.difficulty
            games_by_diff[diff] = games_by_diff.get(diff, 0) + 1
            if game.is_completed:
                completed_by_diff[diff] = completed_by_diff.get(diff, 0) + 1
        
        # Рассчитываем win rate по каждой сложности
        win_rate_by_diff = {}
        for diff, total in games_by_diff.items():
            completed = completed_by_diff.get(diff, 0)
            win_rate_by_diff[diff] = (completed / total * 100) if total > 0 else 0
        
        total_completed = sum(completed_by_diff.values())
        total_games = len(games)
        
        return {
            "total_games": total_games,
            "completed_games": total_completed,
            "win_rate": (total_completed / total_games * 100) if total_games > 0 else 0,
            "games_by_difficulty": games_by_diff,
            "win_rate_by_difficulty": win_rate_by_diff,
            "recent_games": games[:20]  # последние 20 игр
        }
    
    @classmethod
    def get_user_full_stats(cls, user_id: int, session: Session) -> Dict:
        """Получить полную статистику (все игры за всё время)"""
        from ..models import SudokuGame
        
        games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user_id)
        ).all()
        
        total_games = len(games)
        completed_games = sum(1 for g in games if g.is_completed)
        
        return {
            "total_games": total_games,
            "completed_games": completed_games,
            "win_rate": (completed_games / total_games * 100) if total_games > 0 else 0,
            "stats_by_difficulty": cls.get_stats_by_difficulty(games)
        }
    
    @classmethod
    def get_stats_by_difficulty(cls, games: List) -> Dict:
        """Статистика по сложностям"""
        stats = {}
        for game in games:
            diff = game.difficulty
            if diff not in stats:
                stats[diff] = {"total": 0, "completed": 0}
            stats[diff]["total"] += 1
            if game.is_completed:
                stats[diff]["completed"] += 1
        
        for diff in stats:
            total = stats[diff]["total"]
            completed = stats[diff]["completed"]
            stats[diff]["win_rate"] = (completed / total * 100) if total > 0 else 0
        
        return stats
    
    @classmethod
    def get_relevant_games(cls, user_id: int, session: Session, limit: int = 20) -> List:
        """Получить последние N игр для анализа"""
        from ..models import SudokuGame
        
        games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user_id)
            .order_by(SudokuGame.created_at.desc())
            .limit(limit)
        ).all()
        
        return games
    
    @classmethod
    def calculate_win_rate(cls, games: List) -> Dict:
        """Рассчитать винрейт по списку игр"""
        if not games:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "stats_by_difficulty": {}
            }
        
        stats_by_diff = {}
        completed = 0
        
        for game in games:
            diff = game.difficulty
            if diff not in stats_by_diff:
                stats_by_diff[diff] = {"total": 0, "completed": 0}
            stats_by_diff[diff]["total"] += 1
            if game.is_completed:
                stats_by_diff[diff]["completed"] += 1
                completed += 1
        
        for diff in stats_by_diff:
            total = stats_by_diff[diff]["total"]
            comp = stats_by_diff[diff]["completed"]
            stats_by_diff[diff]["win_rate"] = (comp / total * 100) if total > 0 else 0
        
        return {
            "total_games": len(games),
            "completed_games": completed,
            "win_rate": (completed / len(games) * 100) if games else 0,
            "stats_by_difficulty": stats_by_diff
        }
    
    @classmethod
    def determine_skill_level(cls, user_id: int, session: Session) -> Tuple[str, Dict]:
        """
        Определить уровень навыка игрока на основе его игр
        
        Returns:
            (skill_level, promotion_info)
        """
        from ..models import User, SudokuGame
        
        # Получаем пользователя
        user = session.get(User, user_id)
        if not user:
            return "beginner", {}
        
        current_skill = user.skill_level
        
        # Если уже advanced, не понижаем
        if current_skill == "advanced":
            return "advanced", {}
        
        # Получаем последние игры нужного уровня
        games = cls.get_relevant_games(user_id, session, limit=20)
        
        if current_skill == "beginner":
            # Для перехода на intermediate нужно:
            # 1. Сыграть минимум 10 игр на easy
            # 2. Win rate на easy >= 70%
            easy_games = [g for g in games if g.difficulty == "easy"]
            easy_games_count = len(easy_games)
            
            if easy_games_count >= 10:
                easy_win_rate = cls.calculate_win_rate(easy_games)["win_rate"]
                
                if easy_win_rate >= 70:
                    return "intermediate", {
                        "promoted": True,
                        "from": "beginner",
                        "to": "intermediate",
                        "reason": f"Выиграно {easy_win_rate:.0f}% игр на лёгком уровне (нужно 70%)"
                    }
            
            return "beginner", {
                "promoted": False,
                "required": {
                    "easy_games_needed": max(0, 10 - easy_games_count),
                    "required_win_rate": 70,
                    "current_win_rate": cls.calculate_win_rate(easy_games)["win_rate"] if easy_games else 0
                }
            }
        
        elif current_skill == "intermediate":
            # Для перехода на advanced нужно:
            # 1. Сыграть минимум 10 игр на medium
            # 2. Win rate на medium >= 60%
            medium_games = [g for g in games if g.difficulty == "medium"]
            medium_games_count = len(medium_games)
            
            if medium_games_count >= 10:
                medium_win_rate = cls.calculate_win_rate(medium_games)["win_rate"]
                
                if medium_win_rate >= 60:
                    return "advanced", {
                        "promoted": True,
                        "from": "intermediate",
                        "to": "advanced",
                        "reason": f"Выиграно {medium_win_rate:.0f}% игр на среднем уровне (нужно 60%)"
                    }
            
            return "intermediate", {
                "promoted": False,
                "required": {
                    "medium_games_needed": max(0, 10 - medium_games_count),
                    "required_win_rate": 60,
                    "current_win_rate": cls.calculate_win_rate(medium_games)["win_rate"] if medium_games else 0
                }
            }
        
        return current_skill, {}
    
    @classmethod
    def update_skill_level(cls, user_id: int, session: Session) -> Dict:
        """
        Обновить уровень навыка игрока на основе его игр
        
        Returns:
            Информация об обновлении
        """
        from ..models import User
        
        user = session.get(User, user_id)
        if not user:
            return {"error": "User not found"}
        
        old_skill = user.skill_level
        new_skill, promotion_info = cls.determine_skill_level(user_id, session)
        
        if new_skill != old_skill:
            user.skill_level = new_skill
            session.add(user)
            session.commit()
            
            return {
                "updated": True,
                "old_skill": old_skill,
                "new_skill": new_skill,
                "reason": promotion_info.get("reason", "Skill level updated based on performance")
            }
        
        return {
            "updated": False,
            "current_skill": old_skill,
            "progress": promotion_info.get("required", {})
        }
    
    @classmethod
    async def get_adaptive_difficulty(
        cls,
        vk_user_id: str,
        requested_difficulty: str,
        session: Session,
        client_skill: Optional[str] = None,
        auto_adjust: bool = True,
        recent_games_limit: int = 20
    ) -> Dict:
        """
        Получить адаптированную сложность для игры
        
        Args:
            vk_user_id: VK ID пользователя
            requested_difficulty: Запрошенная сложность
            session: Сессия БД
            client_skill: Уровень навыка (если передан с клиента)
            auto_adjust: Автоматически корректировать сложность
            recent_games_limit: Количество последних игр для анализа
        
        Returns:
            Dict с информацией об адаптации
        """
        from ..models import User
        
        # Получаем или создаём пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            user = User(
                vk_user_id=str(vk_user_id),
                username=f"Player_{str(vk_user_id)[:5]}",
                skill_level="beginner"
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        
        # Определяем реальный уровень навыка
        skill_level = client_skill or user.skill_level
        
        # Получаем последние игры
        games = cls.get_relevant_games(user.id, session, recent_games_limit)
        recent_stats = cls.calculate_win_rate(games)
        
        # Получаем разрешённые сложности
        thresholds = cls.SKILL_THRESHOLDS.get(skill_level, cls.SKILL_THRESHOLDS["beginner"])
        allowed_difficulties = thresholds["allowed_difficulties"]
        
        # Базовая информация о прогрессе
        promotion_info = {}
        
        if skill_level == "beginner":
            easy_games = [g for g in games if g.difficulty == "easy"]
            easy_win_rate = cls.calculate_win_rate(easy_games)["win_rate"] if easy_games else 0
            promotion_info = {
                "easy_games_played": len(easy_games),
                "easy_games_needed": max(0, 10 - len(easy_games)),
                "required_win_rate": 70,
                "current_win_rate": easy_win_rate,
                "next_skill": "intermediate"
            }
        elif skill_level == "intermediate":
            medium_games = [g for g in games if g.difficulty == "medium"]
            medium_win_rate = cls.calculate_win_rate(medium_games)["win_rate"] if medium_games else 0
            promotion_info = {
                "medium_games_played": len(medium_games),
                "medium_games_needed": max(0, 10 - len(medium_games)),
                "required_win_rate": 60,
                "current_win_rate": medium_win_rate,
                "next_skill": "advanced"
            }
        
        # Определяем итоговую сложность
        final_difficulty = requested_difficulty
        was_adjusted = False
        reason = ""
        detailed_reason = ""
        
        if auto_adjust:
            # Если запрошенная сложность не разрешена
            if requested_difficulty not in allowed_difficulties:
                final_difficulty = thresholds["allowed_difficulties"][-1]  # Берём максимальную разрешённую
                was_adjusted = True
                reason = f"Сложность '{requested_difficulty}' недоступна для вашего уровня ({skill_level})"
                detailed_reason = f"Ваш уровень: {skill_level}. Доступны: {', '.join(allowed_difficulties)}"
            
            # Адаптация на основе винрейта (если игрок слишком сильный для своего уровня)
            elif skill_level == "beginner" and requested_difficulty == "easy":
                easy_win_rate = recent_stats["stats_by_difficulty"].get("easy", {}).get("win_rate", 0)
                if easy_win_rate >= 80 and recent_stats["total_games"] >= 5:
                    # Игрок слишком хорош для лёгкого, повышаем до medium
                    if "medium" in allowed_difficulties:
                        final_difficulty = "medium"
                        was_adjusted = True
                        reason = "Вы слишком хороши для лёгкого уровня!"
                        detailed_reason = f"Ваш винрейт на лёгком: {easy_win_rate:.0f}%"
            
            elif skill_level == "intermediate" and requested_difficulty == "medium":
                medium_win_rate = recent_stats["stats_by_difficulty"].get("medium", {}).get("win_rate", 0)
                if medium_win_rate >= 75 and recent_stats["total_games"] >= 5:
                    if "hard" in allowed_difficulties:
                        final_difficulty = "hard"
                        was_adjusted = True
                        reason = "Вы слишком хороши для среднего уровня!"
                        detailed_reason = f"Ваш винрейт на среднем: {medium_win_rate:.0f}%"
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill_level,
            "allowed_difficulties": allowed_difficulties,
            "games_analyzed": len(games),
            "total_games_all_time": recent_stats["total_games"],
            "win_rate": recent_stats["win_rate"],
            "easy_win_rate": recent_stats["stats_by_difficulty"].get("easy", {}).get("win_rate"),
            "medium_win_rate": recent_stats["stats_by_difficulty"].get("medium", {}).get("win_rate"),
            "hard_win_rate": recent_stats["stats_by_difficulty"].get("hard", {}).get("win_rate"),
            "promotion_info": promotion_info,
            "reason": reason,
            "detailed_reason": detailed_reason,
            "requested": requested_difficulty,
            "message": f"Автоматически изменено с {requested_difficulty} на {final_difficulty}" if was_adjusted 
                      else f"Игра создана на уровне {final_difficulty}"
        }
    
    @classmethod
    def suggest_next_difficulty(cls, user_id: int, session: Session) -> Dict:
        """
        Предложить следующую сложность на основе статистики
        """
        from ..models import User
        
        user = session.get(User, user_id)
        if not user:
            return {"suggestion": "easy"}
        
        stats = cls.get_user_stats(user_id, session, limit=20)
        
        if user.skill_level == "beginner":
            easy_win_rate = stats["win_rate_by_difficulty"].get("easy", 0)
            easy_games = stats["games_by_difficulty"].get("easy", 0)
            
            if easy_games >= 10 and easy_win_rate >= 70:
                return {
                    "suggestion": "medium",
                    "reason": "Вы готовы перейти на средний уровень!",
                    "confidence": "high"
                }
            elif easy_win_rate >= 80 and easy_games >= 5:
                return {
                    "suggestion": "medium",
                    "reason": "Вы хорошо справляетесь с лёгким уровнем. Попробуйте средний!",
                    "confidence": "medium"
                }
            else:
                return {
                    "suggestion": "easy",
                    "reason": f"Продолжайте тренироваться на лёгком. Нужно {max(0, 10 - easy_games)} игр и {70 - easy_win_rate:.0f}% побед до повышения.",
                    "confidence": "low"
                }
        
        elif user.skill_level == "intermediate":
            medium_win_rate = stats["win_rate_by_difficulty"].get("medium", 0)
            medium_games = stats["games_by_difficulty"].get("medium", 0)
            
            if medium_games >= 10 and medium_win_rate >= 60:
                return {
                    "suggestion": "hard",
                    "reason": "Вы готовы перейти на сложный уровень!",
                    "confidence": "high"
                }
            elif medium_win_rate >= 75 and medium_games >= 5:
                return {
                    "suggestion": "hard",
                    "reason": "Вы хорошо справляетесь со средним уровнем. Попробуйте сложный!",
                    "confidence": "medium"
                }
            else:
                return {
                    "suggestion": "medium",
                    "reason": f"Продолжайте тренироваться на среднем. Нужно {max(0, 10 - medium_games)} игр и {60 - medium_win_rate:.0f}% побед до повышения.",
                    "confidence": "low"
                }
        
        else:
            return {
                "suggestion": "hard",
                "reason": "Вы на высшем уровне мастерства!",
                "confidence": "high"
            }