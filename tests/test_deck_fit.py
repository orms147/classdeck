"""Everything fits on the slide: budgets (no browser) and the headless-browser gate.

The browser tests render the synthetic lessons of ``tests/deck_fit_fixtures.py`` in every theme and
run ``tools/deck_check.py`` over every slide; they are skipped when there is no Edge or Chrome (or
no ``websockets`` package) on the machine.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from classdeck.export import export_html, fit
from classdeck.export.html import theme_ids
from classdeck.lesson import Lesson
from tests.deck_fit_fixtures import (
    DOC_ID,
    make_fit_lesson,
    make_fit_sources,
    write_fit_images,
)
from tests.test_export_html import THEMES_README, Deck, Node, load

ROOT = Path(__file__).resolve().parent.parent


def load_checker() -> Any:
    spec = importlib.util.spec_from_file_location("deck_check", ROOT / "tools" / "deck_check.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deck_check"] = module
    spec.loader.exec_module(module)
    return module


# --- budgets (no browser) ----------------------------------------------------------------------


def test_paginate_balances_the_slides() -> None:
    cost: Callable[[int, int], float] = lambda i, j: 100.0 * (j - i)  # noqa: E731
    assert fit.paginate(0, cost, budget=500, max_items=5) == []
    assert fit.paginate(4, cost, budget=500, max_items=5) == [(0, 4)]
    # seven items, three to a slide: 3 + 2 + 2, not 3 + 3 + 1
    assert fit.paginate(7, cost, budget=900, max_items=3) == [(0, 3), (3, 5), (5, 7)]
    # an item taller than the budget stands alone
    tall = lambda i, j: 800.0 * (j - i)  # noqa: E731
    assert fit.paginate(2, tall, budget=500, max_items=5) == [(0, 1), (1, 2)]


def test_long_text_counts_for_more() -> None:
    short = fit.choice_item_height("Go ___ home.", ["a", "b", "c", "d"], 1, fit.CONTENT_WIDTH)
    long = fit.choice_item_height("Go ___ home. " * 8, ["a long option " * 3] * 4, 1, 1640)
    assert long > short * 2
    assert fit.text_rows("word " * 80, 800) > fit.text_rows("word " * 10, 800)


@pytest.fixture(scope="module")
def primary(tmp_path_factory: pytest.TempPathFactory) -> Deck:
    return render_fixture(tmp_path_factory.mktemp("fit"), secondary=False, theme="sunny")


def render_fixture(folder: Path, *, secondary: bool, theme: str) -> Deck:
    root = folder / "images"
    write_fit_images(root)
    lesson: Lesson = make_fit_lesson(secondary=secondary)
    path = export_html(
        lesson,
        folder / f"{lesson.id}-{theme}.html",
        sources={DOC_ID: make_fit_sources()},
        image_root=root,
        theme=theme,
    )
    return load(path)


def of(deck: Deck, kind: str) -> list[Node]:
    return deck.root.find("section", "slide", data_kind=kind)


def test_every_long_block_is_cut_into_numbered_slides(primary: Deck) -> None:
    for kind in ("choice", "gap_fill", "match", "true_false", "question_set", "sound_sort"):
        slides = of(primary, kind)
        assert len(slides) > 1, kind
        parts = [s.one(class_="ex__part").text() for s in slides]
        # the numbers run per exercise: (1/n) ... (n/n)
        assert parts[0].startswith("(1/"), (kind, parts)
    labels = [s.one(class_="rule-box__part").text() for s in of(primary, "rule_box")]
    assert labels and labels[0].startswith("(1/") and labels[-1].startswith(f"({len(labels)}/")


def test_item_budgets(primary: Deck) -> None:
    for slide in of(primary, "gap_fill"):
        assert len(slide.find(class_="gap-item")) <= fit.MAX_GAP_ITEMS
    for slide in of(primary, "choice"):
        assert len(slide.find(class_="choice")) <= fit.MAX_CHOICE_ITEMS_FOUR_OPTIONS
    for slide in of(primary, "true_false"):
        assert len(slide.find(class_="tf")) <= fit.MAX_TF_ITEMS
    for slide in of(primary, "question_set"):
        assert len(slide.find(class_="qset__q")) <= fit.MAX_QUESTIONS
    for slide in of(primary, "dialogue"):
        assert len(slide.find(class_="bubble")) <= fit.MAX_DIALOGUE_LINES
    for slide in of(primary, "activity"):
        assert len(slide.find(class_="step")) <= fit.MAX_STEPS
    for slide in of(primary, "sound_sort"):
        assert len(slide.find(class_="sort__item")) <= fit.MAX_SORT_WORDS
        assert len(slide.find(class_="sort__row")) <= fit.MAX_SORT_SENTENCES
    for slide in of(primary, "match"):
        assert len(slide.find(class_="match__left")[0].find("li")) <= fit.MAX_PICTURE_MATCH_ITEMS


def test_split_exercises_keep_every_item_once_and_in_order(primary: Deck) -> None:
    numbers = [
        int(n.text()) for slide in of(primary, "true_false") for n in slide.find(class_="tf__no")
    ]
    assert numbers == list(range(1, 10))
    steps = [
        n.text() for slide in of(primary, "activity") for n in slide.find(class_="step__number")
    ]
    assert steps == [str(n) for n in range(1, 8)]


def test_pictures_in_a_row_share_slides(primary: Deck) -> None:
    galleries = [s for s in of(primary, "image_ref") if s.find(class_="gallery")]
    assert len(galleries) == 2  # seven pictures: 4 + 3, never one slide each
    counts = [len(s.find(class_="gallery__item")) for s in galleries]
    assert counts == [4, 3]
    assert all(c <= fit.MAX_GALLERY_PICTURES for c in counts)
    assert [s.one(class_="gallery__part").text() for s in galleries] == ["(1/2)", "(2/2)"]


def test_new_exercise_kinds_render_and_split(primary: Deck) -> None:
    galleries = of(primary, "gallery")
    # six captioned pictures take two rows: the questions go on a second slide
    assert len(galleries) == 2
    assert [n.text() for n in galleries[0].find(class_="gal__label")] == list("abcdef")
    assert not galleries[1].find(class_="gal__picture")
    assert sum(len(g.find(class_="gal__q")) for g in galleries) == 3
    assert galleries[0].find(class_="gal__picture")[0].attrs["alt"] == "Habit 1"
    grids = of(primary, "grid")
    assert len(grids) >= 2  # five long questions, a blank row, three empty rows
    assert sum(len(g.find(class_="grid__row")) for g in grids) == 9
    blanks = [b.attrs["data-answer"] for g in grids for b in g.find(class_="gap")]
    assert blanks == ["Mai", "Nam"]
    checks = of(primary, "self_check")
    assert len(checks) >= 2  # eight long lines do not fit on one slide
    assert all(len(c.find(class_="sc__btn")) % 3 == 0 for c in checks)
    assert [n.text() for n in checks[0].find(class_="sc__btn")][:3] == ["✓", "✓✓", "✓✓✓"]


def test_chips_and_paragraph_pictures(primary: Deck) -> None:
    chips = [c.text() for s in of(primary, "dialogue") for c in s.find(class_="dialogue__chip")]
    assert chips == ["I think so", "of the conversation"]  # once, under the last part
    pictures = [p for s in of(primary, "reading") for p in s.find(class_="reading__para-picture")]
    assert len(pictures) == 2
    assert not [p for s in of(primary, "reading") for p in s.find(class_="reading__picture")]


def test_every_class_of_the_fit_deck_is_documented(primary: Deck) -> None:
    readme = THEMES_README.read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in primary.root.walk():
        emitted |= node.classes
    assert sorted(c for c in emitted if f".{c}" not in readme and c not in readme) == []


def test_the_summary_is_cut_when_it_has_many_words(primary: Deck) -> None:
    slides = of(primary, "summary")
    assert len(slides) == 2
    titles = [s.one(class_="summary__title").text() for s in slides]
    assert titles == ["Let's remember! (1/2)", "Let's remember! (2/2)"]
    cards = sum(len(s.find(class_="summary-card")) for s in slides)
    assert cards == 12


# --- the browser gate --------------------------------------------------------------------------


def browser_or_skip() -> Any:
    pytest.importorskip("websockets", reason="the deck checker needs the websockets package")
    checker = load_checker()
    if checker.find_browser() is None:
        pytest.skip("no Edge or Chrome on this machine: the deck checker cannot run")
    return checker


SUNNY_STATES = ("pupil", "teacher", "notes", "edit")
OTHER_STATES = ("pupil", "notes")


@pytest.fixture(scope="module")
def results(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``(lesson id, theme)`` to the checker's results; the decks are checked four at a time."""
    checker = browser_or_skip()
    folder = tmp_path_factory.mktemp("decks")
    jobs: list[tuple[str, str, Path, tuple[str, ...]]] = []
    for secondary in (False, True):
        for theme in theme_ids():
            deck = render_fixture(folder / f"{secondary}-{theme}", secondary=secondary, theme=theme)
            lesson_id = make_fit_lesson(secondary=secondary).id
            states = SUNNY_STATES if theme == "sunny" else OTHER_STATES
            jobs.append((lesson_id, theme, deck.path, states))

    def run(job: tuple[str, str, Path, tuple[str, ...]]) -> list[dict[str, Any]]:
        return checker.check_deck(job[2], states=job[3])  # type: ignore[no-any-return]

    with ThreadPoolExecutor(4) as pool:
        done = list(pool.map(run, jobs))
    return {(job[0], job[1]): result for job, result in zip(jobs, done, strict=True)}


def describe(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        if row["problems"]:
            first = row["problems"][0]
            lines.append(
                f"{row['state']} slide {row['number']} ({row['kind']}): {first['kind']} "
                f"{first['el']} {first['detail']}"
            )
    return "\n".join(lines[:12])


@pytest.mark.parametrize("secondary", [False, True], ids=["primary", "secondary"])
@pytest.mark.parametrize("theme", theme_ids())
def test_every_slide_fits_in_every_theme(
    results: dict[tuple[str, str], list[dict[str, Any]]], theme: str, secondary: bool
) -> None:
    lesson_id = "l_fit_secondary" if secondary else "l_fit_primary"
    rows = results[(lesson_id, theme)]
    assert rows, "the checker visited no slide"
    bad = [row for row in rows if not row["pass"]]
    assert not bad, describe(bad)
    assert all(row["fit"]["revealed"] >= 0.75 for row in rows)
