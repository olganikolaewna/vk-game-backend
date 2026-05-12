# app/routers/stats.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
import logging

from ..db import get_session
from ..models import User, SudokuGame, PuzzleGame

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["Stats & Leaderboard"])

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


@router.get("/stats/user/{vk_user_id}")
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Статистика конкретного игрока"""
    try:
        # Приводим к строке для безопасности
        vk_user_id_str = str(vk_user_id)
        
        user = session.exec(
            select(User).where(User.vk_user_id == vk_user_id_str)
        ).first()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Получаем игры пользователя
        sudoku_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        puzzle_games = session.exec(
            select(PuzzleGame).where(PuzzleGame.user_id == user.id)
        ).all()
        
        all_games = sudoku_games + puzzle_games
        completed = [g for g in all_games if g.is_completed]
        
        # Подсчёт очков по типам игр
        sudoku_completed = [g for g in sudoku_games if g.is_completed]
        puzzle_completed = [g for g in puzzle_games if g.is_completed]
        
        # Среднее время завершения (если есть completed_at)
        avg_completion_time = None
        games_with_time = [g for g in completed if g.completed_at and g.created_at]
        if games_with_time:
            total_time = sum(
                (g.completed_at - g.created_at).total_seconds() 
                for g in games_with_time
            )
            avg_completion_time = total_time / len(games_with_time) / 60  # в минутах
        
        return {
            "vk_user_id": user.vk_user_id,
            "username": user.username,
            "rating": user.rating,
            "total_games": len(all_games),
            "completed_games": len(completed),
            "win_rate": round(len(completed)/len(all_games) * 100, 2) if all_games else 0,
            "games_by_type": {
                "sudoku": {
                    "total": len(sudoku_games),
                    "completed": len(sudoku_completed)
                },
                "puzzle": {
                    "total": len(puzzle_games),
                    "completed": len(puzzle_completed)
                }
            },
            "avg_completion_time_minutes": round(avg_completion_time, 1) if avg_completion_time else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in user stats for {vk_user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch user stats")


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