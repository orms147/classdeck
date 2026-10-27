from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.editing import MAX_EXTRA_SPANS, EditRequest, build_edit_context, render_context
from classdeck.english.validators import lookup_ipa
from classdeck.lesson import BlockAdapter, Lesson, Phase, Section, SourceDocument, Span, SpanKind
from tests.test_lesson_schema import DOC_ID, make_lesson, make_source_document

CURRICULUM_ROOT = Path(__file__).resolve().parents[1] / "curriculum"
CURRICULUM = load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 3)

GREETINGS_SPAN = "p6.s7"
GREETINGS_TEXT = "Hello, I'm Ha. Hi, Ha. I'm Tien. Nice to meet you. Goodbye, Tien. Bye, Ha."
VOCABULARY = [
    ("hello", "xin chào", "Hello, I'm Ha."),
    ("hi", "chào", "Hi, Ha."),
    ("goodbye", "tạm biệt", "Goodbye, Tien."),
    ("bye", "chào tạm biệt", "Bye, Ha."),
    ("nice", "vui", "Nice to meet you."),
    ("meet", "gặp", "Nice to meet you."),
]
FOUR = ["hello", "hi", "goodbye", "bye"]
HIDDEN_BLOCKS = ("b_04", "b_05", "b_07", "b_08", "b_10", "b_11")
REQUEST = EditRequest(target_block_ids=["b_02"], instruction="Reduce new words to four")


def cite(span_id: str) -> dict[str, Any]:
    return {"doc_id": DOC_ID, "page": 6, "span_id": span_id}


def make_edit_source_document() -> SourceDocument:
    """The schema test page plus a greetings paragraph that the six-word vocabulary cites."""
    document = make_source_document()
    document.pages[0].spans.append(
        Span(span_id=GREETINGS_SPAN, kind=SpanKind.PARAGRAPH, text=GREETINGS_TEXT, order=7)
    )
    return document


SOURCES = {DOC_ID: make_edit_source_document()}


def vocabulary_items(words: list[str] | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for word, meaning, example in VOCABULARY:
        if words is not None and word not in words:
            continue
        item: dict[str, Any] = {
            "word": word,
            "ipa": lookup_ipa(word),
            "pos": "interjection",
            "meaning_vi": meaning,
            "example": example,
        }
        if word == "hello":
            item["image"] = cite("p6.s4")
        items.append(item)
    return items


def make_edit_lesson() -> Lesson:
    """Five phases around the schema test lesson.

    Vocabulary ``b_02`` has six cited words; the generated activity ``b_07`` depends on it and
    on the pattern ``b_03``; the derived activity ``b_10``, the summary ``b_08`` and (through
    ``b_07``) the teacher note ``b_11`` depend on the vocabulary too.
    """
    lesson = make_lesson()
    blocks: list[dict[str, Any]] = [
        {
            "id": "b_02",
            "kind": "vocabulary",
            "content": {"items": vocabulary_items()},
            "sources": [cite(GREETINGS_SPAN)],
            "provenance": "extracted",
            "checks": [
                {
                    "name": "grounding.citation_supported",
                    "status": "pass",
                    "detail": "lexical overlap 0.68",
                },
                {"name": "english.ipa_present", "status": "pass"},
            ],
        },
        {
            "id": "b_04",
            "kind": "activity",
            "content": {
                "type": "chant",
                "title": "Hello chant",
                "skills": ["speaking"],
                "interaction": "whole_class",
                "minutes": 4,
                "instructions": "Listen and sing.",
                "teacher_steps": ["Chant the first line.", "Clap the rhythm."],
                "student_steps": ["Listen.", "Sing."],
                "materials": ["flashcards"],
            },
            "provenance": "generated",
        },
        {
            "id": "b_05",
            "kind": "dialogue",
            "content": {
                "context": "Ha meets Tien.",
                "lines": [
                    {"speaker": "Ha", "text": "Hello, I'm Ha."},
                    {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
                ],
            },
            "sources": [cite("p6.s3")],
            "provenance": "extracted",
        },
        {
            "id": "b_08",
            "kind": "summary",
            "content": {"bullets": ["hello, hi, goodbye, bye, nice, meet", "Hello, I'm ___."]},
            "provenance": "derived",
            "depends_on": ["b_02", "b_07"],
        },
        {
            "id": "b_10",
            "kind": "activity",
            "content": {
                "type": "listen_point_say",
                "title": "Listen, point and say",
                "skills": ["listening", "speaking"],
                "interaction": "pairs",
                "minutes": 8,
                "instructions": "Listen, point and say.",
                "teacher_steps": ["Say a word.", "Check the pointing."],
                "student_steps": ["Point to the ball.", "Say hello."],
                "materials": ["flashcards"],
            },
            "provenance": "derived",
            "depends_on": ["b_02"],
        },
        {
            "id": "b_11",
            "kind": "teacher_note",
            "content": {"text": "Praise effort, not only accuracy."},
            "provenance": "generated",
            "depends_on": ["b_07"],
        },
    ]
    for data in blocks:
        lesson.blocks[data["id"]] = BlockAdapter.validate_python(data)
    lesson.blocks["b_07"].depends_on = ["b_02", "b_03"]

    presentation, production, wrap_up = lesson.sections
    presentation.block_ids.append("b_05")
    production.block_ids.append("b_11")
    lesson.sections = [
        Section(
            id="s_warm_up",
            phase=Phase.WARM_UP,
            title="Warm-up: Hello chant",
            minutes=5,
            block_ids=["b_04"],
        ),
        presentation,
        Section(
            id="s_practice",
            phase=Phase.PRACTICE,
            title="Practice: Listen, point and say",
            minutes=12,
            block_ids=["b_10"],
        ),
        production,
        wrap_up,
    ]
    return Lesson.model_validate(lesson.model_dump())


# --- fixture sanity ----------------------------------------------------------------------------


def test_fixture_shape() -> None:
    lesson = make_edit_lesson()
    assert [section.phase for section in lesson.sections] == list(Phase)
    assert sum(section.minutes for section in lesson.sections) == 35
    assert [block.id for block in lesson.ordered_blocks()] == [
        "b_04",
        "b_01",
        "b_02",
        "b_03",
        "b_05",
        "b_10",
        "b_07",
        "b_11",
        "b_08",
    ]
    assert lesson.dependents_of("b_02") == ["b_10", "b_07", "b_11", "b_08"]
    assert SOURCES[DOC_ID].find_span(6, GREETINGS_SPAN) is not None


# --- build_edit_context ------------------------------------------------------------------------


def test_context_contains_targets_cited_spans_neighbours_and_section() -> None:
    context = build_edit_context(
        make_edit_lesson(), REQUEST, sources=SOURCES, curriculum=CURRICULUM
    )
    assert [block.id for block in context.target_blocks] == ["b_02"]
    assert [(citation.span_id, text) for citation, text in context.cited_spans] == [
        (GREETINGS_SPAN, GREETINGS_TEXT)
    ]
    assert [block.id for block in context.neighbours] == ["b_01", "b_03"]
    assert context.section is not None
    assert context.section.id == "s_presentation"


def test_context_has_rules_outcomes_and_the_sorted_unit_word_list() -> None:
    context = build_edit_context(
        make_edit_lesson(), REQUEST, sources=SOURCES, curriculum=CURRICULUM
    )
    assert "Stay inside this scope" in context.rules
    assert "present simple with be and have" in context.rules
    assert context.outcomes == [("ta3.u1.o1", "Greet and respond to greetings using Hello / Hi.")]
    assert {"hello", "goodbye", "nice", "meet", "ruler"} <= set(context.word_list_excerpt)
    assert context.word_list_excerpt == sorted(context.word_list_excerpt)


def test_context_without_curriculum_has_builtin_rules_and_no_word_list() -> None:
    context = build_edit_context(make_edit_lesson(), REQUEST, sources=SOURCES)
    assert "Language scope for grade 3" in context.rules
    assert context.outcomes == []
    assert context.word_list_excerpt == []


def test_extra_spans_come_from_the_cited_pages_ranked_by_instruction_overlap() -> None:
    request = EditRequest(
        target_block_ids=["b_02"], instruction="Use the look, listen and repeat line"
    )
    context = build_edit_context(make_edit_lesson(), request, sources=SOURCES)
    ids = [citation.span_id for citation, _ in context.extra_spans]
    assert ids[0] == "p6.s2"  # shares look, listen, and, repeat
    assert ids[1:] == ["p6.s1", "p6.s3", "p6.s6"]  # no overlap: page order; image and cited out
    assert all(c.doc_id == DOC_ID and c.page == 6 for c, _ in context.extra_spans)
    texts = {citation.span_id: text for citation, text in context.extra_spans}
    assert texts["p6.s2"] == "1. Look, listen and repeat."


def test_extra_spans_are_capped() -> None:
    document = make_edit_source_document()
    document.pages[0].spans.extend(
        Span(span_id=f"p6.x{n}", kind=SpanKind.PARAGRAPH, text=f"Extra line {n}", order=100 + n)
        for n in range(30)
    )
    context = build_edit_context(make_edit_lesson(), REQUEST, sources={DOC_ID: document})
    assert len(context.extra_spans) == MAX_EXTRA_SPANS == 20


def test_cited_span_without_sources_falls_back_to_the_quote() -> None:
    lesson = make_edit_lesson()
    lesson.blocks["b_02"].sources[0].quote = "Hello, I'm Ha."
    context = build_edit_context(lesson, REQUEST)
    assert [text for _, text in context.cited_spans] == ["Hello, I'm Ha."]
    assert context.extra_spans == []


def test_neighbours_of_several_targets_skip_the_targets_themselves() -> None:
    request = EditRequest(target_block_ids=["b_02", "b_03"], instruction="Simplify")
    context = build_edit_context(make_edit_lesson(), request)
    assert [block.id for block in context.target_blocks] == ["b_02", "b_03"]
    assert [block.id for block in context.neighbours] == ["b_01", "b_05"]


def test_unplaced_target_has_no_section_and_no_neighbours() -> None:
    lesson = make_edit_lesson()
    lesson.sections[3].block_ids.remove("b_11")
    request = EditRequest(target_block_ids=["b_11"], instruction="Shorten")
    context = build_edit_context(lesson, request)
    assert context.section is None
    assert context.neighbours == []
    assert "not placed in any section" in render_context(context, request)


def test_unknown_target_raises_key_error_naming_the_id() -> None:
    request = EditRequest(target_block_ids=["b_02", "b_99"], instruction="x")
    with pytest.raises(KeyError, match="b_99"):
        build_edit_context(make_edit_lesson(), request)


# --- render_context ----------------------------------------------------------------------------


def test_render_context_shows_the_selection_and_hides_the_rest() -> None:
    context = build_edit_context(
        make_edit_lesson(), REQUEST, sources=SOURCES, curriculum=CURRICULUM
    )
    text = render_context(context, REQUEST)
    assert "## Instruction\nReduce new words to four" in text
    assert "edit only these: b_02" in text
    assert '"id": "b_02"' in text
    assert '"word": "goodbye"' in text
    assert "xin chào" in text  # meaning_vi is written as is, not escaped
    assert f"- {GREETINGS_SPAN} (page 6, {DOC_ID}): {GREETINGS_TEXT}" in text
    assert "Presentation: Look, listen and repeat (phase: presentation, 10 min)" in text
    assert '"id": "b_01"' in text
    assert '"id": "b_03"' in text
    for hidden in HIDDEN_BLOCKS:
        assert hidden not in text
    assert "## Rules\n- Vocabulary and sentence patterns come from the cited unit." in text
    assert "- ta3.u1.o1: Greet and respond to greetings using Hello / Hi." in text
    word_list = text.split("## Unit word list\n")[1]
    assert "goodbye" in word_list.split(", ")


def test_render_context_without_curriculum_or_sources_says_so() -> None:
    context = build_edit_context(make_edit_lesson(), REQUEST)
    text = render_context(context, REQUEST)
    assert text.count("(not available)") == 2
    assert f"- {GREETINGS_SPAN} (page 6, {DOC_ID}):" in text
    assert "## Other spans on the same pages, most relevant first\n(none)" in text
