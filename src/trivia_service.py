import random

CATEGORY_EMOJI = {
    "music": ":musical_note:",
    "sport_and_leisure": ":soccer:",
    "film_and_tv": ":clapper:",
    "arts_and_literature": ":art:",
    "history": ":hourglass:",
    "society_and_culture": ":globe_with_meridians:",
    "science": ":microscope:",
    "geography": ":earth_americas:",
    "food_and_drink": ":fork_and_knife:",
    "general_knowledge": ":brain:",
}
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

    # ------------------------------------------------------------------
    # Posted questions (shared, multi-answerer)
    # ------------------------------------------------------------------

    def create_posted_question(self, channel_id):
        """Fetch a question and return public channel blocks + question_id."""
        max_attempts = 5
        for _ in range(max_attempts):
            q = self.api.fetch_question()
            if not self.stats.has_asked(channel_id, q["id"]):
                break

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

        public_blocks = self._build_public_question_blocks(state)
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
        return self._build_question_blocks(state, "posted_trivia_answer_")

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

        return self._build_result_blocks(state, selected_label)

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

    # ------------------------------------------------------------------
    # Block builders
    # ------------------------------------------------------------------

    def _build_question_blocks(self, state, action_prefix="trivia_answer_"):
        emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
        category_title = state["category"].replace("_", " ").title()

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji}  {category_title}",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{state['question_text']}*",
                },
            },
        ]

        for label, answer in zip(state["labels"], state["answers"]):
            value = label
            if action_prefix != "trivia_answer_":
                value = f"{label}|{state['question_id']}"

            blocks.append(
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "emoji": True,
                                "text": f"{label}. {answer}",
                            },
                            "action_id": f"{action_prefix}{label.lower()}",
                            "value": value,
                        }
                    ],
                }
            )

        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Difficulty: {state['difficulty']}  •  Pick an answer above",
                    }
                ],
            }
        )

        return blocks

    def _build_result_blocks(self, state, selected_label):
        correct_label = state["correct_label"]
        is_correct = selected_label == correct_label

        header_text = (
            ":white_check_mark:  Correct!"
            if is_correct
            else ":x:  Wrong!"
        )

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text,
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{state['question_text']}*",
                },
            },
        ]

        for label, answer in zip(state["labels"], state["answers"]):
            if label == correct_label:
                if label == selected_label:
                    line = (
                        f":white_check_mark:  *{label})  {answer}*"
                        "  ← correct  (your pick)"
                    )
                else:
                    line = f":white_check_mark:  *{label})  {answer}*  ← correct"
            elif label == selected_label:
                line = f":x:  {label})  {answer}  ← your answer"
            else:
                line = f"     {label})  {answer}"

            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": line},
                }
            )

        category_display = state["category"].replace("_", " ").title()
        emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji}  {category_display}"
                            f"  •  Difficulty: {state['difficulty']}"
                        ),
                    }
                ],
            }
        )

        return blocks

    def _build_public_question_blocks(self, state):
        """Public channel message with 'Answer' button."""
        emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
        category_title = state["category"].replace("_", " ").title()

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":trophy:  Daily Trivia",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{state['question_text']}*",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "Answer",
                            "emoji": True,
                        },
                        "action_id": "start_answer",
                        "value": state["question_id"],
                    }
                ],
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"{emoji}  {category_title}"
                            f"  •  Difficulty: {state['difficulty']}"
                            f"  •  Click *Answer* to submit yours privately"
                        ),
                    }
                ],
            },
        ]
