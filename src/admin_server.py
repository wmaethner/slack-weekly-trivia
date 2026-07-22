import os
import threading
import logging

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from stats_store import StatsStore

logger = logging.getLogger(__name__)

ADMIN_HOST = os.getenv("ADMIN_HOST", "0.0.0.0")
ADMIN_PORT = int(os.getenv("ADMIN_PORT", "8080"))

app = FastAPI(title="Trivia Admin")
_env = Environment(loader=FileSystemLoader("src/templates"))

store = StatsStore()


# ------------------------------------------------------------------
# Pages
# ------------------------------------------------------------------


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    overview = _get_overview()
    daily = store.get_daily_answer_counts(30)
    categories = store.get_category_stats()
    difficulties = store.get_difficulty_stats()
    leaderboard = store.get_leaderboard(limit=10)
    users_page = store.get_all_user_summaries(page=1, per_page=50, sort="answers")
    configs = store.get_all_configs()

    template = _env.get_template("dashboard.html")
    html = template.render(
        overview=overview,
        daily=daily,
        categories=categories,
        difficulties=difficulties,
        leaderboard=leaderboard,
        users=users_page["users"],
        total_users=users_page["total"],
        configs=configs,
    )
    return HTMLResponse(html)


# ------------------------------------------------------------------
# JSON API
# ------------------------------------------------------------------


@app.get("/admin/api/overview")
async def api_overview():
    return _get_overview()


@app.get("/admin/api/daily")
async def api_daily(days: int = Query(30, ge=1, le=365)):
    return {"series": store.get_daily_answer_counts(days)}


@app.get("/admin/api/users")
async def api_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    sort: str = Query("answers", pattern="^(answers|accuracy|correct)$"),
):
    return store.get_all_user_summaries(page=page, per_page=per_page, sort=sort)


@app.get("/admin/api/users/{user_id}")
async def api_user_detail(user_id: str):
    stats = store.get_user_stats(user_id)
    if stats["total"] == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return stats


@app.get("/admin/api/categories")
async def api_categories():
    return {"categories": store.get_category_stats()}


@app.get("/admin/api/leaderboard")
async def api_leaderboard(limit: int = Query(10, ge=1, le=100)):
    return {"leaderboard": store.get_leaderboard(limit=limit)}


@app.get("/admin/configs")
async def api_configs():
    return {"configs": store.get_all_configs()}


# ------------------------------------------------------------------
# Internal
# ------------------------------------------------------------------


def _get_overview() -> dict:
    return {
        "total_answers": store.get_total_answer_count(),
        "total_users": store.get_total_user_count(),
        "overall_accuracy": store.get_global_accuracy(),
        "active_workspaces": store.get_workspace_count(),
        "answers_today": _answers_since("today"),
        "answers_this_week": _answers_since("-7 days"),
    }


def _answers_since(since: str) -> int:
    row = store._conn.execute(
        "SELECT COUNT(*) FROM answers WHERE timestamp >= date('now', ?)",
        (since,),
    ).fetchone()
    return row[0]


# ------------------------------------------------------------------
# Standalone runner
# ------------------------------------------------------------------


def run():
    logger.info(f"Admin server starting on {ADMIN_HOST}:{ADMIN_PORT}")
    uvicorn.run(app, host=ADMIN_HOST, port=ADMIN_PORT, log_level="info")


def start_in_thread():
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    logger.info("Admin server thread started")


if __name__ == "__main__":
    run()
