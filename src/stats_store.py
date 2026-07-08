import os
import sqlite3
from datetime import datetime, timezone

_DATA_DIR = os.environ.get(
    "TRIVIA_DB_DIR",
    os.path.join(os.path.dirname(__file__), "data"),
)
os.makedirs(_DATA_DIR, exist_ok=True)


class StatsStore:
    """SQLite-backed persistence for trivia answer stats."""

    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(_DATA_DIR, "trivia_stats.db")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._migrate()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _migrate(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS answers (
                user_id     TEXT NOT NULL,
                question_id TEXT NOT NULL,
                category    TEXT NOT NULL,
                difficulty  TEXT NOT NULL,
                correct     INTEGER NOT NULL,
                selected    TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                UNIQUE(user_id, question_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS asked_questions (
                channel_id  TEXT NOT NULL,
                question_id TEXT NOT NULL,
                UNIQUE(channel_id, question_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_configs (
                team_id    TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                post_time  TEXT NOT NULL DEFAULT '09:00'
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_answer(
        self,
        user_id,
        question_id,
        category,
        difficulty,
        correct,
        selected,
        timestamp=None,
    ):
        if timestamp is None:
            timestamp = datetime.now(timezone.utc).isoformat()

        self._conn.execute(
            """
            INSERT OR IGNORE INTO answers
                (user_id, question_id, category, difficulty,
                 correct, selected, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, question_id, category, difficulty,
             int(correct), selected, timestamp),
        )
        self._conn.commit()

    def record_asked(self, channel_id, question_id):
        """Mark a question as having been asked in a channel."""
        self._conn.execute(
            "INSERT OR IGNORE INTO asked_questions (channel_id, question_id) "
            "VALUES (?, ?)",
            (channel_id, question_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def has_asked(self, channel_id, question_id):
        """Check if a question has already been asked in a channel."""
        row = self._conn.execute(
            "SELECT 1 FROM asked_questions "
            "WHERE channel_id = ? AND question_id = ?",
            (channel_id, question_id),
        ).fetchone()
        return row is not None

    def get_user_stats(self, user_id):
        row = self._conn.execute(
            """
            SELECT COUNT(*), SUM(correct)
            FROM answers
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

        total = row[0]
        correct = row[1] or 0
        accuracy = round(correct / total * 100, 1) if total else 0

        by_category = self._group_by(user_id, "category")
        by_difficulty = self._group_by(user_id, "difficulty")

        return {
            "total": total,
            "correct": correct,
            "accuracy": accuracy,
            "by_category": by_category,
            "by_difficulty": by_difficulty,
        }

    def get_leaderboard(self, limit=3, category=None, difficulty=None):
        where = []
        params = []

        if category:
            where.append("category = ?")
            params.append(category)
        if difficulty:
            where.append("difficulty = ?")
            params.append(difficulty)

        clause = ""
        if where:
            clause = "WHERE " + " AND ".join(where)

        rows = self._conn.execute(
            f"""
            SELECT user_id, COUNT(*) AS total, SUM(correct) AS correct
            FROM answers
            {clause}
            GROUP BY user_id
            ORDER BY CAST(SUM(correct) AS REAL) / COUNT(*) DESC, SUM(correct) DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()

        return [
            {
                "user_id": r[0],
                "total": r[1],
                "correct": r[2] or 0,
                "accuracy": round(r[2] / r[1] * 100, 1) if r[1] else 0,
            }
            for r in rows
        ]

    def get_active_categories(self):
        rows = self._conn.execute(
            "SELECT DISTINCT category FROM answers ORDER BY category"
        ).fetchall()
        return [r[0] for r in rows]

    def get_active_difficulties(self):
        rows = self._conn.execute(
            "SELECT DISTINCT difficulty FROM answers ORDER BY difficulty"
        ).fetchall()
        return [r[0] for r in rows]

    def _group_by(self, user_id, column):
        rows = self._conn.execute(
            f"""
            SELECT {column}, COUNT(*), SUM(correct)
            FROM answers
            WHERE user_id = ?
            GROUP BY {column}
            ORDER BY COUNT(*) DESC
            """,
            (user_id,),
        ).fetchall()

        result = {}
        for key, total, correct in rows:
            c = correct or 0
            result[key] = {
                "total": total,
                "correct": c,
                "accuracy": round(c / total * 100, 1) if total else 0,
            }
        return result

    # ------------------------------------------------------------------
    # Workspace config
    # ------------------------------------------------------------------

    def set_channel_config(self, team_id, channel_id):
        self._conn.execute(
            "INSERT INTO workspace_configs (team_id, channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(team_id) DO UPDATE SET channel_id = excluded.channel_id",
            (team_id, channel_id),
        )
        self._conn.commit()

    def get_channel_config(self, team_id):
        row = self._conn.execute(
            "SELECT channel_id, post_time FROM workspace_configs WHERE team_id = ?",
            (team_id,),
        ).fetchone()
        if row is None:
            return None, None
        return row[0], row[1]

    def set_post_time(self, team_id, post_time):
        self._conn.execute(
            "INSERT INTO workspace_configs (team_id, channel_id, post_time) "
            "VALUES (?, '', ?) "
            "ON CONFLICT(team_id) DO UPDATE SET post_time = excluded.post_time",
            (team_id, post_time),
        )
        self._conn.commit()

    def get_all_configs(self):
        rows = self._conn.execute(
            "SELECT team_id, channel_id, post_time FROM workspace_configs"
        ).fetchall()
        return [
            {"team_id": r[0], "channel_id": r[1], "post_time": r[2]}
            for r in rows
        ]

    def close(self):
        self._conn.close()
