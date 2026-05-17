import logging
from typing import Optional, Dict, Any, List
from sqlmodel import Session, select
from datetime import datetime
from collections import defaultdict

from ..models import User, SudokuGame, PuzzleGame

logger = logging.getLogger(__name__)

# Уровни сложности
DIFFICULTY_LEVELS = {
    "easy": 1,
    "medium": 2,
    "hard": 3
}

# Доступные сложности для каждого уровня игрока
SKILL_ACCESS = {
    "beginner": ["easy"],           # новичок - только easy
    "intermediate": ["easy", "medium"],  # средний - easy и medium
    "advanced": ["easy", "medium", "hard"]  # продвинутый - всё
}

# Какой уровень нужен для перехода
PROMOTION_REQUIREMENTS = {
    "beginner": {
        "need_games_on": "easy",      # нужно играть на easy
        "min_games": 10,              # минимум 10 игр на easy
        "min_win_rate": 70,           # минимум 70% побед
        "next_skill": "intermediate"
    },
    "intermediate": {
        "need_games_on": "medium",    # нужно играть на medium
        "min_games": 10,              # минимум 10 игр на medium
        "min_win_rate": 60,           # минимум 60% побед
        "next_skill": "advanced"
    }
}

class AdaptiveDifficulty:
    
    @staticmethod
    def get_user_skill(user_id: int, session: Session) -> str:
        """Получить сохраненный уровень навыка пользователя"""
        user = session.get(User, user_id)
        if not user or not user.skill_level:
            return "beginner"
        return user.skill_level
    
    @staticmethod
    def save_user_skill(user_id: int, skill: str, session: Session):
        """Сохранить уровень навыка пользователя"""
        user = session.get(User, user_id)
        if user:
            user.skill_level = skill
            user.updated_at = datetime.utcnow()
            session.add(user)
            session.commit()
            logger.info(f"User {user_id} skill updated to {skill}")
    
    @staticmethod
    def get_relevant_games(user_id: int, session: Session, limit: int = 15) -> List:
        """
        Получить последние игры ТОЛЬКО на разрешенных сложностях
        Игры на запрещенных сложностях игнорируются полностью
        """
        # Получаем текущий уровень игрока
        current_skill = AdaptiveDifficulty.get_user_skill(user_id, session)
        allowed_difficulties = SKILL_ACCESS.get(current_skill, ["easy"])
        
        # Получаем все игры
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
        
        # Фильтруем ТОЛЬКО игры на разрешенных сложностях
        relevant_games = []
        for game in all_games:
            if game.difficulty in allowed_difficulties:
                relevant_games.append(game)
            else:
                logger.debug(f"Ignoring game on {game.difficulty} (user skill: {current_skill})")
        
        # Возвращаем последние N игр
        return relevant_games[:limit]
    
    @staticmethod
    def calculate_win_rate(games: List) -> Dict[str, Any]:
        """Рассчитать win-rate по списку игр"""
        if not games:
            return {
                "win_rate": 0,
                "total_games": 0,
                "completed_games": 0
            }
        
        total = len(games)
        completed = sum(1 for g in games if g.is_completed)
        win_rate = (completed / total * 100) if total > 0 else 0
        
        # Статистика по сложностям
        stats_by_difficulty = defaultdict(lambda: {"total": 0, "completed": 0})
        for game in games:
            stats_by_difficulty[game.difficulty]["total"] += 1
            if game.is_completed:
                stats_by_difficulty[game.difficulty]["completed"] += 1
        
        return {
            "win_rate": round(win_rate, 2),
            "total_games": total,
            "completed_games": completed,
            "stats_by_difficulty": dict(stats_by_difficulty)
        }
    
    @staticmethod
    def update_skill_level(user_id: int, session: Session) -> Dict[str, Any]:
        """
        Обновить уровень навыка на основе последних 15 игр
        Вызывается после каждой завершенной игры
        """
        current_skill = AdaptiveDifficulty.get_user_skill(user_id, session)
        
        # Получаем последние 15 релевантных игр
        recent_games = AdaptiveDifficulty.get_relevant_games(user_id, session, limit=15)
        
        if len(recent_games) < 3:
            # Недостаточно игр для оценки
            return {
                "skill": current_skill,
                "changed": False,
                "reason": f"Not enough games ({len(recent_games)}/3)",
                "stats": AdaptiveDifficulty.calculate_win_rate(recent_games)
            }
        
        # Проверяем возможность повышения
        if current_skill in PROMOTION_REQUIREMENTS:
            req = PROMOTION_REQUIREMENTS[current_skill]
            need_difficulty = req["need_games_on"]
            
            # Считаем статистику ТОЛЬКО по нужной сложности
            games_on_need = [g for g in recent_games if g.difficulty == need_difficulty]
            stats = AdaptiveDifficulty.calculate_win_rate(games_on_need)
            
            logger.info(f"User {user_id} - Checking promotion from {current_skill}: "
                       f"played {stats['total_games']}/{req['min_games']} games on {need_difficulty}, "
                       f"win rate {stats['win_rate']}%")
            
            # Проверяем условия для повышения
            if (stats['total_games'] >= req['min_games'] and 
                stats['win_rate'] >= req['min_win_rate']):
                
                new_skill = req['next_skill']
                AdaptiveDifficulty.save_user_skill(user_id, new_skill, session)
                
                return {
                    "skill": new_skill,
                    "changed": True,
                    "from_skill": current_skill,
                    "reason": f"Mastered {need_difficulty}: {stats['completed_games']}/{stats['total_games']} wins ({stats['win_rate']:.0f}%)",
                    "stats": stats
                }
        
        # Проверяем возможность понижения (если игрок стал хуже играть)
        if current_skill == "intermediate":
            # Если проигрывает на medium больше 70% времени - понижаем
            medium_games = [g for g in recent_games if g.difficulty == "medium"]
            if len(medium_games) >= 5:
                medium_stats = AdaptiveDifficulty.calculate_win_rate(medium_games)
                if medium_stats['win_rate'] < 40:
                    AdaptiveDifficulty.save_user_skill(user_id, "beginner", session)
                    return {
                        "skill": "beginner",
                        "changed": True,
                        "from_skill": current_skill,
                        "reason": f"Struggling on medium: {medium_stats['win_rate']:.0f}% win rate",
                        "stats": medium_stats
                    }
        
        if current_skill == "advanced":
            # Если проигрывает на hard больше 70% времени - понижаем до intermediate
            hard_games = [g for g in recent_games if g.difficulty == "hard"]
            if len(hard_games) >= 5:
                hard_stats = AdaptiveDifficulty.calculate_win_rate(hard_games)
                if hard_stats['win_rate'] < 40:
                    AdaptiveDifficulty.save_user_skill(user_id, "intermediate", session)
                    return {
                        "skill": "intermediate",
                        "changed": True,
                        "from_skill": current_skill,
                        "reason": f"Struggling on hard: {hard_stats['win_rate']:.0f}% win rate",
                        "stats": hard_stats
                    }
        
        # Уровень не изменился
        all_stats = AdaptiveDifficulty.calculate_win_rate(recent_games)
        return {
            "skill": current_skill,
            "changed": False,
            "reason": f"Current level maintained",
            "stats": all_stats
        }
    
    @staticmethod
    async def get_adaptive_difficulty(
        vk_user_id: str,
        requested_difficulty: str,
        session: Session,
        auto_adjust: bool = True
    ) -> Dict[str, Any]:
        """
        Получить адаптированную сложность для игры
        """
        from ..models import User
        
        # Находим пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            # Новый игрок - всегда beginner
            return {
                "difficulty": "easy",
                "was_adjusted": requested_difficulty != "easy",
                "skill_level": "beginner",
                "reason": "New player - can only play easy",
                "allowed_difficulties": ["easy"]
            }
        
        # Получаем текущий уровень
        skill = AdaptiveDifficulty.get_user_skill(user.id, session)
        allowed = SKILL_ACCESS.get(skill, ["easy"])
        
        final_difficulty = requested_difficulty
        was_adjusted = False
        reason = ""
        
        if auto_adjust:
            if requested_difficulty not in allowed:
                # Запрошенная сложность недоступна - понижаем до максимальной доступной
                final_difficulty = allowed[-1]
                was_adjusted = True
                reason = f"Cannot play {requested_difficulty} at {skill} level, using {final_difficulty}"
            else:
                reason = f"Playing {requested_difficulty} at {skill} level"
        else:
            reason = "Auto-adjust disabled"
        
        # Получаем статистику для отображения
        recent_games = AdaptiveDifficulty.get_relevant_games(user.id, session, limit=15)
        stats = AdaptiveDifficulty.calculate_win_rate(recent_games)
        
        return {
            "difficulty": final_difficulty,
            "was_adjusted": was_adjusted,
            "skill_level": skill,
            "reason": reason,
            "allowed_difficulties": allowed,
            "stats": stats  # Статистика по последним 15 играм
        }
    
    @staticmethod
    def get_user_full_stats(user_id: int, session: Session) -> Dict[str, Any]:
        """
        Полная статистика для фронтенда (ВСЕ игры, без ограничений)
        """
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user_id)
        ).all()
        
        puzzle_games = session.exec(
            select(PuzzleGame).where(PuzzleGame.user_id == user_id)
        ).all()
        
        all_games = sudoku_games + puzzle_games
        
        if not all_games:
            return {
                "total_games": 0,
                "completed_games": 0,
                "win_rate": 0
            }
        
        total = len(all_games)
        completed = sum(1 for g in all_games if g.is_completed)
        win_rate = (completed / total * 100) if total > 0 else 0
        
        # Статистика по сложностям
        by_difficulty = defaultdict(lambda: {"total": 0, "completed": 0})
        for game in all_games:
            by_difficulty[game.difficulty]["total"] += 1
            if game.is_completed:
                by_difficulty[game.difficulty]["completed"] += 1
        
        return {
            "total_games": total,
            "completed_games": completed,
            "win_rate": round(win_rate, 2),
            "by_difficulty": dict(by_difficulty),
            "by_type": {
                "sudoku": len(sudoku_games),
                "puzzle": len(puzzle_games)
            }
        }