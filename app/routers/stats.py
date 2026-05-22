# app/routers/stats.py
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlmodel import Session, select, func
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any
import logging

from ..db import get_session
from ..models import User, SudokuGame
from ..services.adaptive_difficulty import AdaptiveDifficulty

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Stats & Leaderboard"])

# ========== СТАТИСТИКА ПЛАТФОРМЫ ==========

@router.get("/global")
async def get_global_stats(session: Session = Depends(get_session)):
    """Общая статистика платформы"""
    try:
        total_users = session.exec(select(func.count(User.id))).one()
        total_sudoku = session.exec(select(func.count(SudokuGame.id))).one()
        
        return {
            "total_users": total_users,
            "total_games": total_sudoku,
            "status": "ok"
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
            
            result.append({
                "rank": offset + idx + 1,
                "vk_user_id": user.vk_user_id,
                "username": user.username,
                "rating": user.rating,
                "games_played": sudoku_count,
                "sudoku_games": sudoku_count,
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
    """Топ игроков за сегодня по количеству побед (только судоку)"""
    try:
        today_start = datetime.combine(date.today(), datetime.min.time())
        
        query = """
            SELECT 
                u.vk_user_id,
                u.username,
                COUNT(CASE WHEN sg.is_completed AND sg.completed_at >= %s THEN 1 END) as sudoku_wins
            FROM users u
            LEFT JOIN sudoku_games sg ON u.id = sg.user_id
            GROUP BY u.id, u.vk_user_id, u.username
            HAVING COUNT(CASE WHEN sg.is_completed AND sg.completed_at >= %s THEN 1 END) > 0
            ORDER BY sudoku_wins DESC
            LIMIT %s
        """
        
        result = session.exec(
            query,
            params=[today_start, today_start, limit]  # ← 3 параметра
        )
        
        results = []
        for row in result:
            results.append({
                "vk_user_id": row[0],
                "username": row[1],
                "wins_today": row[2],
                "sudoku_wins": row[2]
            })
        
        return results
        
    except Exception as e:
        logger.error(f"Error in daily leaderboard: {e}")
        # Fallback на простой метод
        try:
            users = session.exec(select(User)).all()
            
            results = []
            for user in users:
                completed_today = session.exec(
                    select(func.count(SudokuGame.id))
                    .where(
                        SudokuGame.user_id == user.id, 
                        SudokuGame.is_completed == True, 
                        SudokuGame.completed_at >= today_start
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
        
        
        return {
            "period_days": days,
            "sudoku_completions": [{"date": str(row[0]), "count": row[1]} for row in sudoku_daily],
            "total_sudoku": sum(row[1] for row in sudoku_daily)
        }
        
    except Exception as e:
        logger.error(f"Error in completed games stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch completion stats")


@router.get("/user/{vk_user_id}")  # ← ТОЧНО такой путь!
async def get_user_stats(
    vk_user_id: str,
    session: Session = Depends(get_session)
):
    """Статистика игрока - GET /api/v1/stats/user/{vk_user_id}"""
    try:
        logger.info(f"=== Getting stats for user: {vk_user_id} ===")
        
        # Ищем пользователя
        user = session.exec(
            select(User).where(User.vk_user_id == str(vk_user_id))
        ).first()
        
        if not user:
            logger.warning(f"User not found: {vk_user_id}")
            raise HTTPException(status_code=404, detail=f"User {vk_user_id} not found")
        
        # Получаем все игры
        all_games = session.exec(
            select(SudokuGame).where(SudokuGame.user_id == user.id)
        ).all()
        
        total_games = len(all_games)
        completed_games = sum(1 for g in all_games if g.is_completed)
        
        # Получаем последние 15 игр
        recent_games = session.exec(
            select(SudokuGame)
            .where(SudokuGame.user_id == user.id)
            .order_by(SudokuGame.created_at.desc())
            .limit(15)
        ).all()
        
        # Рассчитываем винрейт по последним играм
        if recent_games:
            recent_completed = sum(1 for g in recent_games if g.is_completed)
            recent_win_rate = (recent_completed / len(recent_games)) * 100
        else:
            recent_completed = 0
            recent_win_rate = 0
        
        # Статистика по сложностям
        stats_by_diff = {}
        for game in recent_games:
            diff = game.difficulty
            if diff not in stats_by_diff:
                stats_by_diff[diff] = {"total": 0, "completed": 0}
            stats_by_diff[diff]["total"] += 1
            if game.is_completed:
                stats_by_diff[diff]["completed"] += 1
        
        for diff in stats_by_diff:
            total = stats_by_diff[diff]["total"]
            comp = stats_by_diff[diff]["completed"]
            stats_by_diff[diff]["win_rate"] = round((comp / total * 100), 2) if total > 0 else 0
        
        # Определяем доступные сложности
        if user.skill_level == "beginner":
            allowed_difficulties = ["easy"]
        elif user.skill_level == "intermediate":
            allowed_difficulties = ["easy", "medium"]
        else:
            allowed_difficulties = ["easy", "medium", "hard"]
        
        # Информация о прогрессе
        promotion_info = {}
        if user.skill_level == "beginner":
            easy_games = [g for g in recent_games if g.difficulty == "easy"]
            easy_win_rate = 0
            if easy_games:
                easy_completed = sum(1 for g in easy_games if g.is_completed)
                easy_win_rate = (easy_completed / len(easy_games)) * 100
            
            promotion_info = {
                "games_played": len(easy_games),
                "win_rate": round(easy_win_rate, 2),
                "needed_games": 10,
                "needed_win_rate": 70,
                "games_remaining": max(0, 10 - len(easy_games)),
                "can_promote": len(easy_games) >= 10 and easy_win_rate >= 70
            }
        elif user.skill_level == "intermediate":
            medium_games = [g for g in recent_games if g.difficulty == "medium"]
            medium_win_rate = 0
            if medium_games:
                medium_completed = sum(1 for g in medium_games if g.is_completed)
                medium_win_rate = (medium_completed / len(medium_games)) * 100
            
            promotion_info = {
                "games_played": len(medium_games),
                "win_rate": round(medium_win_rate, 2),
                "needed_games": 10,
                "needed_win_rate": 60,
                "games_remaining": max(0, 10 - len(medium_games)),
                "can_promote": len(medium_games) >= 10 and medium_win_rate >= 60
            }
        
        response = {
            "vk_user_id": user.vk_user_id,
            "username": user.username,
            "rating": user.rating,
            "skill_level": user.skill_level,
            "total_games_all_time": total_games,
            "total_completed_all_time": completed_games,
            "total_win_rate_all_time": round((completed_games / total_games * 100), 2) if total_games > 0 else 0,
            "recent_games_analyzed": len(recent_games),
            "recent_win_rate": round(recent_win_rate, 2),
            "recent_completed": recent_completed,
            "stats_by_difficulty": stats_by_diff,
            "promotion_info": promotion_info,
            "allowed_difficulties": allowed_difficulties
        }
        
        logger.info(f"Stats returned: {response}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in user stats: {e}", exc_info=True)
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

   