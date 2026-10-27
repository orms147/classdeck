from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.english.rules import prompt_rules
from classdeck.handoff import (
    WRAP_UP_REPLY,
    ImportResult,
    build_draft_pack,
    import_draft,
)
from classdeck.ingest import ingest
from classdeck.lesson import (
    ActivityBlock,
    DialogueBlock,
    Lesson,
    LessonStore,
    Phase,
    Provenance,
    SourceDocument,
)
from classdeck.planning import Outline, outline

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "eval" / "fixtures" / "public" / "mini-unit-hello.pdf"
PRESENTATION = 1  # outline index of the section with the dialogue spans
PRACTICE = 2
PRODUCTION = 3


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)


@pytest.fixture(scope="module")
def document(tmp_path_factory: pytest.TempPathFactory) -> SourceDocument:
    return ingest(FIXTURE, image_dir=tmp_path_factory.mktemp("images"))


@pytest.fixture(scope="module")
def plan(document: SourceDocument, curriculum: Curriculum) -> Outline:
    return outline(
        [document], curriculum, unit_id="ta3.u1", lesson_no=1, period=1, grade=3, provider=None
    )


def activity(title: str, **extra: Any) -> dict[str, Any]:
    content = {
        "type": "game",
        "title": title,
        "skills": ["speaking"],
        "interaction": "pairs",
        "minutes": 4,
        "instructions": "Say hello to your partner.",
        "teacher_steps": ["Model the game.", "Let pairs play."],
        "student_steps": ["Listen.", "Play with your partner."],
    }
    return {"kind": "activity", "content": content, **extra}


def valid_reply(plan: Outline) -> list[dict[str, Any]]:
    """A hand-written reply: a dialogue and the activity that drills it, citing the spans."""
    dialogue = {
        "kind": "dialogue",
        "content": {
            "context": "Ha meets Tien.",
            "lines": [
                {"speaker": "Ha", "text": "Hello, I'm Ha."},
                {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
            ],
        },
        "span_ids": ["p1.s4", "p1.s5"],
    }
    reply: list[dict[str, Any]] = []
    for index, section in enumerate(plan.sections):
        if section.phase is Phase.WRAP_UP:
            reply.append({"blocks": []})
        elif index == PRESENTATION:
            reply.append(
                {
                    "blocks": [
                        dialogue,
                        activity("Say hello", span_ids=["p1.s2"], depends_on=[0]),
                    ]
                }
            )
        else:
            reply.append({"blocks": [activity("Hello game")]})
    return reply


# --- the draft pack ----------------------------------------------------------------------------


def test_draft_pack_carries_the_outline_spans_rules_and_schema(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    pack = build_draft_pack(plan, [document], curriculum)
    for section in plan.sections:
        assert section.title in pack
        for ref in section.span_refs:
            assert f"[{ref.span_id}] " in pack
    assert "[p1.s4] Hello, I'm Ha." in pack
    assert "[p1.s3] (image)" in pack
    assert prompt_rules(3, curriculum) in pack
    assert '"DraftSection"' in pack or '"DraftBlock"' in pack
    assert '"span_ids"' in pack and '"depends_on"' in pack
    assert "Hello, I'm ___." in pack  # the unit's pattern and the filled template
    assert "/h/ (h): hello, hi" in pack
    assert "Vocabulary: hello, hi, goodbye" in pack
    assert "Look, listen and repeat" in pack  # the template of the section's type
    assert f"ONE JSON array of exactly {len(plan.sections)} objects" in pack
    assert WRAP_UP_REPLY in pack
    assert "- dialogue: context, lines" in pack
    assert pack == build_draft_pack(plan, [document], curriculum)


def test_draft_pack_headings_follow_the_outline_order(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    pack = build_draft_pack(plan, [document], curriculum)
    positions = [pack.index(f"### Section {i + 1} · ") for i in range(len(plan.sections))]
    assert positions == sorted(positions)
    assert pack.index("## 4. Outline") < positions[0] < pack.index("## 5. Reply format")


# --- importing a reply -------------------------------------------------------------------------


def test_a_valid_reply_becomes_model_blocks_with_citations_and_dependencies(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    reply = json.dumps(valid_reply(plan))
    result = import_draft(plan, [document], curriculum, reply)
    assert isinstance(result, ImportResult)
    reports = result.section_reports
    assert [r.index for r in reports] == list(range(len(plan.sections)))
    assert [r.title for r in reports] == [s.title for s in plan.sections]
    assert [r.status for r in reports] == ["model", "model", "model", "model", "template"]
    assert all(r.problems == [] for r in reports)

    lesson = result.lesson
    dialogue = next(b for b in lesson.blocks.values() if isinstance(b, DialogueBlock))
    assert [c.span_id for c in dialogue.sources] == ["p1.s4", "p1.s5"]
    assert dialogue.content.lines[0].text == "Hello, I'm Ha."
    drill = next(
        b
        for b in lesson.blocks.values()
        if isinstance(b, ActivityBlock) and b.content.title == "Say hello"
    )
    assert drill.depends_on == [dialogue.id]
    assert [c.span_id for c in drill.sources] == ["p1.s2"]
    # the engine added the objectives and the wrap-up
    assert lesson.sections[0].block_ids[0] in lesson.blocks
    assert lesson.blocks[lesson.sections[0].block_ids[0]].kind == "objective"
    assert [lesson.blocks[i].kind for i in lesson.sections[-1].block_ids] == [
        "summary",
        "activity",
        "teacher_note",
    ]
    # grounding and the level checks ran
    assert dialogue.provenance is Provenance.EXTRACTED
    assert any(c.name.startswith("grounding.") for c in dialogue.checks)
    assert {"english.vocab_level", "english.steps_present"} <= {c.name for c in drill.checks}
    assert "english.timing" in {c.name for c in lesson.checks}
    assert [s.phase for s in lesson.sections] == [s.phase for s in plan.sections]


def test_an_unknown_span_id_falls_back_to_the_template_for_that_section(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    entries = valid_reply(plan)
    entries[PRACTICE] = {"blocks": [activity("Bad cite", span_ids=["p9.s99"])]}
    result = import_draft(plan, [document], curriculum, json.dumps(entries))
    statuses = [r.status for r in result.section_reports]
    assert statuses == ["model", "model", "template", "model", "template"]
    problems = result.section_reports[PRACTICE].problems
    assert problems and "p9.s99" in problems[0]
    assert result.section_reports[PRESENTATION].problems == []
    titles = [
        b.content.title for b in result.lesson.blocks.values() if isinstance(b, ActivityBlock)
    ]
    assert "Bad cite" not in titles
    assert "Say hello" in titles


def test_other_invalid_entries_are_reported_per_section(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    entries = valid_reply(plan)
    entries[0] = {"blocks": [activity("Self", depends_on=[0])]}  # depends on itself
    entries[PRESENTATION] = {"blocks": [{"kind": "activity", "content": {"title": "x"}}]}
    entries[PRODUCTION] = {"blocks": []}
    result = import_draft(plan, [document], curriculum, json.dumps(entries))
    reports = result.section_reports
    assert [r.status for r in reports] == ["template", "template", "model", "template", "template"]
    assert "depends_on" in reports[0].problems[0]
    assert "not valid activity" in reports[PRESENTATION].problems[0]
    assert reports[PRODUCTION].problems == ["the entry has no blocks"]


def test_a_non_json_reply_falls_back_everywhere(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    result = import_draft(plan, [document], curriculum, "Sorry, I cannot help with that.")
    reports = result.section_reports
    assert all(r.status == "template" for r in reports)
    assert all("not valid JSON" in r.problems[0] for r in reports[:-1])
    assert reports[-1].problems == []  # the wrap-up is the engine's anyway
    assert len(result.lesson.sections) == len(plan.sections)
    assert result.lesson.blocks


def test_a_short_array_and_surplus_entries(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    entries = valid_reply(plan)
    short = import_draft(plan, [document], curriculum, json.dumps(entries[:2]))
    assert [r.status for r in short.section_reports] == [
        "model",
        "model",
        "template",
        "template",
        "template",
    ]
    assert short.section_reports[2].problems == ["the reply has no entry for this section"]
    long = import_draft(plan, [document], curriculum, json.dumps([*entries, {"blocks": []}]))
    assert "1 extra entries" in long.section_reports[-1].problems[0]


def test_a_fenced_reply_with_prose_is_read(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    text = "Here you go:\n```json\n" + json.dumps(valid_reply(plan)) + "\n```\nHope it helps!"
    result = import_draft(plan, [document], curriculum, text)
    assert [r.status for r in result.section_reports][:4] == ["model"] * 4


def test_the_imported_lesson_validates_and_can_be_saved(
    plan: Outline, document: SourceDocument, curriculum: Curriculum, tmp_path: Path
) -> None:
    result = import_draft(plan, [document], curriculum, json.dumps(valid_reply(plan)))
    lesson = result.lesson
    assert Lesson.model_validate_json(lesson.model_dump_json()) == lesson
    assert lesson.curriculum.unit_id == "ta3.u1"
    assert [r.doc_id for r in lesson.sources] == [document.doc_id]
    store = LessonStore(tmp_path)
    path = store.save(lesson)
    assert path.is_file()
    assert store.load(lesson.id).id == lesson.id


def test_a_period_two_review_is_built_by_the_engine_not_the_reply(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    second = outline(
        [document], curriculum, unit_id="ta3.u1", lesson_no=1, period=2, grade=3, provider=None
    )
    assert second.sections[0].review is True
    chatty = {
        "blocks": [
            {
                "kind": "vocabulary",
                "content": {"items": [{"word": "hello", "meaning_vi": "xin chao"}]},
                "span_ids": ["p1.s4"],
            },
            activity("Hello chain", span_ids=["p1.s4"], depends_on=[0]),
        ]
    }
    entries = [chatty] + [
        {"blocks": [] if section.phase is Phase.WRAP_UP else [activity("Hello game")]}
        for section in second.sections[1:]
    ]
    result = import_draft(second, [document], curriculum, json.dumps(entries))
    lesson = result.lesson
    warm_up = [lesson.blocks[block_id] for block_id in lesson.sections[0].block_ids]
    assert [b.kind for b in warm_up if b.kind != "objective"] == [
        "vocabulary",
        "sentence_pattern",
        "activity",
    ]
    vocabulary = next(b for b in warm_up if b.kind == "vocabulary")
    assert [item.meaning_vi for item in vocabulary.content.items] == ["", ""]  # built by the engine
    game = next(b for b in warm_up if isinstance(b, ActivityBlock))
    assert game.content.title == "Hello chain"
    assert game.sources == []
    assert result.section_reports[0].status == "model"
    only_game = [{"blocks": [chatty["blocks"][0]]}, *entries[1:]]
    template = import_draft(second, [document], curriculum, json.dumps(only_game))
    assert template.section_reports[0].status == "template"


def test_an_outline_without_sections_is_rejected(
    plan: Outline, document: SourceDocument, curriculum: Curriculum
) -> None:
    empty = plan.model_copy(update={"sections": []})
    with pytest.raises(ValueError, match="no sections"):
        import_draft(empty, [document], curriculum, "[]")
