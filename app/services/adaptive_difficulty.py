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
            "min_games": 10,
            "required_win_rate": 70,
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
    
    # Поражение считается если игра не завершена через N часов
    LOSS_AFTER_HOURS = 24
    
    @classmethod
    def get_relevant_games_with_results(cls, user_id: int, session: Session, limit: int = 15) -> Tuple[List, Dict]:
        """
        Получить последние N игр с учетом результатов.
        Возвращает: (список игр для анализа, статистика)
        """
        from ..models import SudokuGame
        
        # Получаем последние N*2 игр (чтобы набрать N релевантных)
        all_games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user_id)
            .order_by(SudokuGame.created_at.desc())
            .limit(limit * 2)
        ).all()
        
        games_with_results = []
        now = datetime.utcnow()
        
        for game in all_games:
            if len(games_with_results) >= limit:
                break
            
            if game.is_completed:
                # Победа
                games_with_results.append({
                    "id": game.id,
                    "difficulty": game.difficulty,
                    "result": "win",
                    "completed_at": game.completed_at,
                    "created_at": game.created_at
                })
            else:
                # Проверяем, не пора ли считать это поражением
                hours_passed = (now - game.created_at).total_seconds() / 3600
                if hours_passed >= cls.LOSS_AFTER_HOURS:
                    # Поражение (игра старая и не завершена)
                    games_with_results.append({
                        "id": game.id,
                        "difficulty": game.difficulty,
                        "result": "loss",
                        "completed_at": None,
                        "created_at": game.created_at
                    })
                # Игры младше LOSS_AFTER_HOURS игнорируем
        
        # Рассчитываем статистику
        stats = cls.calculate_stats_from_results(games_with_results)
        
        return games_with_results, stats
    
    @classmethod
    def calculate_stats_from_results(cls, games: List) -> Dict:
        """Рассчитать статистику из списка игр с результатами"""
        if not games:
            return {
                "total_games": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "stats_by_difficulty": {}
            }
        
        stats_by_diff = {}
        wins = 0
        losses = 0
        
        for game in games:
            diff = game["difficulty"]
            result = game["result"]
            
            if diff not in stats_by_diff:
                stats_by_diff[diff] = {"wins": 0, "losses": 0, "total": 0}
            
            stats_by_diff[diff]["total"] += 1
            
            if result == "win":
                stats_by_diff[diff]["wins"] += 1
                wins += 1
            else:
                stats_by_diff[diff]["losses"] += 1
                losses += 1
        
        # Рассчитываем винрейт для каждой сложности
        for diff in stats_by_diff:
            total = stats_by_diff[diff]["total"]
            wins_count = stats_by_diff[diff]["wins"]
            stats_by_diff[diff]["win_rate"] = (wins_count / total * 100) if total > 0 else 0
        
        total_games = len(games)
        win_rate = (wins / total_games * 100) if total_games > 0 else 0
        
        return {
            "total_games": total_games,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "stats_by_difficulty": stats_by_diff
        }
    
    @classmethod
    def get_user_stats_for_leveling(cls, user_id: int, session: Session, skill_level: str, limit: int = 15) -> Dict:
        """
        Получить статистику для определения возможности повышения уровня
        Учитывает ТОЛЬКО игры на нужной сложности
        """
        games, stats = cls.get_relevant_games_with_results(user_id, session, limit)
        
        if skill_level == "beginner":
            # Для новичка важны только игры на easy
            relevant_games = [g for g in games if g["difficulty"] == "easy"]
        elif skill_level == "intermediate":
            # Для среднего важны только игры на medium
            relevant_games = [g for g in games if g["difficulty"] == "medium"]
        else:
            relevant_games = games
        
        if not relevant_games:
            return {
                "total_games": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "games_needed": 10,
                "wins_needed": 10
            }
        
        wins = sum(1 for g in relevant_games if g["result"] == "win")
        losses = sum(1 for g in relevant_games if g["result"] == "loss")
        total = len(relevant_games)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        return {
            "total_games": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "games_needed": max(0, 10 - total),
            "wins_needed": max(0, 10 - wins)
        }
    
    @classmethod
    def get_relevant_games(cls, user_id: int, session: Session, limit: int = 15) -> List:
        """Получить последние N ЗАВЕРШЕННЫХ игр для анализа (только победы)"""
        from ..models import SudokuGame
        
        games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user_id)
            .where(SudokuGame.is_completed == True)
            .order_by(SudokuGame.completed_at.desc())
            .limit(limit)
        ).all()
        
        return games
    
    @classmethod
    def calculate_win_rate(cls, games: List) -> Dict:
        """Рассчитать винрейт по списку игр (только победы)"""
        if not games:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0,
                "stats_by_difficulty": {}
            }
        
        stats_by_diff = {}
        
        for game in games:
            diff = game.difficulty
            if diff not in stats_by_diff:
                stats_by_diff[diff] = {"total": 0}
            stats_by_diff[diff]["total"] += 1
        
        for diff in stats_by_diff:
            total = stats_by_diff[diff]["total"]
            stats_by_diff[diff]["win_rate"] = 100.0
        
        return {
            "total_games": len(games),
            "completed_games": len(games),
            "win_rate": 100.0,
            "stats_by_difficulty": stats_by_diff
        }
    
    @classmethod
    def determine_skill_level(cls, user_id: int, session: Session) -> Tuple[str, Dict]:
        """Определить уровень навыка игрока на основе игр с учетом поражений"""
        from ..models import User
        
        user = session.get(User, user_id)
        if not user:
            return "beginner", {}
        
        current_skill = user.skill_level
        
        if current_skill == "advanced":
            return "advanced", {}
        
        # Получаем статистику с учетом поражений
        stats = cls.get_user_stats_for_leveling(user_id, session, current_skill, limit=15)
        
        logger.info(f"User {user_id} ({current_skill}): {stats['total_games']} games, {stats['wins']} wins, {stats['losses']} losses, win_rate={stats['win_rate']:.1f}%")
        
        if current_skill == "beginner":
            required_win_rate = 70
            
            if stats["total_games"] >= 10 and stats["win_rate"] >= required_win_rate:
                return "intermediate", {
                    "promoted": True,
                    "from": "beginner",
                    "to": "intermediate",
                    "reason": f"Выиграно {stats['win_rate']:.0f}% из последних {stats['total_games']} игр на лёгком уровне"
                }
            
            return "beginner", {
                "promoted": False,
                "required": {
                    "easy_games_played": stats["total_games"],
                    "easy_games_needed": max(0, 10 - stats["total_games"]),
                    "required_win_rate": required_win_rate,
                    "current_win_rate": stats["win_rate"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "total_analyzed": stats["total_games"]
                }
            }
        
        elif current_skill == "intermediate":
            required_win_rate = 60
            
            if stats["total_games"] >= 10 and stats["win_rate"] >= required_win_rate:
                return "advanced", {
                    "promoted": True,
                    "from": "intermediate",
                    "to": "advanced",
                    "reason": f"Выиграно {stats['win_rate']:.0f}% из последних {stats['total_games']} игр на среднем уровне"
                }
            
            return "intermediate", {
                "promoted": False,
                "required": {
                    "medium_games_played": stats["total_games"],
                    "medium_games_needed": max(0, 10 - stats["total_games"]),
                    "required_win_rate": required_win_rate,
                    "current_win_rate": stats["win_rate"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "total_analyzed": stats["total_games"]
                }
            }
        
        return current_skill, {}
    
    @classmethod
    def update_skill_level(cls, user_id: int, session: Session) -> Dict:
        """Обновить уровень навыка игрока"""
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
            
            logger.info(f"User {user_id} promoted from {old_skill} to {new_skill}")
            
            return {
                "updated": True,
                "old_skill": old_skill,
                "new_skill": new_skill,
                "reason": promotion_info.get("reason", "Skill level updated")
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
        recent_games_limit: int = 15
    ) -> Dict:
        """Получить адаптированную сложность для игры"""
        from ..models import User
        
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
            logger.info(f"Created new user {vk_user_id} with skill_level=beginner")
        
        skill_level = client_skill or user.skill_level
        
        # Получаем статистику с учетом поражений
        stats = cls.get_user_stats_for_leveling(user.id, session, skill_level, recent_games_limit)
        
        # Получаем общую статистику для отображения
        all_games, all_stats = cls.get_relevant_games_with_results(user.id, session, recent_games_limit)
        
        thresholds = cls.SKILL_THRESHOLDS.get(skill_level, cls.SKILL_THRESHOLDS["beginner"])
        allowed_difficulties = thresholds["allowed_difficulties"]
        
        if skill_level == "beginner":
            promotion_info = {
                "easy_games_played": stats["total_games"],
                "easy_games_needed": stats["games_needed"],
                "required_win_rate": 70,
                "current_win_rate": round(stats["win_rate"], 1),
                "wins": stats["wins"],
                "losses": stats["losses"],
                "next_skill": "intermediate",
                "message": f"Сыграно {stats['total_games']}/10 игр на easy, винрейт {stats['win_rate']:.1f}% (нужно 70%)"
            }
            display_win_rate = stats["win_rate"]
            
        elif skill_level == "intermediate":
            promotion_info = {
                "medium_games_played": stats["total_games"],
                "medium_games_needed": stats["games_needed"],
                "required_win_rate": 60,
                "current_win_rate": round(stats["win_rate"], 1),
                "wins": stats["wins"],
                "losses": stats["losses"],
                "next_skill": "advanced",
                "message": f"Сыграно {stats['total_games']}/10 игр на medium, винрейт {stats['win_rate']:.1f}% (нужно 60%)"
            }
            display_win_rate = stats["win_rate"]
        else:
            promotion_info = {"message": "Вы достигли максимального уровня!"}
            display_win_rate = all_stats["win_rate"]
        
        final_difficulty = requested_difficulty
        was_adjusted = False
        reason = ""
        detailed_reason = ""
        
        if auto_adjust:
            if requested_difficulty not in allowed_difficulties:
                final_difficulty = thresholds["allowed_difficulties"][-1]
                was_adjusted = True
                reason = f"Сложность '{requested_difficulty}' недоступна для вашего уровня ({skill_level})"
                detailed_reason = f"Доступны: {', '.join(allowed_difficulties)}"
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill_level,
            "allowed_difficulties": allowed_difficulties,
            "games_analyzed": stats["total_games"],
            "total_games_all_time": all_stats["total_games"],
            "win_rate": round(display_win_rate, 1),
            "easy_win_rate": round(all_stats["stats_by_difficulty"].get("easy", {}).get("win_rate", 0), 1),
            "medium_win_rate": round(all_stats["stats_by_difficulty"].get("medium", {}).get("win_rate", 0), 1),
            "hard_win_rate": round(all_stats["stats_by_difficulty"].get("hard", {}).get("win_rate", 0), 1),
            "promotion_info": promotion_info,
            "reason": reason,
            "detailed_reason": detailed_reason,
            "requested": requested_difficulty,
            "message": f"Автоматически изменено с {requested_difficulty} на {final_difficulty}" if was_adjusted 
                      else f"Игра создана на уровне {final_difficulty}"
        }
    
    @classmethod
    def suggest_next_difficulty(cls, user_id: int, session: Session) -> Dict:
        """Предложить следующую сложность"""
        from ..models import User
        
        user = session.get(User, user_id)
        if not user:
            return {"suggestion": "easy"}
        
        stats = cls.get_user_stats_for_leveling(user.id, session, user.skill_level, 15)
        
        if user.skill_level == "beginner":
            if stats["total_games"] >= 10 and stats["win_rate"] >= 70:
                return {"suggestion": "medium", "reason": "Вы готовы к среднему уровню!", "confidence": "high"}
            else:
                return {"suggestion": "easy", "reason": f"Нужно {stats['games_needed']} игр и {max(0, 70 - stats['win_rate']):.0f}% побед", "confidence": "low"}
        
        elif user.skill_level == "intermediate":
            if stats["total_games"] >= 10 and stats["win_rate"] >= 60:
                return {"suggestion": "hard", "reason": "Вы готовы к сложному уровню!", "confidence": "high"}
            else:
                return {"suggestion": "medium", "reason": f"Нужно {stats['games_needed']} игр и {max(0, 60 - stats['win_rate']):.0f}% побед", "confidence": "low"}
        
        return {"suggestion": "hard", "reason": "Максимальный уровень", "confidence": "high"}