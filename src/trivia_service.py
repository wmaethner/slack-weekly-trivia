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

    def __init__(self, trivia_api):
        self.api = trivia_api
        self._active = {}

    def create_question(self, user_id):
        """Fetch a question, store state, and return Slack blocks."""
        q = self.api.fetch_question()

        answers = [q["correctAnswer"]] + q["incorrectAnswers"]
        random.shuffle(answers)
        correct_index = answers.index(q["correctAnswer"])
        labels = LABELS[: len(answers)]

        state = {
            "correct_label": labels[correct_index],
            "correct_answer": q["correctAnswer"],
            "answers": answers,
            "labels": labels,
            "question_text": q["question"]["text"],
            "category": q["category"],
            "difficulty": q.get("difficulty", "medium"),
        }
        self._active[user_id] = state

        return self._build_question_blocks(state)

    def check_answer(self, user_id, selected_label):
        """Check the user's answer and return result Slack blocks."""
        state = self._active.pop(user_id, None)
        if state is None:
            return None
        return self._build_result_blocks(state, selected_label)

    # ------------------------------------------------------------------
    # Block builders
    # ------------------------------------------------------------------

    def _build_question_blocks(self, state):
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
                            "action_id": f"trivia_answer_{label.lower()}",
                            "value": label,
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
