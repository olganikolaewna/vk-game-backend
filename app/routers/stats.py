# app/routers/stats.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
import logging

from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame
from ..services.adaptive_difficulty import AdaptiveDifficulty

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Stats & Leaderboard"])

# ========== СТАТИСТИКА ПЛАТФОРМЫ ==========

@router.get("/stats/global")
async def get_global_stats(session: Session = Depends(get_session)):
    """Общая статистика платформы"""
    try:
        # Общее количество пользователей
        total_users = session.exec(select(func.count(User.id))).one()
        
        # Общее количество игр
        total_sudoku = session.exec(select(func.count(SudokuGame.id))).one()
        total_puzzles = session.exec(select(func.count(PuzzleGame.id))).one()
        
        # Игры за сегодня
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        sudoku_today = session.exec(
            select(func.count(SudokuGame.id)).where(SudokuGame.created_at >= today_start)
        ).one()
        puzzles_today = session.exec(
            select(func.count(PuzzleGame.id)).where(PuzzleGame.created_at >= today_start)
        ).one()
        
        # Подсчёт завершённых игр
        completed_sudoku = session.exec(
            select(func.count(SudokuGame.id)).where(SudokuGame.is_completed == True)
        ).one()
        completed_puzzles = session.exec(
            select(func.count(PuzzleGame.id)).where(PuzzleGame.is_completed == True)
        ).one()
        
        return {
            "total_users": total_users,
            "total_games": total_sudoku + total_puzzles,
            "completed_games": completed_sudoku + completed_puzzles,
            "games_by_type": {
                "sudoku": {
                    "total": total_sudoku,
                    "completed": completed_sudoku,
                    "today": sudoku_today
                },
                "puzzle": {
                    "total": total_puzzles,
                    "completed": completed_puzzles,
                    "today": puzzles_today
                }
            },
            "games_today": sudoku_today + puzzles_today,
        }
    except Exception as e:
        logger.error(f"Error in global stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch global stats")


# ========== ТАБЛИЦА ЛИДЕРОВ ==========

@router.get("/leaderboard")
async def get_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session)
):
    """Топ игроков по рейтингу с пагинацией"""
    try:
        # Получаем пользователей с их рейтингом и количеством игр
        users = session.exec(
            select(User)
            .order_by(User.rating.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        
        result = []
        for idx, user in enumerate(users):
            # Считаем игры через один запрос к каждой таблице
            sudoku_count = session.exec(
                select(func.count(SudokuGame.id)).where(SudokuGame.user_id == user.id)
            ).one()
            puzzle_count = session.exec(
                select(func.count(PuzzleGame.id)).where(PuzzleGame.user_id == user.id)
            ).one()
            
            result.append({
                "rank": offset + idx + 1,
                "vk_user_id": user.vk_user_id,
                "username": user.username,
                "rating": user.rating,
                "games_played": sudoku_count + puzzle_count,
                "sudoku_games": sudoku_count,
                "puzzle_games": puzzle_count
            })
        
        # Общее количество пользователей для пагинации
        total_users = session.exec(select(func.count(User.id))).one()
        
        return {
            "total": total_users,
            "offset": offset,
            "limit": limit,
            "leaderboard": result
        }
    except Exception as e:
        logger.error(f"Error in leaderboard: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch leaderboard")


@router.get("/leaderboard/daily")
async def get_daily_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session)
):
    """Топ игроков за сегодня по количеству побед"""
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        # Оптимизированный запрос через JOIN
        query = """
            SELECT 
                u.vk_user_id,
                u.username,
                COUNT(CASE WHEN sg.is_completed AND sg.completed_at >= %s THEN 1 END) as sudoku_wins,
                COUNT(CASE WHEN pg.is_completed AND pg.completed_at >= %s THEN 1 END) as puzzle_wins
            FROM users u
            LEFT JOIN sudoku_games sg ON u.id = sg.user_id
            LEFT JOIN puzzle_games pg ON u.id = pg.user_id
            GROUP BY u.id, u.vk_user_id, u.username
            HAVING COUNT(CASE WHEN sg.is_completed AND sg.completed_at >= %s THEN 1 END) > 0
                OR COUNT(CASE WHEN pg.is_completed AND pg.completed_at >= %s THEN 1 END) > 0
            ORDER BY (sudoku_wins + puzzle_wins) DESC
            LIMIT %s
        """
        
        result = session.exec(
            query,
            params=[today_start, today_start, today_start, today_start, limit]
        )
        
        results = []
        for row in result:
            results.append({
                "vk_user_id": row[0],
                "username": row[1],
                "wins_today": row[2] + row[3],
                "sudoku_wins": row[2],
                "puzzle_wins": row[3]
            })
        
        return results
        
    except Exception as e:
        logger.error(f"Error in daily leaderboard: {e}")
        # Fallback на простой метод если сложный запрос не работает
        try:
            users = session.exec(select(User)).all()
            
            results = []
            for user in users:
                completed_today = 0
                completed_today += session.exec(
                    select(func.count(SudokuGame.id))
                    .where(
                        SudokuGame.user_id == user.id, 
                        SudokuGame.is_completed == True, 
                        SudokuGame.completed_at >= today_start
                    )
                ).one()
                completed_today += session.exec(
                    select(func.count(PuzzleGame.id))
                    .where(
                        PuzzleGame.user_id == user.id, 
                        PuzzleGame.is_completed == True, 
                        PuzzleGame.completed_at >= today_start
                    )
                ).one()
                
                if completed_today > 0:
                    results.append({
                        "vk_user_id": user.vk_user_id,
                        "username": user.username,
                        "wins_today": completed_today
                    })
            
            results.sort(key=lambda x: x["wins_today"], reverse=True)
            return results[:limit]
        except Exception as fallback_error:
            logger.error(f"Fallback also failed: {fallback_error}")
            raise HTTPException(status_code=500, detail="Failed to fetch daily leaderboard")


@router.get("/leaderboard/weekly")
async def get_weekly_leaderboard(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_session)
):
    """Топ игроков за последние 7 дней"""
    try:
        week_start = datetime.now() - timedelta(days=7)
        
        users = session.exec(select(User)).all()
        
        results = []
        for user in users:
            completed_weekly = 0
            completed_weekly += session.exec(
                select(func.count(SudokuGame.id))
                .where(
                    SudokuGame.user_id == user.id,
                    SudokuGame.is_completed == True,
                    SudokuGame.completed_at >= week_start
                )
            ).one()
            completed_weekly += session.exec(
                select(func.count(PuzzleGame.id))
                .where(
                    PuzzleGame.user_id == user.id,
                    PuzzleGame.is_completed == True,
                    PuzzleGame.completed_at >= week_start
                )
            ).one()
            
            if completed_weekly > 0:
                results.append({
                    "vk_user_id": user.vk_user_id,
                    "username": user.username,
                    "wins_weekly": completed_weekly
                })
        
        results.sort(key=lambda x: x["wins_weekly"], reverse=True)
        return results[:limit]
        
    except Exception as e:
        logger.error(f"Error in weekly leaderboard: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch weekly leaderboard")


# ========== ДОПОЛНИТЕЛЬНЫЕ СТАТИСТИКИ ==========

@router.get("/stats/games/completed")
async def get_completed_games_stats(
    days: int = Query(30, ge=1, le=365),
    session: Session = Depends(get_session)
):
    """Статистика завершённых игр за последние N дней"""
    try:
        start_date = datetime.now() - timedelta(days=days)
        
        # Группировка по дням
        sudoku_daily = session.exec(
            select(
                func.date(SudokuGame.completed_at).label("date"),
                func.count(SudokuGame.id).label("count")
            )
            .where(
                SudokuGame.is_completed == True,
                SudokuGame.completed_at >= start_date
            )
            .group_by(func.date(SudokuGame.completed_at))
            .order_by(func.date(SudokuGame.completed_at))
        ).all()
        
        puzzle_daily = session.exec(
            select(
                func.date(PuzzleGame.completed_at).label("date"),
                func.count(PuzzleGame.id).label("count")
            )
            .where(
                PuzzleGame.is_completed == True,
                PuzzleGame.completed_at >= start_date
            )
            .group_by(func.date(PuzzleGame.completed_at))
            .order_by(func.date(PuzzleGame.completed_at))
        ).all()
        
        return {
            "period_days": days,
            "sudoku_completions": [{"date": str(row[0]), "count": row[1]} for row in sudoku_daily],
            "puzzle_completions": [{"date": str(row[0]), "count": row[1]} for row in puzzle_daily],
            "total_sudoku": sum(row[1] for row in sudoku_daily),
            "total_puzzle": sum(row[1] for row in puzzle_daily)
        }
        
    except Exception as e:
        logger.error(f"Error in completed games stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch completion stats")


@router.get("/stats/user/{vk_user_id}")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """
    Статистика игрока для фронтенда
    """
    try:
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Полная статистика (все игры)
        full_stats = AdaptiveDifficulty.get_user_full_stats(user.id, session)
        
        # Статистика по последним 15 играм (для отображения текущего win-rate)
        recent_games = AdaptiveDifficulty.get_relevant_games(user.id, session, limit=15)
        recent_stats = AdaptiveDifficulty.calculate_win_rate(recent_games)
        
        # Информация о прогрессе
        skill_info = {
            "current": user.skill_level,
            "next_level": None,
            "progress": {}
        }
        
        # Информация о необходимом для повышения
        if user.skill_level == "beginner":
            skill_info["next_level"] = "intermediate"
            easy_games = [g for g in recent_games if g.difficulty == "easy"]
            skill_info["progress"] = {
                "need_games": 10,
                "current_games": len(easy_games),
                "need_win_rate": 70,
                "current_win_rate": AdaptiveDifficulty.calculate_win_rate(easy_games)["win_rate"]
            }
        elif user.skill_level == "intermediate":
            skill_info["next_level"] = "advanced"
            medium_games = [g for g in recent_games if g.difficulty == "medium"]
            skill_info["progress"] = {
                "need_games": 10,
                "current_games": len(medium_games),
                "need_win_rate": 60,
                "current_win_rate": AdaptiveDifficulty.calculate_win_rate(medium_games)["win_rate"]
            }
        
        return {
            "vk_user_id": user.vk_user_id,
            "username": user.username,
            "rating": user.rating,
            "skill_level": user.skill_level,
            # Полная статистика (все игры за всё время)
            "total_games_all_time": full_stats["total_games"],
            "total_completed_all_time": full_stats["completed_games"],
            "total_win_rate_all_time": full_stats["win_rate"],
            # Статистика по последним 15 играм (текущая форма)
            "recent_games_count": recent_stats["total_games"],
            "recent_win_rate": recent_stats["win_rate"],
            "recent_completed": recent_stats["completed_games"],
            "stats_by_difficulty_recent": recent_stats["stats_by_difficulty"],
            # Информация о прогрессе
            "promotion_info": skill_info,
            # Доступные сложности
            "allowed_difficulties": ["easy"] if user.skill_level == "beginner" 
                                    else (["easy", "medium"] if user.skill_level == "intermediate" 
                                    else ["easy", "medium", "hard"])
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in user stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/game/completed/{game_id}")
async def on_game_completed(
    game_id: int,
    game_type: str,  # "sudoku" or "puzzle"
    session: Session = Depends(get_session)
):
    """
    Вызывается когда игрок завершил игру
    Обновляет уровень навыка
    """
    try:
        from ..services.adaptive_difficulty import AdaptiveDifficulty
        
        # Находим игру
        if game_type == "sudoku":
            game = session.get(SudokuGame, game_id)
        else:
            game = session.get(PuzzleGame, game_id)
        
        if not game or not game.is_completed:
            return {"status": "ignored", "reason": "Game not found or not completed"}
        
        # Обновляем уровень навыка
        if game_type == "sudoku":
            skill_update = AdaptiveDifficulty.update_skill_level(game.user_id, session)
            
            # Дополнительно: получаем следующую рекомендуемую сложность
            next_suggestion = AdaptiveDifficulty.suggest_next_difficulty(game.user_id, session)
            
            return {
                "status": "updated",
                "skill_update": skill_update,
                "next_difficulty_suggestion": next_suggestion
            }
        
        return {"status": "ignored", "reason": "Puzzle games don't affect skill level yet"}
        
    except Exception as e:
        logger.error(f"Error in game completed callback: {e}")
        return {"status": "error", "reason": str(e)}

   