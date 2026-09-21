import random

from slack_blocks import build_question_blocks, build_result_blocks, build_public_question_blocks

LABELS = ["A", "B", "C", "D", "E"]


class TriviaService:
    """Core trivia game logic. Decoupled from Slack and API details."""

    def __init__(self, trivia_api, stats_store):
        self.api = trivia_api
        self.stats = stats_store
        self._posted = {}
        self._posted_answers = {}  # (question_id, user_id) → True
        self._load_posted_states()

    def get_user_stats(self, user_id):
        """Return stats summary for a user."""
        return self.stats.get_user_stats(user_id)

    def get_leaderboard(self, limit=3, category=None, difficulty=None):
        return self.stats.get_leaderboard(limit, category, difficulty)

    def get_user_rank(self, user_id, category=None, difficulty=None):
        return self.stats.get_user_rank(user_id, category, difficulty)

    def get_streak_leaderboard(self, limit=3):
        return self.stats.get_streak_leaderboard(limit)

    def get_user_answer_count(self, user_id: str) -> int:
        return self.stats.get_user_answer_count(user_id)

    def get_active_categories(self):
        return self.stats.get_active_categories()

    def get_active_difficulties(self):
        return self.stats.get_active_difficulties()

    def get_question_extremes(self, start: str, end: str) -> list[dict]:
        return self.stats.get_question_extremes(start, end)

    # ------------------------------------------------------------------
    # Posted questions (shared, multi-answerer)
    # ------------------------------------------------------------------

    def create_posted_question(self, channel_id):
        """Fetch a question and return public channel blocks + question_id."""
        batch = self.api.fetch_questions(limit=20)
        q = None
        for candidate in batch:
            if not self.stats.has_asked(channel_id, candidate["id"]):
                q = candidate
                break

        if q is None:
            for _ in range(5):
                candidate = self.api.fetch_question()
                if not self.stats.has_asked(channel_id, candidate["id"]):
                    q = candidate
                    break

        if q is None:
            raise RuntimeError("Could not find a unique question")

        self.stats.record_asked(channel_id, q["id"])

        answers = [q["correctAnswer"]] + q["incorrectAnswers"]
        random.shuffle(answers)
        correct_index = answers.index(q["correctAnswer"])
        labels = LABELS[: len(answers)]

        state = {
            "question_id": q["id"],
            "channel_id": channel_id,
            "correct_label": labels[correct_index],
            "correct_answer": q["correctAnswer"],
            "answers": answers,
            "labels": labels,
            "question_text": q["question"]["text"],
            "category": q["category"],
            "difficulty": q.get("difficulty", "medium"),
        }
        # Expire old question for this channel
        old_ids = [
            qid for qid, s in self._posted.items()
            if s.get("channel_id") == channel_id
        ]
        for old_id in old_ids:
            del self._posted[old_id]
        self._posted[q["id"]] = state
        self.stats.record_question_state(channel_id, state)

        public_blocks = build_public_question_blocks(state)
        return public_blocks, q["id"]

    def _load_posted_states(self):
        """Hydrate _posted with latest question per channel. Older ones expire."""
        latest_per_channel = {}
        for state in self.stats.load_all_question_states():
            ch = state["channel_id"]
            latest_per_channel[ch] = state  # later row overwrites = newest wins
        self._posted = {s["question_id"]: s for s in latest_per_channel.values()}

    def get_answer_blocks(self, question_id):
        """Return ephemeral answer blocks for a posted question."""
        state = self._posted.get(question_id)
        if state is None:
            return None
        return build_question_blocks(state, "posted_trivia_answer_")

    def check_posted_answer(self, question_id, user_id, selected_label):
        """Check a posted answer, prevent double-answers, record stats."""
        state = self._posted.get(question_id)
        if state is None:
            return None

        key = (question_id, user_id)
        if key in self._posted_answers or self.stats.has_answered(user_id, question_id):
            return None  # already answered

        self._posted_answers[key] = True
        is_correct = selected_label == state["correct_label"]
        self.stats.record_answer(
            user_id=user_id,
            question_id=state["question_id"],
            category=state["category"],
            difficulty=state["difficulty"],
            correct=is_correct,
            selected=selected_label,
        )

        return build_result_blocks(state, selected_label)

    # ------------------------------------------------------------------
    # Workspace config passthroughs
    # ------------------------------------------------------------------

    def set_channel_config(self, team_id, channel_id):
        self.stats.set_channel_config(team_id, channel_id)

    def get_channel_config(self, team_id):
        return self.stats.get_channel_config(team_id)

    def set_post_time(self, team_id, post_time):
        self.stats.set_post_time(team_id, post_time)

    def get_all_configs(self):
        return self.stats.get_all_configs()
