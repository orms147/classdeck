"""Slide budgets: how much of an exercise, a conversation or a list fits on one slide.

The deck is a fixed 1920x1080 stage; a slide body has about 790-890 design pixels of height. The
writer estimates the height of each item from the length of its text (a line holds so many
characters at the exercise size) and cuts a long block into ``(1/2)`` slides of similar height
instead of cramming it on one. The estimates are deliberately a little generous; whatever they
miss is caught by the browser-side auto-fit (``--fit``, down to 0.75), and ``tools/deck_check.py``
holds the result to the stage. All sizes are design pixels of the base look in
``deck_template.html``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

EXERCISE_SIZE = 44
"""The exercise body size (``--size-exercise``); themes do not change it."""
CHAR_WIDTH = 0.50
"""Average width of a letter in em for the deck's sans-serif fonts (spaces included)."""
WRAP_LOSS = 0.97
"""The share of a line a greedy word wrap really fills."""
CONTENT_WIDTH = 1640
"""Width of an exercise (``.ex``)."""

EX_BUDGET = 620
"""Height of an exercise's own content (not its heading and buttons) that fits at full size."""
RULE_BUDGET = 740
"""Height of a whole rule box (frame, title, points, examples)."""
SUMMARY_BUDGET = 860
"""Height of the summary slide: title, word cards and sentence patterns."""
ACTIVITY_BUDGET = 900
"""Height of an activity slide: heading, instructions, step cards and the countdown."""
CARDS_PER_ROW = 4

MAX_CHOICE_ITEMS = 4
MAX_CHOICE_ITEMS_FOUR_OPTIONS = 3
MAX_GAP_ITEMS = 5
MAX_MATCH_ITEMS = 5
MAX_PICTURE_MATCH_ITEMS = 6
MAX_TF_ITEMS = 5
MAX_QUESTIONS = 4
MAX_SORT_WORDS = 10
MAX_SORT_SENTENCES = 4
MAX_DIALOGUE_LINES = 5
MAX_STEPS = 5
MAX_GALLERY_PICTURES = 6
MAX_PHONICS_WORDS = 6
MAX_WORDCARDS = 4
MAX_GRID_ROWS = 7
MAX_SELF_CHECK_ITEMS = 6
WORDCARD_BUDGET = 760


def text_rows(text: str, width: float, size: float = EXERCISE_SIZE, *, bold: bool = False) -> int:
    """How many lines ``text`` takes in a box ``width`` wide at ``size`` (greedy word wrap)."""
    if not text.strip():
        return 1
    per_line = max(4.0, width * WRAP_LOSS / (size * CHAR_WIDTH * (1.08 if bold else 1.0)))
    rows = 0
    for paragraph in text.split("\n"):
        line = 0.0
        count = 1
        for word in paragraph.split():
            need = len(word) + (1 if line else 0)
            if line and line + need > per_line:
                count += 1
                line = float(len(word))
            else:
                line += need
            while line > per_line:  # a word longer than a line breaks anywhere
                count += 1
                line -= per_line
        rows += count
    return max(rows, 1)


def flow_rows(widths: Sequence[float], available: float, gap: float) -> int:
    """Rows taken by boxes of these widths that wrap (a flex row with ``flex-wrap``)."""
    rows = 1
    used = 0.0
    for width in widths:
        width = min(width, available)
        if used and used + gap + width > available:
            rows += 1
            used = width
        else:
            used += width + (gap if used else 0)
    return rows


# --- cutting a list into slides ---------------------------------------------------------------


def paginate(
    count: int,
    cost: Callable[[int, int], float],
    *,
    budget: float,
    max_items: int,
) -> list[tuple[int, int]]:
    """Cut ``count`` items into ``(start, end)`` ranges, each no taller than ``budget``.

    ``cost(i, j)`` is the height of items ``i`` to ``j - 1`` on one slide. The fewest slides come
    first (a page of one item may exceed the budget: it cannot be split further); then the cut is
    chosen so that the tallest slide is as short as possible, then so that the counts are even.
    """
    if count <= 0:
        return []

    def fits(i: int, j: int) -> bool:
        return j - i <= max_items and (j - i == 1 or cost(i, j) <= budget)

    pages = 0
    start = 0
    while start < count:
        end = start + 1
        while end < count and fits(start, end + 1):
            end += 1
        start = end
        pages += 1
    if pages == 1:
        return [(0, count)]

    infinity = (math.inf, math.inf)
    best: list[list[tuple[float, float]]] = [[infinity] * (count + 1) for _ in range(pages + 1)]
    back: list[list[int]] = [[-1] * (count + 1) for _ in range(pages + 1)]
    best[0][0] = (0.0, 0.0)
    for used in range(1, pages + 1):
        for end in range(1, count + 1):
            for start in range(end - 1, -1, -1):
                previous = best[used - 1][start]
                if previous == infinity or not fits(start, end):
                    continue
                height = cost(start, end)
                value = (max(previous[0], height), previous[1] + (end - start) ** 2)
                if value < best[used][end]:
                    best[used][end] = value
                    back[used][end] = start
    if best[pages][count] == infinity:  # cannot happen (the greedy cut is feasible); be safe
        return [(i, min(i + max_items, count)) for i in range(0, count, max_items)]
    cuts: list[tuple[int, int]] = []
    end = count
    for used in range(pages, 0, -1):
        start = back[used][end]
        cuts.append((start, end))
        end = start
    return cuts[::-1]


# --- exercise items (heights in design pixels, at the base exercise size) ----------------------

_LINE = EXERCISE_SIZE * 1.3
_GAP = 20


def choice_item_height(stem: str, options: Sequence[str], number: int, width: float) -> float:
    inner = width - 64
    stem_rows = text_rows(f"{number}  {stem}", inner)
    option_widths = [64 + 38 + 14 + len(option) * EXERCISE_SIZE * CHAR_WIDTH for option in options]
    option_rows = flow_rows(option_widths, inner, 14)
    return 48 + stem_rows * _LINE + 14 + option_rows * 72 + (option_rows - 1) * 14


def choice_layout(items: Sequence[tuple[str, Sequence[str]]], first: int) -> tuple[float, bool]:
    """``(height, two columns?)`` of the better layout for these choice items."""
    single = [
        choice_item_height(stem, options, first + n + 1, CONTENT_WIDTH)
        for n, (stem, options) in enumerate(items)
    ]
    one = sum(single) + _GAP * (len(single) - 1)
    if len(items) < 3:
        return one, False
    double = [
        choice_item_height(stem, options, first + n + 1, (CONTENT_WIDTH - 32) / 2)
        for n, (stem, options) in enumerate(items)
    ]
    two = sum(max(double[i : i + 2]) for i in range(0, len(double), 2))
    two += _GAP * (math.ceil(len(double) / 2) - 1)
    return (two, True) if two < one * 0.92 else (one, False)


def gap_item_height(text: str, answers: Sequence[str], number: int) -> float:
    width = CONTENT_WIDTH - 90
    shown = text
    for answer in answers:
        shown = shown.replace("___", "x" * max(3, round(len(answer) * 1.25) + 3), 1)
    rows = text_rows(f"{number}  {shown}", width)
    return rows * EXERCISE_SIZE * 1.55 + 14


def word_box_height(words: Sequence[str]) -> float:
    if not words:
        return 0.0
    widths = [len(word) * EXERCISE_SIZE * CHAR_WIDTH * 1.1 + 64 for word in words]
    rows = flow_rows(widths, CONTENT_WIDTH, 16)
    return rows * 75 + (rows - 1) * 16 + 28


def true_false_item_height(statement: str, number: int) -> float:
    width = CONTENT_WIDTH - 64 - 32 - 2 * 170 - 16 - 40
    rows = text_rows(f"{number}  {statement}", width)
    return max(72.0, rows * _LINE) + 28 + 8 + 16


def question_height(question: str, number: int) -> float:
    width = CONTENT_WIDTH - 64 - 8 - 54 - 64 - 36
    rows = text_rows(question, width)
    return max(64.0, rows * _LINE) + 32 + 8 + 16


def match_item_height(text: str, *, column: float = 640) -> float:
    rows = text_rows(text, column)
    return max(76.0, rows * EXERCISE_SIZE * 1.25 + 20 + 8) + 12


def sentence_height(text: str, number: int) -> float:
    width = CONTENT_WIDTH - 60 - 64 - 40
    rows = text_rows(f"{number} {text}", width, bold=True)
    return max(72.0, rows * EXERCISE_SIZE * 1.7) + 20


def sort_words_height(words: Sequence[str], sounds: Sequence[str], pairs: Sequence[int]) -> float:
    """Height of a sound-sorting slide at its tallest: all chips in the pool, or all in columns.

    ``pairs`` is the sound index of each word. A chip is a pill (the word at 52px, bold) with a
    speaker button beside it.
    """
    widths = [len(word) * 52 * CHAR_WIDTH * 1.1 + 64 + 8 + 74 for word in words]
    pool_rows = flow_rows(widths, CONTENT_WIDTH - 32, 18)
    pool = max(112.0, pool_rows * 84 + (pool_rows - 1) * 14 + 32)
    columns = max(len(sounds), 1)
    column_width = (CONTENT_WIDTH - 28 * (columns - 1)) / columns - 28
    tallest = 0.0
    for sound in range(columns):
        mine = [widths[n] for n, which in enumerate(pairs) if which == sound]
        rows = flow_rows(mine, column_width, 18) if mine else 1
        tallest = max(tallest, rows * 84 + (rows - 1) * 14 + 28)
    columns_empty = 96 + 14 + 150
    columns_full = 96 + 14 + max(150.0, tallest)
    return max(pool + 28 + columns_empty, 112 + 28 + columns_full)


# --- dialogue ----------------------------------------------------------------------------------


def bubble_height(text: str, *, dense: bool) -> int:
    """Height in design pixels of one speech bubble and the gap below it."""
    per_line, line_height, padding, floor, gap = (
        (64, 41, 32, 80, 16) if dense else (46, 53, 56, 112, 30)
    )
    lines = max(1, -(-len(text) // per_line))
    return max(floor, padding + lines * line_height) + gap


# --- lists (rule box, summary, steps, gallery, reading) ----------------------------------------


@dataclass(frozen=True)
class RuleItem:
    height: float
    point: bool


def rule_point_height(text: str) -> float:
    rows = text_rows(text, 1290, EXERCISE_SIZE, bold=True)
    return rows * EXERCISE_SIZE * 1.35 + 10


def rule_example_height(text: str) -> float:
    rows = text_rows(text, 1200, EXERCISE_SIZE, bold=True)
    return max(60.0, rows * EXERCISE_SIZE * 1.3) + 20 + 10 + 14


def rule_frame_height(title: str) -> float:
    """The box's padding and border, and its title tab (which may take several lines)."""
    rows = text_rows(title, 1100, 60, bold=True)
    return 136 + 28 + 40 + rows * 70


def summary_row_height(has_picture: bool) -> float:
    return (24 * 2 + 180 + 12 + 46 + 8 if has_picture else 24 * 2 + 46 + 12 + 52 + 8) + 24


def step_height(text: str) -> float:
    """A numbered step card (two to a row; the number badge is 96px)."""
    rows = text_rows(text, 540, 40, bold=True)
    return max(152.0, rows * 40 * 1.25 + 56)


READING_TEXT_BESIDE_PICTURE = 776
"""Width of a reading paragraph next to the picture (640px wide, 48px gap, box padding, speaker)."""
READING_TEXT_NARROW = 1056
"""... and next to the narrow (360px) picture of a very long paragraph."""
READING_TEXT_FULL = 1464


def reading_paragraph_height(text: str, width: float, size: float = 44.0) -> float:
    return text_rows(text, width, size, bold=True) * size * 1.4 + 18


def wordcard_height(word: str, example: str, has_picture: bool) -> float:
    """One lower-secondary word card (two to a row): word, IPA, meaning and the example."""
    text_width = 490 if has_picture else 740
    word_rows = text_rows(word, text_width, 56, bold=True)
    rows = text_rows(example, text_width, 32) if example else 0
    height = 44 + word_rows * 62 + 8 + 44 + 8 + 52 + (8 + rows * 42 if rows else 0)
    return max(height, 240.0) if has_picture else height
