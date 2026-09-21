"""Slack Block Kit builders — the single place all trivia messages are assembled.

Pure functions: no Slack client, no DB/service access, no APScheduler —
data in, blocks out.
"""

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

TROPHIES = [
    ":first_place_medal:",
    ":second_place_medal:",
    ":third_place_medal:",
]


# ------------------------------------------------------------------
# Primitives
# ------------------------------------------------------------------

def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _category_display(category: str) -> str:
    return category.replace("_", " ").title()


def _trophy(i: int) -> str:
    return TROPHIES[i] if i < len(TROPHIES) else f"{i + 1}."


def _find_option(options: list[dict], value: str) -> dict:
    for opt in options:
        if opt["value"] == value:
            return opt
    return options[0]


# ------------------------------------------------------------------
# Trivia Q&A (used by trivia_service.py)
# ------------------------------------------------------------------

def build_question_blocks(state: dict, action_prefix: str = "trivia_answer_") -> list[dict]:
    emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
    category_title = _category_display(state["category"])

    blocks = [
        _header(f"{emoji}  {category_title}"),
        _section(f"*{state['question_text']}*"),
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
        _context(f"Difficulty: {state['difficulty']}  •  Pick an answer above")
    )

    return blocks


def build_result_blocks(state: dict, selected_label: str) -> list[dict]:
    correct_label = state["correct_label"]
    is_correct = selected_label == correct_label

    header_text = (
        ":white_check_mark:  Correct!"
        if is_correct
        else ":x:  Wrong!"
    )

    blocks = [
        _header(header_text),
        _section(f"*{state['question_text']}*"),
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
            line = f"     {label})  {answer}"

        blocks.append(_section(line))

    category_display = _category_display(state["category"])
    emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
    blocks.append(
        _context(f"{emoji}  {category_display}  •  Difficulty: {state['difficulty']}")
    )

    return blocks


def build_public_question_blocks(state: dict) -> list[dict]:
    """Public channel message with 'Answer' button."""
    emoji = CATEGORY_EMOJI.get(state["category"], ":grey_question:")
    category_title = _category_display(state["category"])

    return [
        _header(":trophy:  Daily Trivia"),
        _section(f"*{state['question_text']}*"),
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
        _context(
            f"{emoji}  {category_title}"
            f"  •  Difficulty: {state['difficulty']}"
            f"  •  Click *Answer* to submit yours privately"
        ),
    ]


# ------------------------------------------------------------------
# Slash commands (used by app.py)
# ------------------------------------------------------------------

def build_stats_blocks(stats: dict) -> list[dict]:
    total = stats["total"]
    correct = stats["correct"]
    accuracy = stats["accuracy"]

    blocks = [
        _header(":bar_chart:  Your Trivia Stats"),
        _section(
            f"*Total:* {total}  |  *Correct:* {correct}  |"
            f"  *Wrong:* {total - correct}  |  *Accuracy:* {accuracy}%"
        ),
    ]

    if stats["by_difficulty"]:
        lines = []
        for level in ["easy", "medium", "hard"]:
            d = stats["by_difficulty"].get(level)
            if d:
                lines.append(f"• {level.title()}: {d['correct']}/{d['total']} ({d['accuracy']}%)")
        blocks.append(_section("*By Difficulty*\n" + "\n".join(lines)))

    if stats["by_category"]:
        lines = []
        for cat, d in stats["by_category"].items():
            emoji = CATEGORY_EMOJI.get(cat, ":grey_question:")
            display = _category_display(cat)
            lines.append(f"{emoji}  {display}: {d['correct']}/{d['total']} ({d['accuracy']}%)")
        blocks.append(_section("*By Category*\n" + "\n".join(lines)))

    return blocks


def build_leaderboard_command_blocks(
    board: list[dict],
    categories: list[str],
    difficulties: list[str],
    selected_category: str | None,
    selected_difficulty: str | None,
    streaks: list[dict],
    my_rank: dict | None,
    answer_count: int,
) -> list[dict]:
    cat_options = [
        {"text": {"type": "plain_text", "text": "All Categories", "emoji": True}, "value": "all"}
    ]
    for cat in categories:
        cat_options.append(
            {
                "text": {
                    "type": "plain_text",
                    "text": f"{CATEGORY_EMOJI.get(cat, ':grey_question:')}  {_category_display(cat)}",
                    "emoji": True,
                },
                "value": cat,
            }
        )

    diff_options = [
        {"text": {"type": "plain_text", "text": "All Difficulties", "emoji": True}, "value": "all"}
    ]
    for d in difficulties:
        diff_options.append(
            {"text": {"type": "plain_text", "text": d.title(), "emoji": True}, "value": d}
        )

    blocks = [
        _header(":trophy:  Leaderboard"),
        {
            "type": "actions",
            "elements": [
                {
                    "type": "static_select",
                    "placeholder": {"type": "plain_text", "text": "Category", "emoji": True},
                    "options": cat_options,
                    "action_id": "leaderboard_category",
                    **(
                        {"initial_option": _find_option(cat_options, selected_category)}
                        if selected_category
                        else {}
                    ),
                },
                {
                    "type": "static_select",
                    "placeholder": {"type": "plain_text", "text": "Difficulty", "emoji": True},
                    "options": diff_options,
                    "action_id": "leaderboard_difficulty",
                    **(
                        {"initial_option": _find_option(diff_options, selected_difficulty)}
                        if selected_difficulty
                        else {}
                    ),
                },
            ],
        },
    ]

    if not board:
        blocks.append(_section("No stats yet! Play `/trivia` to get on the board."))
    else:
        lines = []
        for i, entry in enumerate(board):
            lines.append(
                f"{_trophy(i)}  <@{entry['user_id']}>  "
                f"{entry['accuracy']}%  ({entry['correct']}/{entry['total']})"
            )
        blocks.append(_section("\n".join(lines)))

    if streaks:
        streak_lines = []
        for i, s in enumerate(streaks):
            streak_lines.append(f"{_trophy(i)}  <@{s['user_id']}>  {s['streak']} correct in a row")
        blocks.append({"type": "divider"})
        blocks.append(_section(":fire:  *Top Streaks*\n" + "\n".join(streak_lines)))

    if my_rank:
        blocks.append(
            _section(
                f"*Your rank:* #{my_rank['rank']} of {my_rank['total_players']}"
                f"  ({my_rank['accuracy']}%)"
            )
        )
    elif 0 < answer_count < 5:
        need = 5 - answer_count
        blocks.append(
            _section(
                f":hourglass_flowing_sand:  Answer *{need} more*"
                f" question{'s' if need > 1 else ''} to get ranked on"
                " the leaderboard (min 5 required)."
            )
        )

    active_filters = []
    if selected_category:
        active_filters.append(f"{CATEGORY_EMOJI.get(selected_category, '')} {_category_display(selected_category)}")
    if selected_difficulty:
        active_filters.append(selected_difficulty.title())
    filter_text = "  •  ".join(active_filters) if active_filters else "All"

    blocks.append(_context(f"Filters: {filter_text}  •  Min 5 answers to rank"))

    return blocks


# ------------------------------------------------------------------
# Scheduled posts (used by scheduler.py)
# ------------------------------------------------------------------

def build_weekly_leaderboard_blocks(board: list[dict], category_tops: list[tuple], questions: list[dict]) -> list[dict]:
    blocks = [_header(":trophy:  Weekly Leaderboard")]

    if not board:
        blocks.append(_section("No stats this week! Play daily trivia to get on the board."))
    else:
        lines = []
        for i, entry in enumerate(board):
            lines.append(
                f"{_trophy(i)}  <@{entry['user_id']}>  "
                f"{entry['accuracy']}%  ({entry['correct']}/{entry['total']})"
            )
        blocks.append(_section("*Overall Top 5*\n" + "\n".join(lines)))

    if category_tops:
        cat_lines = []
        for cat, top in category_tops:
            emoji = CATEGORY_EMOJI.get(cat, ":grey_question:")
            cat_lines.append(f"{emoji}  {_category_display(cat)}: <@{top['user_id']}> {top['accuracy']}%")
        blocks.append(_section("*Top by Category*\n" + "\n".join(cat_lines)))

    extremes = build_extremes_block(questions, "*This Week's Toughest & Easiest*")
    if extremes:
        blocks.append(extremes)

    blocks.append(_context("Use `/leaderboard` for filters and `/stats` for your own results."))
    return blocks


def build_monthly_recap_blocks(questions: list[dict], month_label: str) -> list[dict]:
    blocks = [_header(f":calendar: Monthly Recap — {month_label}")]

    extremes = build_extremes_block(questions, None)
    blocks.append(extremes if extremes else _section("No questions answered last month."))
    return blocks


def build_extremes_block(questions: list[dict], heading: str | None) -> dict | None:
    """Section block showing the hardest/easiest question, or None if empty."""
    if not questions:
        return None

    hardest = questions[0]
    easiest = questions[-1]

    def line(emoji, label, q):
        emoji_cat = CATEGORY_EMOJI.get(q["category"], ":grey_question:")
        return (
            f"{emoji} *{label}:* {emoji_cat} {q['question_text']} "
            f"— {q['accuracy']}% correct ({q['correct']}/{q['total']})"
        )

    lines = [line(":skull:", "Hardest", hardest)]
    if easiest["question_id"] != hardest["question_id"]:
        lines.append(line(":sparkles:", "Easiest", easiest))

    text = "\n".join(lines)
    if heading:
        text = f"{heading}\n{text}"

    return _section(text)
