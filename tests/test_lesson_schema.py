from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from classdeck.lesson import (
    SCHEMA_VERSION,
    ActivityBlock,
    BlockAdapter,
    BlockKind,
    CheckResult,
    CheckStatus,
    Citation,
    Lesson,
    PatchOp,
    Provenance,
    Revision,
    SourceDocument,
    VocabularyBlock,
    doc_id_from_sha256,
    new_id,
    span_id,
)

DOC_ID = "d_8f2a1c"
SHA = "8f2a1c" + "0" * 58
IPA_HELLO = "/həˈləʊ/"  # noqa: RUF001
IPA_HI = "/haɪ/"  # noqa: RUF001


def make_source_document() -> SourceDocument:
    """The page from docs/lesson-model.md, with every span the example lesson cites."""
    return SourceDocument.model_validate(
        {
            "doc_id": DOC_ID,
            "filename": "english3-unit1.pdf",
            "sha256": SHA,
            "media_type": "application/pdf",
            "pages": [
                {
                    "number": 6,
                    "spans": [
                        {
                            "span_id": "p6.s1",
                            "kind": "heading",
                            "text": "Unit 1  Hello",
                            "order": 1,
                            "bbox": [60.0, 40.0, 300.0, 70.0],
                        },
                        {
                            "span_id": "p6.s2",
                            "kind": "list_item",
                            "text": "1. Look, listen and repeat.",
                            "order": 2,
                        },
                        {
                            "span_id": "p6.s3",
                            "kind": "paragraph",
                            "text": "Hello, I'm Ha.  Hi, Ha. I'm Tien.",
                            "order": 3,
                        },
                        {
                            "span_id": "p6.s4",
                            "kind": "image",
                            "text": "",
                            "order": 4,
                            "bbox": [60.0, 120.0, 520.0, 260.0],
                            "image_path": f"sources/{DOC_ID}/p6_img1.png",
                        },
                        {
                            "span_id": "p6.s6",
                            "kind": "paragraph",
                            "text": "Hello, I'm ___.  Hi, ___. I'm ___.",
                            "order": 6,
                        },
                    ],
                }
            ],
        }
    )


def make_lesson() -> Lesson:
    """Objective b_01, vocabulary b_02, pattern b_03, activity b_07 and summary b_08."""
    cite_text = {"doc_id": DOC_ID, "page": 6, "span_id": "p6.s3", "quote": "Hello, I'm Ha."}
    data = {
        "id": "l_3b9e",
        "title": "Unit 1 Hello - Lesson 1, Period 1",
        "grade": 3,
        "curriculum": {
            "version": "gdpt-2018",
            "unit_id": "ta3.u1",
            "lesson_no": 1,
            "period": 1,
            "outcome_ids": ["ta3.u1.o1"],
        },
        "sources": [{"doc_id": DOC_ID, "filename": "english3-unit1.pdf", "sha256": SHA}],
        "sections": [
            {
                "id": "s_presentation",
                "phase": "presentation",
                "title": "Presentation: Look, listen and repeat",
                "minutes": 10,
                "block_ids": ["b_01", "b_02", "b_03"],
            },
            {
                "id": "s_production",
                "phase": "production",
                "title": "Production: Name chain",
                "minutes": 6,
                "block_ids": ["b_07"],
            },
            {
                "id": "s_wrap_up",
                "phase": "wrap_up",
                "title": "Wrap-up",
                "minutes": 2,
                "block_ids": ["b_08"],
            },
        ],
        "blocks": {
            "b_01": {
                "id": "b_01",
                "kind": "objective",
                "content": {
                    "text": "Pupils can greet each other and say their names.",
                    "outcome_ids": ["ta3.u1.o1"],
                    "skills": ["speaking", "listening"],
                },
                "provenance": "derived",
            },
            "b_02": {
                "id": "b_02",
                "kind": "vocabulary",
                "content": {
                    "items": [
                        {
                            "word": "hello",
                            "ipa": IPA_HELLO,
                            "pos": "interjection",
                            "meaning_vi": "xin chào",
                            "example": "Hello, I'm Ha.",
                            "image": {"doc_id": DOC_ID, "page": 6, "span_id": "p6.s4"},
                        },
                        {
                            "word": "hi",
                            "ipa": IPA_HI,
                            "pos": "interjection",
                            "meaning_vi": "chào",
                            "example": "Hi, Ha.",
                        },
                    ]
                },
                "sources": [{**cite_text, "confidence": 0.97}],
                "provenance": "extracted",
                "checks": [
                    {
                        "name": "grounding.citation_supported",
                        "status": "pass",
                        "detail": "lexical overlap 0.88",
                    },
                    {"name": "english.ipa_present", "status": "pass"},
                ],
            },
            "b_03": {
                "id": "b_03",
                "kind": "sentence_pattern",
                "content": {
                    "pattern": "Hello, I'm ___.",
                    "function": "Introduce yourself",
                    "examples": ["Hello, I'm Ha."],
                },
                "sources": [{"doc_id": DOC_ID, "page": 6, "span_id": "p6.s6"}],
                "provenance": "extracted",
            },
            "b_07": {
                "id": "b_07",
                "kind": "activity",
                "content": {
                    "type": "game",
                    "title": "Name chain",
                    "skills": ["speaking", "listening"],
                    "interaction": "whole_class",
                    "minutes": 5,
                    "instructions": "Pass the ball. Say 'Hello, I'm ...' and your name.",
                    "teacher_steps": ["Model the chain with two pupils.", "Start the ball."],
                    "student_steps": ["Catch the ball, say 'Hello, I'm (name).'", "Pass it on."],
                    "materials": ["a soft ball"],
                    "competency_tags": ["communication", "cooperation"],
                },
                "provenance": "generated",
                "depends_on": ["b_03"],
                "checks": [{"name": "english.pattern_used", "status": "pass"}],
                "teacher_note": "If the class is large, run two chains in parallel.",
                "slide_hint": {"layout": "game"},
            },
            "b_08": {
                "id": "b_08",
                "kind": "summary",
                "content": {"bullets": ["hello, hi", "Hello, I'm ___."]},
                "provenance": "derived",
                "depends_on": ["b_02", "b_07"],
            },
        },
    }
    return Lesson.model_validate(data)


def lesson_data() -> dict[str, Any]:
    return make_lesson().model_dump(mode="json")


# --- ids ---------------------------------------------------------------------------------------


def test_ids() -> None:
    first, second = new_id("b"), new_id("b")
    assert first.startswith("b_")
    assert len(first) == len("b_") + 6
    assert first != second
    assert doc_id_from_sha256("8f2a1c9e" + "0" * 56) == "d_8f2a1c9e"
    assert span_id(6, 3) == "p6.s3"


# --- round trip --------------------------------------------------------------------------------


def test_json_round_trip_is_equal() -> None:
    lesson = make_lesson()
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson
    assert Lesson.model_validate_json(lesson.model_dump_json()) == lesson


def test_round_trip_keeps_block_types_and_revisions() -> None:
    lesson = make_lesson()
    lesson.revisions.append(
        Revision(
            id="r_0001",
            actor="teacher",
            target_block_ids=["b_02"],
            patch=[PatchOp(op="remove", path="/blocks/b_02/content/items/1")],
            inverse=[PatchOp(op="add", path="/blocks/b_02/content/items/1", value={"word": "hi"})],
        )
    )
    restored = Lesson.model_validate(lesson.model_dump(mode="json"))
    assert restored == lesson
    assert isinstance(restored.blocks["b_02"], VocabularyBlock)
    assert isinstance(restored.blocks["b_07"], ActivityBlock)


def test_defaults_and_datetimes_are_utc() -> None:
    lesson = make_lesson()
    assert lesson.schema_version == SCHEMA_VERSION == 1
    assert lesson.subject == "english"
    assert lesson.language == "en"
    for stamp in (lesson.created_at, lesson.updated_at, CheckResult(name="x", status="pass").at):
        assert stamp.utcoffset() == timedelta(0)
    block = lesson.blocks["b_03"]
    assert block.provenance is Provenance.EXTRACTED
    assert lesson.blocks["b_08"].checks == []


def test_datetimes_are_normalised_to_utc_and_naive_is_rejected() -> None:
    data = lesson_data()
    data["created_at"] = datetime(2026, 10, 27, 17, 0, tzinfo=timezone(timedelta(hours=7)))
    lesson = Lesson.model_validate(data)
    assert lesson.created_at == datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
    assert lesson.created_at.tzinfo is UTC

    data["created_at"] = "2026-10-27T10:00:00"
    with pytest.raises(ValidationError):
        Lesson.model_validate(data)


def test_touch_advances_updated_at() -> None:
    lesson = make_lesson()
    before = lesson.updated_at
    lesson.touch()
    assert lesson.updated_at > before
    assert lesson.created_at <= before


def test_check_status_member_is_pass() -> None:
    assert CheckStatus.PASS == "pass"
    assert CheckStatus("stale") is CheckStatus.STALE


# --- discriminated union -----------------------------------------------------------------------


def test_union_picks_the_model_from_kind() -> None:
    block = BlockAdapter.validate_python(
        {"id": "b_9", "kind": "teacher_note", "content": {"text": "Smile."}}
    )
    assert block.kind == "teacher_note"
    assert block.provenance is Provenance.GENERATED


def test_union_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError, match="poem"):
        BlockAdapter.validate_python({"id": "b_9", "kind": "poem", "content": {"text": "x"}})


def test_union_rejects_missing_kind() -> None:
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python({"id": "b_9", "content": {"text": "x"}})


def test_union_rejects_content_of_the_wrong_shape() -> None:
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_9", "kind": "vocabulary", "content": {"bullets": ["a"]}}
        )
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_9", "kind": "summary", "content": {"items": [{"word": "a"}]}}
        )


def test_union_covers_every_block_kind() -> None:
    shapes: dict[str, dict[str, Any]] = {
        "objective": {"text": "t"},
        "vocabulary": {"items": [{"word": "a"}]},
        "sentence_pattern": {"pattern": "p"},
        "dialogue": {"lines": [{"speaker": "A", "text": "Hi."}]},
        "phonics": {"sound": "/s/", "letters": "s"},
        "activity": {"type": "chant", "title": "t", "interaction": "pairs", "minutes": 3},
        "teacher_note": {"text": "t"},
        "summary": {"bullets": ["b"]},
        "image_ref": {"caption": "c", "alt": "a"},
    }
    assert set(shapes) == {kind.value for kind in BlockKind}
    for kind, content in shapes.items():
        block = BlockAdapter.validate_python({"id": "b_1", "kind": kind, "content": content})
        assert block.kind == kind


def test_models_are_strict_about_fields_and_strip_whitespace() -> None:
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_9", "kind": "summary", "content": {"bullets": []}, "mood": "happy"}
        )
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_9", "kind": "summary", "content": {"bullets": [], "extra": 1}}
        )
    block = BlockAdapter.validate_python(
        {"id": " b_9 ", "kind": "teacher_note", "content": {"text": "  Smile.  "}}
    )
    assert block.id == "b_9"
    assert block.content.text == "Smile."


def test_activity_allows_empty_skills_but_needs_positive_minutes() -> None:
    base: dict[str, Any] = {"type": "game", "title": "t", "interaction": "pairs", "minutes": 3}
    block = BlockAdapter.validate_python({"id": "b_1", "kind": "activity", "content": base})
    assert block.content.skills == []
    assert block.content.teacher_steps == []
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_1", "kind": "activity", "content": {**base, "minutes": 0}}
        )
    with pytest.raises(ValidationError):
        BlockAdapter.validate_python(
            {"id": "b_1", "kind": "activity", "content": {**base, "type": "quiz"}}
        )


def test_citation_confidence_is_bounded() -> None:
    assert Citation(doc_id="d", page=1, span_id="p1.s1", confidence=1).confidence == 1
    for bad in (-0.1, 1.1):
        with pytest.raises(ValidationError):
            Citation(doc_id="d", page=1, span_id="p1.s1", confidence=bad)


def test_lesson_grade_and_subject_are_constrained() -> None:
    for bad in (2, 6):
        data = lesson_data()
        data["grade"] = bad
        with pytest.raises(ValidationError):
            Lesson.model_validate(data)
    data = lesson_data()
    data["subject"] = "maths"
    with pytest.raises(ValidationError):
        Lesson.model_validate(data)


# --- invariant 1 -------------------------------------------------------------------------------


def _missing_block_in_section(data: dict[str, Any]) -> None:
    data["sections"][0]["block_ids"].append("b_99")


def _missing_dependency(data: dict[str, Any]) -> None:
    data["blocks"]["b_07"]["depends_on"].append("b_ghost")


def _key_differs_from_id(data: dict[str, Any]) -> None:
    data["blocks"]["b_key"] = data["blocks"].pop("b_03")
    data["sections"][0]["block_ids"][2] = "b_key"
    data["blocks"]["b_07"]["depends_on"] = ["b_key"]


def _block_in_two_sections(data: dict[str, Any]) -> None:
    data["sections"][2]["block_ids"].append("b_02")


def _block_twice_in_one_section(data: dict[str, Any]) -> None:
    data["sections"][0]["block_ids"].append("b_02")


def _duplicate_section_id(data: dict[str, Any]) -> None:
    data["sections"][1]["id"] = "s_presentation"


@pytest.mark.parametrize(
    ("mutate", "offender"),
    [
        (_missing_block_in_section, "b_99"),
        (_missing_dependency, "b_ghost"),
        (_key_differs_from_id, "b_key"),
        (_block_in_two_sections, "b_02"),
        (_block_twice_in_one_section, "b_02"),
        (_duplicate_section_id, "s_presentation"),
    ],
)
def test_reference_violations_are_rejected(mutate: Any, offender: str) -> None:
    data = deepcopy(lesson_data())
    mutate(data)
    with pytest.raises(ValidationError, match=offender):
        Lesson.model_validate(data)


def test_blocks_outside_any_section_are_allowed() -> None:
    data = lesson_data()
    data["sections"][2]["block_ids"] = []
    lesson = Lesson.model_validate(data)
    assert [block.id for block in lesson.ordered_blocks()] == ["b_01", "b_02", "b_03", "b_07"]
    assert lesson.section_of("b_08") is None


# --- helpers -----------------------------------------------------------------------------------


def test_ordered_blocks_follow_section_order() -> None:
    data = lesson_data()
    data["sections"][0]["block_ids"] = ["b_03", "b_01", "b_02"]
    lesson = Lesson.model_validate(data)
    assert [block.id for block in lesson.ordered_blocks()] == [
        "b_03",
        "b_01",
        "b_02",
        "b_07",
        "b_08",
    ]


def test_section_of() -> None:
    lesson = make_lesson()
    section = lesson.section_of("b_07")
    assert section is not None
    assert section.id == "s_production"
    assert lesson.section_of("b_unknown") is None


def test_content_blocks_exclude_objective_and_summary() -> None:
    lesson = make_lesson()
    assert [block.id for block in lesson.content_blocks()] == ["b_02", "b_03", "b_07"]
    lesson.blocks["b_09"] = BlockAdapter.validate_python(
        {"id": "b_09", "kind": "teacher_note", "content": {"text": "Smile."}}
    )
    lesson.sections[1].block_ids.append("b_09")
    assert "b_09" not in [block.id for block in lesson.content_blocks()]
    assert "b_09" in [block.id for block in lesson.ordered_blocks()]


def test_dependents_of_direct_versus_transitive() -> None:
    lesson = make_lesson()
    assert lesson.dependents_of("b_03", transitive=False) == ["b_07"]
    assert lesson.dependents_of("b_03") == ["b_07", "b_08"]
    assert lesson.dependents_of("b_02") == ["b_08"]
    assert lesson.dependents_of("b_08") == []
    assert lesson.dependents_of("b_missing") == []


def test_dependents_of_has_no_duplicates_and_is_in_lesson_order() -> None:
    lesson = make_lesson()
    lesson.blocks["b_08"].depends_on = ["b_07", "b_03", "b_02"]
    assert lesson.dependents_of("b_03") == ["b_07", "b_08"]
    assert lesson.dependents_of("b_03", transitive=False) == ["b_07", "b_08"]


def test_dependents_of_survives_cycles_and_never_returns_the_input() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].depends_on = ["b_08"]
    assert lesson.dependents_of("b_03") == ["b_07", "b_08"]
    assert lesson.dependents_of("b_08") == ["b_03", "b_07"]


# --- patch operations and revisions ------------------------------------------------------------


def test_patch_op_serialises_from_as_from() -> None:
    op = PatchOp(op="move", path="/sections/0/block_ids/0", from_="/sections/1/block_ids/0")
    assert op.model_dump(mode="json") == {
        "op": "move",
        "path": "/sections/0/block_ids/0",
        "from": "/sections/1/block_ids/0",
    }
    assert op.model_dump(mode="json", by_alias=True)["from"] == "/sections/1/block_ids/0"
    assert '"from"' in op.model_dump_json()
    assert "from_" not in op.model_dump_json()


def test_patch_op_accepts_from_by_alias_and_by_name() -> None:
    by_alias = PatchOp.model_validate({"op": "copy", "path": "/a", "from": "/b"})
    by_name = PatchOp(op="copy", path="/a", from_="/b")
    assert by_alias == by_name
    assert by_alias.from_ == "/b"


def test_patch_op_omits_unused_members_but_keeps_explicit_null_values() -> None:
    assert PatchOp(op="remove", path="/a").model_dump(mode="json") == {"op": "remove", "path": "/a"}
    assert PatchOp(op="test", path="/a", value=None).model_dump(mode="json") == {
        "op": "test",
        "path": "/a",
        "value": None,
    }
    with pytest.raises(ValidationError):
        PatchOp(op="swap", path="/a")


def test_revision_round_trips_through_json() -> None:
    revision = Revision(
        id="r_0007",
        parent="r_0006",
        actor="model",
        instruction="Reduce new words to four",
        target_block_ids=["b_02"],
        patch=[PatchOp(op="replace", path="/blocks/b_02/content/items/0/word", value="hello")],
        inverse=[PatchOp(op="replace", path="/blocks/b_02/content/items/0/word", value="hi")],
        flagged_block_ids=["b_08"],
    )
    dumped = revision.model_dump(mode="json")
    assert dumped["patch"][0]["op"] == "replace"
    assert Revision.model_validate(dumped) == revision
    assert revision.at.utcoffset() == timedelta(0)
    with pytest.raises(ValidationError):
        Revision.model_validate({**dumped, "actor": "robot"})
