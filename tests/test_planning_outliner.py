"""Outline skeleton (deterministic) and refinement (model, validated)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.ingest import ingest
from classdeck.lesson import ActivityType, Page, Phase, SourceDocument, Span
from classdeck.llm import LLMError, MockProvider
from classdeck.planning import (
    Outline,
    build_skeleton,
    outline,
    refine_outline,
    scale_minutes,
    span_text,
)
from classdeck.planning.outliner import outcome_terms

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "public"
CURRICULUM = load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)
PHASES_PERIOD_1 = [
    Phase.WARM_UP,
    Phase.PRESENTATION,
    Phase.PRACTICE,
    Phase.PRODUCTION,
    Phase.WRAP_UP,
]


@pytest.fixture(scope="module")
def document() -> SourceDocument:
    return ingest(FIXTURES / "mini-unit-hello.pdf")


def skeleton(document: SourceDocument, lesson_no: int = 1, period: int = 1) -> Outline:
    return build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=lesson_no, period=period, grade=3
    )


def make_document(spans: list[tuple[str, str]], doc_id: str = "d_notes") -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        filename="notes.docx",
        sha256="0" * 64,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        pages=[
            Page(
                number=1,
                spans=[
                    Span(span_id=f"p1.s{order}", kind=kind, text=text, order=order)
                    for order, (kind, text) in enumerate(spans, start=1)
                ],
            )
        ],
    )


def refs(result: Outline, index: int) -> list[str]:
    return [ref.span_id for ref in result.sections[index].span_refs]


def payload(result: Outline) -> dict[str, Any]:
    """A refinement reply that changes nothing."""
    return {
        "sections": [
            {
                "title": section.title,
                "outcome_ids": list(section.outcome_ids),
                "notes": section.notes,
                "span_ids": refs(result, index),
            }
            for index, section in enumerate(result.sections)
        ]
    }


# --- skeleton ----------------------------------------------------------------------------------


def test_skeleton_shape_and_timing(document: SourceDocument) -> None:
    result = skeleton(document)
    assert [section.phase for section in result.sections] == PHASES_PERIOD_1
    assert [section.minutes for section in result.sections] == [5, 10, 12, 6, 2]
    assert result.total_minutes() == CURRICULUM.period_minutes == 35
    assert result.title == "Unit 1 Hello — Lesson 1, Period 1"
    assert result.grade == 3
    assert result.doc_ids == [document.doc_id]
    ref = result.curriculum
    assert (ref.version, ref.unit_id, ref.lesson_no, ref.period) == ("gdpt-2018", "ta3.u1", 1, 1)
    assert ref.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2"]
    assert Outline.model_validate_json(result.model_dump_json()) == result


def test_presentation_section_maps_activity_one(document: SourceDocument) -> None:
    section = skeleton(document).sections[1]
    assert section.activity_type is ActivityType.LOOK_LISTEN_REPEAT
    assert section.title == "1. Look, listen and repeat."
    texts = [span_text(document, ref) for ref in section.span_refs]
    assert "1. Look, listen and repeat." in texts
    assert "Hello, I'm Ha." in texts
    assert [ref.page for ref in section.span_refs] == [1] * 5
    assert section.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2"]
    assert section.notes == ""


def test_framing_sections_have_no_outcomes_or_spans(document: SourceDocument) -> None:
    result = skeleton(document)
    warm_up, wrap_up = result.sections[0], result.sections[-1]
    # lesson 1, period 1 teaches the pattern for the first time: its warm-up is a TPR
    assert (warm_up.title, warm_up.activity_type) == (
        "Warm-up: listen and do",
        ActivityType.TPR,
    )
    assert (wrap_up.title, wrap_up.activity_type) == ("Wrap-up", None)
    for section in (warm_up, wrap_up):
        assert section.outcome_ids == []
        assert section.span_refs == []


def test_warm_up_is_a_tpr_only_in_the_first_period_of_lesson_one(document: SourceDocument) -> None:
    assert skeleton(document, 1, 1).sections[0].activity_type is ActivityType.TPR
    # later periods keep the pattern game: the pattern has been taught by then
    for lesson_no, period in ((1, 2), (2, 1), (2, 2), (3, 1), (3, 2)):
        warm_up = skeleton(document, lesson_no, period).sections[0]
        assert (warm_up.title, warm_up.activity_type) == ("Warm-up", ActivityType.GAME)
        assert warm_up.review is False


def test_activity_types_follow_the_lesson_shape(document: SourceDocument) -> None:
    assert [section.activity_type for section in skeleton(document).sections[1:4]] == [
        ActivityType.LOOK_LISTEN_REPEAT,
        ActivityType.LISTEN_POINT_SAY,
        ActivityType.LETS_TALK,
    ]


def test_period_two_maps_chunks_four_to_six(document: SourceDocument) -> None:
    result = skeleton(document, period=2)
    assert [section.phase for section in result.sections] == [
        Phase.WARM_UP,
        Phase.PRESENTATION,
        Phase.PRACTICE,
        Phase.PRACTICE,
        Phase.PRODUCTION,
        Phase.WRAP_UP,
    ]
    assert [section.title for section in result.sections[2:5]] == [
        "4. Listen and tick.",
        "5. Look, complete and read.",
        "6. Let's sing.",
    ]
    assert all(ref.page == 2 for section in result.sections[2:] for ref in section.span_refs)
    assert refs(result, 2) == ["p2.s1", "p2.s2", "p2.s3"]
    assert result.total_minutes() == 35
    # warm-up 4, review 8, activities 4-5 together 15, activity 6 in 6, wrap-up 2
    assert [section.minutes for section in result.sections] == [4, 8, 8, 7, 6, 2]
    assert result.curriculum.period == 2


def test_period_two_presentation_reviews_the_dialogue_and_pattern_spans(
    document: SourceDocument,
) -> None:
    """Period 2 has a presentation stage that reviews: the text spans of activities 1-2, no
    headings, instructions or images (docs/planning.md, "Period 2 shape")."""
    result = skeleton(document, period=2)
    warm_up, review = result.sections[0], result.sections[1]
    assert (warm_up.phase, warm_up.review, warm_up.span_refs) == (Phase.WARM_UP, False, [])
    assert warm_up.activity_type is ActivityType.GAME
    assert review.review is True
    assert review.phase is Phase.PRESENTATION
    assert review.title == "Review: say it again"
    assert review.activity_type is ActivityType.LOOK_LISTEN_REPEAT
    assert review.outcome_ids == []
    assert refs(result, 1) == ["p1.s4", "p1.s5", "p1.s7", "p1.s8"]
    assert all(ref.page == 1 for ref in review.span_refs)
    assert [span_text(document, ref) for ref in review.span_refs] == [
        "Hello, I'm Ha.",
        "Hi, Ha. I'm Tien.",
        "Hello, I'm ___.",
        "Hi, ___. I'm ___.",
    ]
    # period 1 keeps its shape; the field round-trips through JSON
    first = skeleton(document)
    assert [section.phase for section in first.sections] == PHASES_PERIOD_1
    assert not any(section.review for section in first.sections)
    assert Outline.model_validate_json(result.model_dump_json()).sections[1].review is True


def test_review_is_skipped_when_the_source_has_no_earlier_activities(
    caplog: pytest.LogCaptureFixture,
) -> None:
    document = make_document(
        [
            ("heading", "Unit 1 Hello (Lesson 1, Period 2)"),
            ("list_item", "4. Listen and tick."),
            ("paragraph", "a. Hello, I'm Duc. b. Hi, I'm Lan."),
            ("list_item", "5. Look, complete and read."),
            ("paragraph", "Hello, I'm _____."),
            ("list_item", "6. Let's sing."),
            ("paragraph", "Hello, hello, hello! I'm Ha."),
        ]
    )
    with caplog.at_level("INFO"):
        result = build_skeleton(
            [document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=2, grade=3
        )
    warm_up = result.sections[0]
    assert warm_up.review is False
    assert (warm_up.title, warm_up.span_refs) == ("Warm-up", [])
    assert "without a review" in caplog.text
    assert refs(result, 1) == ["p1.s1", "p1.s2", "p1.s3"]


def test_review_is_skipped_without_instruction_lines() -> None:
    notes = make_document([("paragraph", "Pupils listen and tick the greeting they hear.")])
    result = build_skeleton([notes], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=2, grade=3)
    assert result.sections[0].review is False
    assert result.sections[0].span_refs == []


def test_missing_chunk_gives_template_title_and_no_spans() -> None:
    document = make_document(
        [("list_item", "1. Look, listen and repeat."), ("paragraph", "Hello, I'm Ha.")]
    )
    result = build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=2, grade=3
    )
    assert [section.title for section in result.sections[2:5]] == [
        "Listen and tick",
        "Look, complete and read",
        "Let's sing",
    ]
    assert all(section.span_refs == [] for section in result.sections[2:])
    # activity 1 alone is enough to review
    assert result.sections[1].review is True
    assert refs(result, 1) == ["p1.s2"]


def test_outcomes_fall_back_to_the_skills_when_no_chunk_mentions_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notes = make_document(
        [
            ("list_item", "4. Do the first task."),
            ("paragraph", "A cat sits on a mat."),
            ("list_item", "5. Do the second task."),
            ("paragraph", "A dog runs in the park."),
            ("list_item", "6. Do the third task."),
            ("paragraph", "A bird flies over the tree."),
        ]
    )
    with caplog.at_level("INFO", logger="classdeck.planning.outliner"):
        result = build_skeleton(
            [notes], CURRICULUM, unit_id="ta3.u2", lesson_no=3, period=2, grade=3
        )
    read_and_match, lets_write, project = result.sections[1:4]
    assert read_and_match.outcome_ids == ["ta3.u2.o3"]
    assert lets_write.outcome_ids == ["ta3.u2.o3"]
    assert project.outcome_ids == ["ta3.u2.o1", "ta3.u2.o2", "ta3.u2.o3"]
    assert result.curriculum.outcome_ids == ["ta3.u2.o1", "ta3.u2.o2", "ta3.u2.o3"]
    assert result.title == "Unit 2 Our names — Lesson 3, Period 2"
    assert "assigning outcomes by skill" in caplog.text


def test_outcomes_follow_the_words_of_the_chunk(document: SourceDocument) -> None:
    """The mini unit never says goodbye, so o3 is assigned to no section and not to the lesson."""
    for period in (1, 2):
        result = skeleton(document, period=period)
        assigned = {oid for section in result.sections for oid in section.outcome_ids}
        assert "ta3.u1.o3" not in assigned
        assert result.curriculum.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2"]


def test_an_outcome_goes_only_to_the_sections_that_mention_it() -> None:
    notes = make_document(
        [
            ("list_item", "1. Look, listen and repeat."),
            ("paragraph", "Hello, I'm Ha."),
            ("paragraph", "Hi, Ha. I'm Tien."),
            ("list_item", "2. Listen, point and say."),
            ("paragraph", "Goodbye, Ha."),
            ("paragraph", "Bye, Tien."),
            ("list_item", "3. Let's talk."),
            ("paragraph", "Talk with a friend."),
        ]
    )
    result = build_skeleton([notes], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    presentation, practice, production = result.sections[1:4]
    assert presentation.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2"]
    assert practice.outcome_ids == ["ta3.u1.o3"]
    assert production.outcome_ids == []
    assert result.curriculum.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2", "ta3.u1.o3"]


def test_outcome_terms_add_the_unit_words_the_outcome_text_mentions() -> None:
    unit = CURRICULUM.unit("ta3.u1")
    goodbye = outcome_terms(unit, unit.outcomes[2])
    assert {"goodbye", "bye"} <= goodbye
    assert "say" not in goodbye
    introduce = outcome_terms(unit, unit.outcomes[1])
    assert "i'm" in introduce
    assert "introduce" in introduce


def test_fallback_to_bm25_without_instruction_lines() -> None:
    notes = make_document(
        [
            ("heading", "Unit 1 Hello - teaching notes"),
            ("paragraph", "Pupils look at the picture, listen and repeat the greetings."),
            ("paragraph", "Hello, I'm Ha. Hi, Ha. I'm Tien."),
            ("paragraph", "In pairs pupils point to a picture and say the pattern."),
            ("paragraph", "Pupils talk with a partner about their names."),
            ("image", ""),
        ]
    )
    result = build_skeleton([notes], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    presentation, practice, production = result.sections[1:4]
    assert presentation.title == "Look, listen and repeat"
    assert refs(result, 1)[0] == "p1.s2"
    assert refs(result, 2)[0] == "p1.s4"
    assert refs(result, 3) == ["p1.s5"]
    assert all(len(refs(result, index)) <= 6 for index in range(5))
    assert "p1.s6" not in refs(result, 1)
    assert practice.activity_type is ActivityType.LISTEN_POINT_SAY
    assert production.span_refs[0].doc_id == "d_notes"


def test_two_sources_are_listed_and_the_first_chunked_one_wins(document: SourceDocument) -> None:
    other = ingest(FIXTURES / "mini-unit-hello.docx")
    result = build_skeleton(
        [document, other], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3
    )
    assert result.doc_ids == [document.doc_id, other.doc_id]
    assert {ref.doc_id for ref in result.sections[1].span_refs} == {document.doc_id}


@pytest.mark.parametrize(("lesson_no", "period"), [(4, 1), (1, 3), (0, 1)])
def test_unknown_shape_raises(document: SourceDocument, lesson_no: int, period: int) -> None:
    with pytest.raises(ValueError, match=f"lesson {lesson_no}, period {period}"):
        skeleton(document, lesson_no=lesson_no, period=period)


def test_unknown_unit_raises(document: SourceDocument) -> None:
    with pytest.raises(KeyError, match=r"ta9\.u9"):
        build_skeleton([document], CURRICULUM, unit_id="ta9.u9", lesson_no=1, period=1, grade=3)


def test_minutes_scale_to_the_period_length(document: SourceDocument) -> None:
    longer = Curriculum(CURRICULUM.file.model_copy(update={"period_minutes": 40}))
    result = build_skeleton([document], longer, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    assert result.total_minutes() == 40
    assert [section.minutes for section in result.sections] == [6, 11, 14, 7, 2]


def test_scale_minutes_preserves_the_total() -> None:
    assert scale_minutes([5, 12, 12, 6, 2], 35) == [5, 11, 11, 6, 2]
    assert scale_minutes([5, 12, 6, 6, 2], 35) == [6, 13, 7, 7, 2]
    assert scale_minutes([5, 10, 12, 6, 2], 35) == [5, 10, 12, 6, 2]
    assert scale_minutes([], 35) == []


@pytest.mark.parametrize(("lesson_no", "period"), sorted({(1, 2), (2, 1), (2, 2), (3, 1), (3, 2)}))
def test_every_shape_times_to_the_period(
    document: SourceDocument, lesson_no: int, period: int
) -> None:
    result = skeleton(document, lesson_no, period)
    assert result.total_minutes() == 35
    assert len(result.sections) == (6 if period == 2 else 5)
    assert result.sections[0].phase is Phase.WARM_UP
    assert result.sections[-1].phase is Phase.WRAP_UP
    if period == 2:
        phases = [section.phase for section in result.sections]
        assert phases[1:] == [
            Phase.PRESENTATION,
            Phase.PRACTICE,
            Phase.PRACTICE,
            Phase.PRODUCTION,
            Phase.WRAP_UP,
        ]


# --- refinement --------------------------------------------------------------------------------


def test_refine_applies_a_valid_reply(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    reply["sections"][1].update(
        title="Meet Ha and Tien",
        notes="Dialogue introduces hello and hi.",
        outcome_ids=["ta3.u1.o1"],
    )
    reply["sections"][1]["span_ids"].append("p1.s7")
    reply["sections"][2]["span_ids"].remove("p1.s7")
    provider = MockProvider(responses=[json.dumps(reply)])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert len(provider.calls) == 1
    prompt = provider.calls[0]["messages"][0]["content"]
    assert "[p1.s4] Hello, I'm Ha." in prompt
    assert "ta3.u1.o2" in prompt
    assert "Language scope for grade 3" in prompt
    section = result.sections[1]
    assert section.title == "Meet Ha and Tien"
    assert section.notes == "Dialogue introduces hello and hi."
    assert section.outcome_ids == ["ta3.u1.o1"]
    assert refs(result, 1) == [*refs(base, 1), "p1.s7"]
    assert section.span_refs[-1].page == 1
    assert refs(result, 2) == ["p1.s6", "p1.s8"]
    assert [section.phase for section in result.sections] == PHASES_PERIOD_1
    assert [s.minutes for s in result.sections] == [s.minutes for s in base.sections]
    assert result.sections[0] == base.sections[0]
    assert result.curriculum.outcome_ids == base.curriculum.outcome_ids


def test_refine_recomputes_the_lesson_outcomes(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    for section in reply["sections"][1:4]:
        section["outcome_ids"] = ["ta3.u1.o2"]
    provider = MockProvider(responses=[json.dumps(reply)])
    result = refine_outline(base, [document], CURRICULUM, provider)
    assert result.curriculum.outcome_ids == ["ta3.u1.o2"]


def test_refine_rejects_a_removed_section_then_keeps_the_skeleton(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    reply["sections"].pop()
    bad = json.dumps(reply)
    provider = MockProvider(responses=[bad, bad])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert result == base
    assert len(provider.calls) == 2
    second = provider.calls[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert "added or removed" in second[-1]["content"]


def test_refine_rejects_an_invented_span_id(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    reply["sections"][1]["span_ids"].append("p9.s9")
    bad = json.dumps(reply)
    provider = MockProvider(responses=[bad, bad])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert result == base
    assert len(provider.calls) == 2
    assert "p9.s9" in provider.calls[1]["messages"][-1]["content"]


def test_refine_rejects_a_span_moved_past_a_neighbour(document: SourceDocument) -> None:
    base = skeleton(document)
    bad = payload(base)
    bad["sections"][1]["span_ids"].append("p1.s10")
    good = payload(base)
    good["sections"][3]["title"] = "Talk about names"
    provider = MockProvider(responses=[json.dumps(bad), json.dumps(good)])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert len(provider.calls) == 2
    assert "p1.s10" in provider.calls[1]["messages"][-1]["content"]
    assert result.sections[3].title == "Talk about names"
    assert refs(result, 1) == refs(base, 1)


def test_refine_rejects_outcomes_outside_the_unit(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    reply["sections"][2]["outcome_ids"] = ["ta3.u2.o1"]
    bad = json.dumps(reply)
    provider = MockProvider(responses=[bad, bad])
    assert refine_outline(base, [document], CURRICULUM, provider) == base
    assert "ta3.u2.o1" in provider.calls[1]["messages"][-1]["content"]


def test_refine_repairs_after_an_unparsable_reply(document: SourceDocument) -> None:
    base = skeleton(document)
    good = payload(base)
    good["sections"][0]["title"] = "Warm-up: greeting chain"
    provider = MockProvider(responses=["not json at all", json.dumps(good)])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert len(provider.calls) == 2
    assert result.sections[0].title == "Warm-up: greeting chain"


def test_refine_propagates_model_failures(document: SourceDocument) -> None:
    with pytest.raises(LLMError):
        refine_outline(skeleton(document), [document], CURRICULUM, MockProvider(responses=[]))


def test_outline_without_provider_is_the_skeleton(document: SourceDocument) -> None:
    result = outline([document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    assert result == skeleton(document)


def test_outline_with_provider_refines(document: SourceDocument) -> None:
    reply = payload(skeleton(document))
    reply["sections"][3]["title"] = "Talk about names"
    provider = MockProvider(responses=[json.dumps(reply)])
    result = outline(
        [document],
        CURRICULUM,
        unit_id="ta3.u1",
        lesson_no=1,
        period=1,
        grade=3,
        provider=provider,
    )
    assert result.sections[3].title == "Talk about names"
    assert len(provider.calls) == 1


def test_refine_rejects_dropped_spans_then_keeps_the_skeleton(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    for section in reply["sections"]:
        section["span_ids"] = []
    bad = json.dumps(reply)
    provider = MockProvider(responses=[bad, bad])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert result == base
    assert len(provider.calls) == 2
    assert "dropped" in provider.calls[1]["messages"][-1]["content"]
    assert "p1.s4" in provider.calls[1]["messages"][-1]["content"]


def test_refine_rejects_a_span_listed_in_two_sections(document: SourceDocument) -> None:
    base = skeleton(document)
    bad = payload(base)
    bad["sections"][2]["span_ids"].append("p1.s5")
    good = payload(base)
    good["sections"][3]["title"] = "Talk about names"
    provider = MockProvider(responses=[json.dumps(bad), json.dumps(good)])

    result = refine_outline(base, [document], CURRICULUM, provider)

    assert len(provider.calls) == 2
    feedback = provider.calls[1]["messages"][-1]["content"]
    assert "more than one section" in feedback
    assert "p1.s5" in feedback
    assert result.sections[3].title == "Talk about names"
    assert refs(result, 2) == refs(base, 2)


def test_refine_accepts_a_span_moved_to_the_neighbour(document: SourceDocument) -> None:
    base = skeleton(document)
    reply = payload(base)
    moved = reply["sections"][1]["span_ids"].pop()
    reply["sections"][2]["span_ids"].insert(0, moved)
    provider = MockProvider(responses=[json.dumps(reply)])
    result = refine_outline(base, [document], CURRICULUM, provider)
    assert len(provider.calls) == 1
    assert refs(result, 2)[0] == moved
    assert moved not in refs(result, 1)


def test_refine_prompt_tells_the_model_to_list_every_span(document: SourceDocument) -> None:
    provider = MockProvider(responses=[json.dumps(payload(skeleton(document)))])
    refine_outline(skeleton(document), [document], CURRICULUM, provider)
    assert "ALL of the spans" in provider.calls[0]["messages"][0]["content"]


def test_refine_prompt_names_the_title_and_outcome_rules(document: SourceDocument) -> None:
    provider = MockProvider(responses=[json.dumps(payload(skeleton(document)))])
    refine_outline(skeleton(document), [document], CURRICULUM, provider)
    prompt = provider.calls[0]["messages"][0]["content"]
    assert "must describe what the section's blocks will contain" in prompt
    assert '"Say goodbye"' in prompt
    assert "no span teaches" in prompt


def test_refine_keeps_the_spans_of_a_review_presentation(document: SourceDocument) -> None:
    base = skeleton(document, period=2)
    assert base.sections[1].review is True
    # a span of the next section moved into the review ...
    into = payload(base)
    moved = into["sections"][2]["span_ids"].pop(0)
    into["sections"][1]["span_ids"].append(moved)
    # ... and one of the review moved out of it
    out = payload(base)
    gone = out["sections"][1]["span_ids"].pop()
    out["sections"][2]["span_ids"].insert(0, gone)
    for bad, span in ((into, moved), (out, gone)):
        provider = MockProvider(responses=[json.dumps(bad), json.dumps(bad)])
        result = refine_outline(base, [document], CURRICULUM, provider)
        assert result == base
        feedback = provider.calls[1]["messages"][-1]["content"]
        assert "a review section keeps exactly its spans" in feedback
        assert span in provider.calls[0]["messages"][0]["content"]
    good = payload(base)
    good["sections"][1]["title"] = "Hello again"
    result = refine_outline(
        base, [document], CURRICULUM, MockProvider(responses=[json.dumps(good)])
    )
    assert result.sections[1].title == "Hello again"
    assert result.sections[1].review is True
    assert refs(result, 1) == refs(base, 1)


def test_refine_prompt_names_the_review_rule(document: SourceDocument) -> None:
    base = skeleton(document, period=2)
    provider = MockProvider(responses=[json.dumps(payload(base))])
    refine_outline(base, [document], CURRICULUM, provider)
    prompt = provider.calls[0]["messages"][0]["content"]
    assert '"review": true' in prompt
    assert "move none into it and none out of it" in prompt


def two_lesson_document() -> SourceDocument:
    spans = [
        ("heading", "Lesson 1"),
        ("list_item", "1. Look, listen and repeat."),
        ("paragraph", "Hello, I'm Ha."),
        ("list_item", "2. Listen, point and say."),
        ("paragraph", "Hi, Ha."),
        ("list_item", "3. Let's talk."),
        ("paragraph", "Goodbye."),
        ("heading", "Lesson 2"),
        ("list_item", "1. Look, listen and repeat."),
        ("paragraph", "Good morning, Tien."),
        ("list_item", "2. Listen, point and say."),
        ("paragraph", "Good afternoon."),
        ("list_item", "3. Let's talk."),
        ("paragraph", "Bye, Ha."),
    ]
    return make_document(spans)


def test_lesson_number_selects_the_matching_run_of_activities() -> None:
    document = two_lesson_document()
    first = build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    second = build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=2, period=1, grade=3
    )
    assert refs(first, 1) == ["p1.s1", "p1.s2", "p1.s3"]
    assert refs(second, 1) == ["p1.s9", "p1.s10"]
    assert span_text(document, second.sections[1].span_refs[-1]) == "Good morning, Tien."


def test_missing_lesson_run_falls_back_to_the_first_with_a_warning(
    document: SourceDocument, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        result = skeleton(document, lesson_no=3)
    assert refs(result, 1) == refs(skeleton(document), 1)
    assert "fewer than 3 lessons" in caplog.text


def test_titles_carry_the_textbook_unit_number(document: SourceDocument) -> None:
    assert skeleton(document).title == "Unit 1 Hello — Lesson 1, Period 1"


def test_multi_source_fallback_ranks_jointly_and_labels_spans_by_document() -> None:
    first = make_document(
        [("paragraph", "Pupils look at the picture, listen and repeat the greetings.")], "d_aaaa"
    )
    second = make_document(
        [
            ("paragraph", "Listen and repeat the song words."),
            ("paragraph", "Hello, I'm Ha."),
        ],
        "d_bbbb",
    )
    base = build_skeleton(
        [first, second], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3
    )
    assert all(len(section.span_refs) <= 6 for section in base.sections)
    assert {(r.doc_id, r.span_id) for r in base.sections[1].span_refs} >= {
        ("d_aaaa", "p1.s1"),
        ("d_bbbb", "p1.s1"),
    }
    reply = payload(base)
    for index, section in enumerate(reply["sections"]):
        section["span_ids"] = [
            f"{ref.doc_id}:{ref.span_id}" for ref in base.sections[index].span_refs
        ]
    reply["sections"][1]["title"] = "Songs and greetings"
    provider = MockProvider(responses=[json.dumps(reply)])

    result = refine_outline(base, [first, second], CURRICULUM, provider)

    prompt = provider.calls[0]["messages"][0]["content"]
    assert "[d_aaaa:p1.s1] Pupils look" in prompt
    assert "[d_bbbb:p1.s1] Listen and repeat" in prompt
    assert result.sections[1].title == "Songs and greetings"
    assert result.sections[1].span_refs == base.sections[1].span_refs
