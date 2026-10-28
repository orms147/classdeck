"""Curriculum units built on the fly from the ingested pages (``curriculum/auto.py``), unit-number
parsing and the teacher-facing lesson labels (``lesson/labels.py``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from classdeck.curriculum.auto import (
    curriculum_for,
    parse_unit_number,
    primary_unit,
    secondary_unit,
    unit_id_for,
)
from classdeck.english.validators import run_all
from classdeck.ingest import ingest
from classdeck.lesson import Lesson, SourceDocument, lesson_label_for, unit_line, unit_number
from classdeck.lesson.schema import Page, Span, SpanKind
from classdeck.pipeline import build_lesson, build_secondary_lesson
from classdeck.planning import outline as build_outline

ROOT = Path(__file__).resolve().parents[1]
CURRICULUM = ROOT / "curriculum"
FIXTURES = ROOT / "eval" / "fixtures" / "public"


@pytest.fixture(scope="module")
def primary_document() -> SourceDocument:
    return ingest(FIXTURES / "gs3-unit2.pdf")


def secondary_document(*items: tuple[str, str]) -> SourceDocument:
    spans = [
        Span(span_id=f"p1.s{order}", kind=SpanKind(kind), text=text, order=order)
        for order, (kind, text) in enumerate(items, start=1)
    ]
    return SourceDocument(
        doc_id="d_auto0001",
        filename="unit.pdf",
        sha256="0" * 64,
        media_type="application/pdf",
        pages=[Page(number=1, spans=spans)],
    )


# --- unit numbers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "grade", "number"),
    [
        (1, 3, 1),
        ("1", None, 1),
        (" 12 ", 9, 12),
        ("u4", 5, 4),
        ("ta3.u2", 3, 2),
        ("TA9.U10", 9, 10),
    ],
)
def test_unit_numbers_are_parsed_from_numbers_and_old_ids(
    value: str | int, grade: int | None, number: int
) -> None:
    assert parse_unit_number(value, grade) == number


@pytest.mark.parametrize("value", ["", "x", "unit one", "0", "100", "ta3", "3.5", -1])
def test_bad_unit_numbers_are_refused(value: str | int) -> None:
    with pytest.raises(ValueError, match="unit"):
        parse_unit_number(value)


def test_an_id_of_another_grade_is_refused() -> None:
    with pytest.raises(ValueError, match="grade 3, not grade 4"):
        parse_unit_number("ta3.u1", 4)


def test_unit_ids_are_built_from_grade_and_number() -> None:
    assert unit_id_for(2, 7) == "ta2.u7"


# --- primary, no curriculum file ---------------------------------------------------------------


def test_a_grade_without_a_file_gets_a_unit_built_from_the_pages(
    primary_document: SourceDocument,
) -> None:
    curriculum = curriculum_for(
        1, 2, {primary_document.doc_id: primary_document}, curriculum_dir=CURRICULUM
    )
    unit = curriculum.unit("ta1.u2")
    assert unit.title == "Our names"
    assert unit.textbook_units[0].unit == 2 and unit.lessons == 3
    assert {"name", "spell"} <= set(unit.vocabulary)
    assert "my" not in unit.vocabulary  # function words are not key words
    assert any("My name's ___" in pattern.pattern for pattern in unit.patterns)
    assert unit.outcomes and all(o.id.startswith("ta1.u2.o") for o in unit.outcomes)
    assert curriculum.file.grade == 1 and not curriculum.is_secondary
    assert curriculum.uses_unit_words and curriculum.period_minutes == 35


def test_a_primary_unit_missing_from_the_file_drops_the_closed_word_list(
    primary_document: SourceDocument,
) -> None:
    sources = {primary_document.doc_id: primary_document}
    listed = curriculum_for(3, 2, sources, curriculum_dir=CURRICULUM)
    assert listed.unit("ta3.u2").title == "Our names"
    assert not listed.uses_unit_words  # the grade's word list applies to a listed unit
    built = curriculum_for(3, 17, sources, curriculum_dir=CURRICULUM)
    assert built.unit("ta3.u17").title == "Our names"
    assert built.uses_unit_words
    assert built.file.grammar_scope == listed.file.grammar_scope


def test_a_listed_unit_comes_from_the_file_untouched(primary_document: SourceDocument) -> None:
    curriculum = curriculum_for(3, "ta3.u1", [primary_document], curriculum_dir=CURRICULUM)
    assert curriculum.unit("ta3.u1").title == "Hello"
    assert curriculum.unit("ta3.u1").outcomes[0].id == "ta3.u1.o1"


def test_grades_outside_one_to_nine_are_refused(primary_document: SourceDocument) -> None:
    for grade in (0, 10):
        with pytest.raises(ValueError, match="grade"):
            curriculum_for(grade, 1, [primary_document], curriculum_dir=CURRICULUM)


def test_a_built_primary_unit_runs_through_the_pipeline(primary_document: SourceDocument) -> None:
    sources = {primary_document.doc_id: primary_document}
    curriculum = curriculum_for(2, 2, sources, curriculum_dir=CURRICULUM)
    outline = build_outline(
        [primary_document], curriculum, unit_id="ta2.u2", lesson_no=1, period=1, grade=2
    )
    lesson = build_lesson(outline, sources, curriculum, None)
    assert lesson.grade == 2 and lesson.curriculum.unit_id == "ta2.u2"
    assert lesson.title == "Grade 2 · Lesson 1 · Period 1"
    assert (lesson.curriculum.unit_no, lesson.curriculum.unit_title) == (2, "Our names")
    # no closed word list: the unit-pages check runs, the grade list check does not
    names = {check.name for block in lesson.blocks.values() for check in block.checks}
    assert "english.words_from_unit" in names and "english.vocab_level" not in names
    assert run_all(lesson, curriculum, sources=sources).blocks


def test_primary_unit_without_headings_gets_a_plain_title() -> None:
    spans = [Span(span_id="p1.s1", kind=SpanKind.PARAGRAPH, text="Hello, I'm ___.", order=1)]
    unit = primary_unit(1, 5, spans)
    assert unit.title == "Unit 5"
    assert [p.pattern for p in unit.patterns] == ["Hello, I'm ___."]


# --- secondary, no curriculum file -------------------------------------------------------------

BOX_PAGE = (
    ("heading", "UNIT 3 MY NEIGHBOURHOOD"),
    ("heading", "THIS UNIT INCLUDES:"),
    ("paragraph", "Vocabulary | places in a neighbourhood"),
    ("paragraph", "Grammar: comparative adjectives"),
    ("paragraph", "Everyday English | asking for directions"),
    ("heading", "GETTING STARTED"),
    ("instruction", "1. Listen and read."),
    ("paragraph", "Lan: Where is the library?"),
    ("paragraph", "Nam: It is next to the post office."),
    ("heading", "A CLOSER LOOK 1"),
    ("instruction", "1. Match the words with their meanings."),
)


def test_a_secondary_unit_reads_its_title_and_the_unit_includes_box() -> None:
    document = secondary_document(*BOX_PAGE)
    curriculum = curriculum_for(7, 3, [document], curriculum_dir=CURRICULUM)
    unit = curriculum.unit("ta7.u3")
    assert unit.title == "My neighbourhood"
    assert unit.lessons == 7
    texts = [o.text for o in unit.outcomes]
    assert texts[:3] == [
        "Vocabulary: places in a neighbourhood.",
        "Grammar: comparative adjectives.",
        "Everyday english: asking for directions.",
    ]
    covered = {n for outcome in unit.outcomes for n in outcome.lessons}
    assert covered == set(range(1, 8))  # every textbook lesson has an outcome
    assert curriculum.is_secondary and curriculum.uses_unit_words
    assert curriculum.period_minutes == 45


def test_a_secondary_unit_without_the_box_gets_one_outcome_for_all_lessons() -> None:
    document = secondary_document(
        ("heading", "GETTING STARTED"),
        ("heading", "UNIT 5: WORK"),
        ("instruction", "1. Listen and read."),
    )
    unit = secondary_unit(8, 5, document.pages[0].spans)
    assert unit.title == "Work"  # the banner is not a title
    assert len(unit.outcomes) == 1 and unit.outcomes[0].lessons == [1, 2, 3, 4, 5, 6, 7]
    assert unit.outcomes[0].skills == []


def test_a_secondary_unit_with_no_heading_is_named_by_its_number() -> None:
    document = secondary_document(("paragraph", "Lan: Hello."))
    assert curriculum_for(6, 9, [document], curriculum_dir=CURRICULUM).unit("ta6.u9").title == (
        "Unit 9"
    )


def test_a_built_secondary_unit_drafts_a_lesson() -> None:
    document = secondary_document(*BOX_PAGE)
    sources = {document.doc_id: document}
    curriculum = curriculum_for(6, 3, sources, curriculum_dir=CURRICULUM)
    lesson, _ = build_secondary_lesson(sources, curriculum, None, unit_id="ta6.u3", lesson_no=1)
    assert isinstance(lesson, Lesson)
    assert lesson.title == "Grade 6 · Lesson 1 · Getting Started"
    assert unit_line(lesson) == "Unit 3 · My neighbourhood"


# --- labels ------------------------------------------------------------------------------------


def test_lesson_labels_show_the_period_only_when_there_is_one() -> None:
    assert lesson_label_for(3, 1, 2) == "Grade 3 · Lesson 1 · Period 2"
    assert lesson_label_for(9, 4, None) == "Grade 9 · Lesson 4 · Communication"
    assert lesson_label_for(2, None, None) == "Grade 2"


def test_old_lessons_get_their_unit_line_from_the_id_and_the_old_title() -> None:
    lesson = Lesson.model_validate(
        {
            "id": "l_old",
            "title": "Unit 4 Our friends — Lesson 1, Period 1",
            "grade": 3,
            "curriculum": {"version": "gdpt-2018", "unit_id": "ta3.u4", "lesson_no": 1},
            "sources": [],
            "sections": [],
            "blocks": {},
        }
    )
    assert unit_number(lesson.curriculum) == 4
    assert unit_line(lesson) == "Unit 4 · Our friends"
