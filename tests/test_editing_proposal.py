from __future__ import annotations

import json
from typing import Any

import pytest

from classdeck.editing import (
    EditRejected,
    EditRequest,
    compute_affected,
    direct_edit,
    propose_edit,
)
from classdeck.english.validators import run_all
from classdeck.lesson import Block, BlockAdapter, CheckStatus, Lesson, Provenance
from classdeck.llm import MockProvider
from tests.test_editing_context import (
    CURRICULUM,
    FOUR,
    GREETINGS_SPAN,
    REQUEST,
    SOURCES,
    cite,
    make_edit_lesson,
)

GROUNDING = "grounding.citation_supported"
LESSON_LEVEL = {"english.timing", "english.skill_coverage", "curriculum.outcome_covered"}


def draft_reply(
    replacements: dict[str, Any] | None = None,
    added_blocks: list[dict[str, Any]] | None = None,
    removed_block_ids: list[str] | None = None,
    rationale: str = "Done as asked.",
) -> str:
    """A scripted model reply in the ``EditProposalDraft`` shape."""
    return json.dumps(
        {
            "replacements": replacements or {},
            "added_blocks": added_blocks or [],
            "removed_block_ids": removed_block_ids or [],
            "rationale": rationale,
        },
        ensure_ascii=False,
    )


def vocabulary_json(lesson: Lesson, words: list[str]) -> dict[str, Any]:
    """The lesson's vocabulary block as the model would return it, reduced to ``words``."""
    block = lesson.blocks["b_02"].model_dump(mode="json")
    block["content"]["items"] = [
        item for item in block["content"]["items"] if item["word"] in words
    ]
    return block


def four_words_reply(lesson: Lesson, rationale: str = "Kept the four greeting words.") -> str:
    return draft_reply(replacements={"b_02": vocabulary_json(lesson, FOUR)}, rationale=rationale)


def words_of(block: Block) -> list[str]:
    return [item.word for item in block.content.items]  # type: ignore[union-attr]


@pytest.fixture
def lesson() -> Lesson:
    return make_edit_lesson()


# --- a valid proposal --------------------------------------------------------------------------


def test_reduce_words_replaces_the_vocabulary_and_flags_dependents(lesson: Lesson) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )

    assert len(provider.calls) == 1
    assert proposal.id.startswith("ep_")
    assert proposal.actor == "model"
    assert proposal.instruction == "Reduce new words to four"
    assert proposal.target_block_ids == ["b_02"]
    assert proposal.rationale == "Kept the four greeting words."
    assert words_of(proposal.replacements["b_02"]) == FOUR
    assert words_of(proposal.after.blocks["b_02"]) == FOUR
    assert words_of(lesson.blocks["b_02"]) == [*FOUR, "nice", "meet"]
    assert proposal.added_blocks == []
    assert proposal.removed_block_ids == []
    assert [(a.block_id, a.suggested_action) for a in proposal.affected] == [
        ("b_10", "regenerate"),
        ("b_07", "regenerate"),
        ("b_11", "review"),
        ("b_08", "regenerate"),
    ]
    assert proposal.affected[0].reason == "depends_on b_02 — vocabulary"
    for affected in proposal.affected:  # flagged, never rewritten
        assert proposal.after.blocks[affected.block_id].content == (
            lesson.blocks[affected.block_id].content
        )


def test_preview_has_grounding_and_english_checks_for_the_replaced_block(lesson: Lesson) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    on_block = {c.name: c for c in proposal.checks_preview if c.block_id == "b_02"}
    assert {"english.vocab_level", "english.ipa_present", GROUNDING} <= set(on_block)
    assert all(check.status is CheckStatus.PASS for check in on_block.values())
    assert (on_block[GROUNDING].detail or "").startswith("1/1 supported; lexical")
    assert proposal.after.blocks["b_02"].provenance is Provenance.EXTRACTED
    lesson_level = {c.name: c.status for c in proposal.checks_preview if c.block_id is None}
    assert set(lesson_level) == LESSON_LEVEL  # new compared with the unchecked input lesson
    assert set(lesson_level.values()) == {CheckStatus.PASS}
    assert {c.block_id for c in proposal.checks_preview} == {"b_02", None}


def test_proposal_leaves_the_input_untouched_and_after_is_a_valid_lesson(lesson: Lesson) -> None:
    snapshot = lesson.model_dump(mode="json")
    provider = MockProvider(responses=[four_words_reply(lesson)])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    assert lesson.model_dump(mode="json") == snapshot
    assert proposal.before.model_dump(mode="json") == snapshot
    Lesson.model_validate(proposal.after.model_dump(mode="json"))
    assert proposal.after.revisions == []
    assert proposal.patch and proposal.inverse
    assert any(op.path.startswith("/blocks/b_02/content/items") for op in proposal.patch)
    assert all(op.path.split("/")[1] in {"blocks", "sections", "checks"} for op in proposal.patch)


def test_messages_carry_the_prompt_rules_and_the_context(lesson: Lesson) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    system, user = provider.calls[0]["messages"]
    assert system["role"] == "system"
    assert "Only the selected blocks may be replaced or removed" in system["content"]
    assert "Stay inside this scope" in system["content"]
    assert user["role"] == "user"
    assert "## Instruction\nReduce new words to four" in user["content"]
    assert '"id": "b_02"' in user["content"]
    assert "JSON Schema" in user["content"]


def test_without_curriculum_the_proposal_is_grounded_but_not_level_checked(
    lesson: Lesson,
) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    proposal = propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES)
    on_block = {c.name: c.status for c in proposal.checks_preview if c.block_id == "b_02"}
    assert on_block[GROUNDING] is CheckStatus.PASS
    assert on_block["english.ipa_present"] is CheckStatus.STALE  # carried over, not re-run
    assert "english.vocab_level" not in on_block
    assert proposal.after.checks == []


# --- guardrails --------------------------------------------------------------------------------


def test_kind_change_is_reprompted_once_then_rejected(lesson: Lesson) -> None:
    as_summary = {"id": "b_02", "kind": "summary", "content": {"bullets": ["hello, hi"]}}
    provider = MockProvider(
        responses=[
            draft_reply(replacements={"b_02": as_summary}, rationale="first"),
            draft_reply(replacements={"b_02": as_summary}, rationale="second"),
        ]
    )
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)

    assert len(provider.calls) == 2
    assert raised.value.rationale == "second"
    assert raised.value.violations == [
        "the replacement for 'b_02' changes kind from 'vocabulary' to 'summary'; the kind must stay"
    ]
    assert "could not produce a valid edit" in str(raised.value)
    assert isinstance(raised.value, ValueError)
    retry = provider.calls[1]["messages"]
    assert [message["role"] for message in retry] == ["system", "user", "assistant", "user"]
    assert "changes kind from 'vocabulary' to 'summary'" in retry[-1]["content"]
    assert json.loads(retry[-2]["content"])["rationale"] == "first"


def test_out_of_selection_replacement_is_reprompted_and_the_corrected_reply_used(
    lesson: Lesson,
) -> None:
    pattern = lesson.blocks["b_03"].model_dump(mode="json")
    provider = MockProvider(
        responses=[draft_reply(replacements={"b_03": pattern}), four_words_reply(lesson)]
    )
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    assert len(provider.calls) == 2
    assert "'b_03', which is outside the selection" in provider.calls[1]["messages"][-1]["content"]
    assert set(proposal.replacements) == {"b_02"}
    assert words_of(proposal.after.blocks["b_02"]) == FOUR


def test_out_of_selection_removal_is_rejected(lesson: Lesson) -> None:
    provider = MockProvider(responses=[draft_reply(removed_block_ids=["b_03"])] * 2)
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert len(provider.calls) == 2
    assert raised.value.violations[0].startswith("removed_block_ids names 'b_03'")
    assert "b_03" in lesson.blocks


def test_id_change_and_unparseable_block_are_violations(lesson: Lesson) -> None:
    renamed = vocabulary_json(lesson, FOUR)
    renamed["id"] = "b_99"
    broken = {"id": "b_02", "kind": "vocabulary", "content": {"bullets": ["x"]}}
    provider = MockProvider(
        responses=[
            draft_reply(replacements={"b_02": renamed}),
            draft_reply(replacements={"b_02": broken}),
        ]
    )
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert "has id 'b_99'; the id must stay" in provider.calls[1]["messages"][-1]["content"]
    violation = raised.value.violations[0]
    assert violation.startswith("the replacement for 'b_02' does not parse as a block: ")
    assert "content.items: Field required" in violation


def test_activity_without_pupil_steps_is_reprompted_then_rejected(lesson: Lesson) -> None:
    request = EditRequest(target_block_ids=["b_07"], instruction="Make it pair work")
    activity = lesson.blocks["b_07"].model_dump(mode="json")
    activity["content"]["interaction"] = "pairs"
    activity["content"]["student_steps"] = []
    provider = MockProvider(responses=[draft_reply(replacements={"b_07": activity})] * 2)
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert len(provider.calls) == 2
    assert raised.value.violations == [
        "the replacement for 'b_07' fails english.steps_present: missing student_steps"
    ]


def test_an_empty_proposal_is_a_violation(lesson: Lesson) -> None:
    provider = MockProvider(responses=[draft_reply()] * 2)
    with pytest.raises(EditRejected, match="changes nothing"):
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)


def test_missing_id_and_kind_in_a_replacement_are_taken_from_the_original(lesson: Lesson) -> None:
    bare = {"content": {"items": vocabulary_json(lesson, FOUR)["content"]["items"]}}
    provider = MockProvider(responses=[draft_reply(replacements={"b_02": bare})])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    replaced = proposal.after.blocks["b_02"]
    assert (replaced.id, replaced.kind) == ("b_02", "vocabulary")
    assert words_of(replaced) == FOUR
    assert replaced.sources == []  # the model dropped the citation ...
    assert replaced.provenance is Provenance.GENERATED  # ... so grounding downgrades it


# --- additions and removals --------------------------------------------------------------------


def test_added_block_gets_a_fresh_id_and_follows_its_anchor(lesson: Lesson) -> None:
    note = {
        "id": "b_model",
        "kind": "teacher_note",
        "content": {"text": "Drill the four words with flashcards."},
    }
    provider = MockProvider(
        responses=[draft_reply(added_blocks=[{"after_block_id": "b_02", "block": note}])]
    )
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    assert len(proposal.added_blocks) == 1
    added = proposal.added_blocks[0]
    assert added.after_block_id == "b_02"
    assert added.block.id.startswith("b_")
    assert added.block.id not in lesson.blocks
    assert added.block.id != "b_model"
    assert "b_model" not in proposal.after.blocks
    section = proposal.after.section_of(added.block.id)
    assert section is not None
    assert section.block_ids == ["b_01", "b_02", added.block.id, "b_03", "b_05"]
    assert proposal.after.blocks[added.block.id].content.text == (  # type: ignore[union-attr]
        "Drill the four words with flashcards."
    )
    assert proposal.replacements == {}
    assert proposal.affected == []


def test_added_block_without_anchor_follows_the_last_target_and_is_checked(
    lesson: Lesson,
) -> None:
    game = {
        "kind": "activity",
        "content": {
            "type": "game",
            "title": "Hello ball",
            "skills": ["speaking"],
            "interaction": "whole_class",
            "minutes": 3,
            "instructions": "Say hello and pass the ball.",
            "teacher_steps": ["Start the ball."],
            "student_steps": ["Say hello.", "Pass the ball."],
        },
    }
    request = EditRequest(target_block_ids=["b_02", "b_03"], instruction="Add a quick game")
    provider = MockProvider(
        responses=[draft_reply(added_blocks=[{"after_block_id": None, "block": game}])]
    )
    proposal = propose_edit(
        lesson, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    added = proposal.added_blocks[0]
    assert added.after_block_id == "b_03"
    section = proposal.after.section_of(added.block.id)
    assert section is not None
    assert section.block_ids == ["b_01", "b_02", "b_03", added.block.id, "b_05"]
    on_block = {c.name: c.status for c in proposal.checks_preview if c.block_id == added.block.id}
    assert on_block["english.steps_present"] is CheckStatus.PASS
    assert "english.vocab_level" in on_block
    assert proposal.after.blocks[added.block.id].provenance is Provenance.GENERATED


def test_several_added_blocks_keep_their_order(lesson: Lesson) -> None:
    notes = [
        {
            "after_block_id": "b_02",
            "block": {"kind": "teacher_note", "content": {"text": f"Note {n}."}},
        }
        for n in (1, 2)
    ]
    provider = MockProvider(responses=[draft_reply(added_blocks=notes)])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    first, second = (added.block.id for added in proposal.added_blocks)
    assert first != second
    ids = proposal.after.sections[1].block_ids
    anchor = ids.index("b_02")
    assert ids[anchor + 1 : anchor + 3] == [first, second]


def test_added_block_anchor_must_be_a_target(lesson: Lesson) -> None:
    note = {"kind": "teacher_note", "content": {"text": "x"}}
    provider = MockProvider(
        responses=[draft_reply(added_blocks=[{"after_block_id": "b_07", "block": note}])] * 2
    )
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert "after_block_id 'b_07', which is not a selected block" in raised.value.violations[0]


def test_removed_target_disappears_from_sections_and_dependencies(lesson: Lesson) -> None:
    request = EditRequest(target_block_ids=["b_03"], instruction="Remove the sentence pattern")
    provider = MockProvider(responses=[draft_reply(removed_block_ids=["b_03"])])
    proposal = propose_edit(
        lesson, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    after = proposal.after
    assert proposal.removed_block_ids == ["b_03"]
    assert "b_03" not in after.blocks
    assert all("b_03" not in section.block_ids for section in after.sections)
    assert after.blocks["b_07"].depends_on == ["b_02"]
    assert lesson.blocks["b_07"].depends_on == ["b_02", "b_03"]
    assert [(a.block_id, a.suggested_action) for a in proposal.affected] == [
        ("b_07", "regenerate"),
        ("b_11", "review"),
        ("b_08", "regenerate"),
    ]
    assert [a.reason for a in proposal.affected] == [
        "depends_on b_03 — sentence_pattern",  # b_07 lists it
        "depends_on b_03 indirectly — sentence_pattern",  # b_11 via b_07
        "depends_on b_03 indirectly — sentence_pattern",  # b_08 via b_07
    ]
    assert [op.op for op in proposal.patch if op.path == "/blocks/b_03"] == ["remove"]
    assert [op.op for op in proposal.inverse if op.path == "/blocks/b_03"] == ["add"]


# --- direct edits ------------------------------------------------------------------------------


def test_direct_edit_of_a_drifted_extracted_block_is_downgraded(lesson: Lesson) -> None:
    colours = BlockAdapter.validate_python(
        {
            "id": "b_02",
            "kind": "vocabulary",
            "content": {
                "items": [
                    {"word": word, "example": example}
                    for word, example in (
                        ("red", "A red pen."),
                        ("blue", "A blue book."),
                        ("green", "A green bag."),
                        ("yellow", "A yellow ruler."),
                    )
                ]
            },
            "sources": [cite(GREETINGS_SPAN)],
            "provenance": "extracted",
        }
    )
    proposal = direct_edit(lesson, "b_02", colours, sources=SOURCES, curriculum=CURRICULUM)

    assert proposal.actor == "teacher"
    assert proposal.instruction is None
    assert proposal.rationale == ""
    assert proposal.target_block_ids == ["b_02"]
    after_block = proposal.after.blocks["b_02"]
    assert after_block.provenance is Provenance.GENERATED
    assert lesson.blocks["b_02"].provenance is Provenance.EXTRACTED
    grounding = next(
        c for c in proposal.checks_preview if c.block_id == "b_02" and c.name == GROUNDING
    )
    assert grounding.status is CheckStatus.WARN
    assert grounding.detail == "no supported citation; provenance set to generated"
    assert all(item.ipa for item in after_block.content.items)  # type: ignore[union-attr]
    assert [a.block_id for a in proposal.affected] == ["b_10", "b_07", "b_11", "b_08"]


def test_direct_edit_keeps_a_faithful_block_extracted(lesson: Lesson) -> None:
    block = lesson.blocks["b_02"].model_copy(deep=True)
    block.content.items = block.content.items[:4]  # type: ignore[union-attr]
    proposal = direct_edit(lesson, "b_02", block, sources=SOURCES, curriculum=CURRICULUM)
    assert proposal.after.blocks["b_02"].provenance is Provenance.EXTRACTED
    assert words_of(proposal.after.blocks["b_02"]) == FOUR


def test_direct_edit_rejects_id_or_kind_changes_and_unknown_blocks(lesson: Lesson) -> None:
    renamed = lesson.blocks["b_02"].model_copy(update={"id": "b_99"})
    with pytest.raises(EditRejected) as raised:
        direct_edit(lesson, "b_02", renamed)
    assert raised.value.violations == ["the edited block has id 'b_99'; it must stay 'b_02'"]
    assert raised.value.rationale is None
    with pytest.raises(EditRejected, match="changes kind"):
        direct_edit(lesson, "b_02", lesson.blocks["b_08"].model_copy(update={"id": "b_02"}))
    with pytest.raises(KeyError, match="b_99"):
        direct_edit(lesson, "b_99", lesson.blocks["b_02"])


# --- the edit stays inside the selection -------------------------------------------------------


def test_blocks_outside_the_selection_are_not_touched_by_grounding_or_validators(
    lesson: Lesson,
) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    for block_id, block in lesson.blocks.items():
        if block_id != "b_02":
            assert proposal.after.blocks[block_id] == block
    changed = {op.path.split("/")[2] for op in proposal.patch if op.path.startswith("/blocks/")}
    assert changed == {"b_02"}


def test_removal_touches_only_the_dependency_lists_of_other_blocks(lesson: Lesson) -> None:
    request = EditRequest(target_block_ids=["b_03"], instruction="Remove the sentence pattern")
    provider = MockProvider(responses=[draft_reply(removed_block_ids=["b_03"])])
    proposal = propose_edit(
        lesson, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    for block_id, block in lesson.blocks.items():
        if block_id in {"b_03", "b_07"}:
            continue
        assert proposal.after.blocks[block_id] == block
    expected = lesson.blocks["b_07"].model_copy(update={"depends_on": ["b_02"]})
    assert proposal.after.blocks["b_07"] == expected


def test_added_blocks_never_keep_a_provenance_the_model_wrote(lesson: Lesson) -> None:
    blocks = [
        {"kind": "teacher_note", "content": {"text": "x"}, "provenance": "extracted"},
        {"kind": "summary", "content": {"bullets": ["x"]}, "provenance": "derived"},
    ]
    provider = MockProvider(
        responses=[
            draft_reply(
                added_blocks=[{"after_block_id": "b_02", "block": block} for block in blocks]
            )
        ]
    )
    proposal = propose_edit(
        lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    assert [added.block.provenance for added in proposal.added_blocks] == [Provenance.GENERATED] * 2


def test_removing_the_only_objective_surfaces_the_outcome_warning(lesson: Lesson) -> None:
    checked = run_all(lesson, CURRICULUM, sources=SOURCES)
    request = EditRequest(target_block_ids=["b_01"], instruction="Remove the objective")
    provider = MockProvider(responses=[draft_reply(removed_block_ids=["b_01"])])
    proposal = propose_edit(
        checked, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM
    )
    covered = [c for c in proposal.checks_preview if c.name == "curriculum.outcome_covered"]
    assert [(c.block_id, c.status) for c in covered] == [(None, CheckStatus.WARN)]
    assert "no objective" in (covered[0].detail or "")


def test_a_lesson_level_warning_is_shown_even_when_it_was_already_there(lesson: Lesson) -> None:
    request = EditRequest(target_block_ids=["b_01"], instruction="Remove the objective")
    removal = draft_reply(removed_block_ids=["b_01"])
    first = propose_edit(
        run_all(lesson, CURRICULUM, sources=SOURCES),
        request,
        provider=MockProvider(responses=[removal]),
        sources=SOURCES,
        curriculum=CURRICULUM,
    )
    again = propose_edit(
        first.after,
        EditRequest(target_block_ids=["b_04"], instruction="Shorter title"),
        provider=MockProvider(
            responses=[
                draft_reply(
                    replacements={
                        "b_04": {
                            **first.after.blocks["b_04"].model_dump(mode="json"),
                            "teacher_note": "Keep it short.",
                        }
                    }
                )
            ]
        ),
        sources=SOURCES,
        curriculum=CURRICULUM,
    )
    warned = [c.name for c in again.checks_preview if c.status is CheckStatus.WARN]
    assert warned == ["curriculum.outcome_covered"]


def test_a_block_cannot_depend_on_itself(lesson: Lesson) -> None:
    looped = vocabulary_json(lesson, FOUR)
    looped["depends_on"] = ["b_02"]
    provider = MockProvider(responses=[draft_reply(replacements={"b_02": looped})] * 2)
    with pytest.raises(EditRejected) as raised:
        propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert raised.value.violations == ["the replacement for 'b_02' depends on itself"]


def test_an_added_block_cannot_follow_a_block_outside_every_section(lesson: Lesson) -> None:
    unplaced = lesson.model_copy(deep=True)
    for section in unplaced.sections:
        section.block_ids = [block_id for block_id in section.block_ids if block_id != "b_05"]
    unplaced = Lesson.model_validate(unplaced.model_dump())
    note = {"kind": "teacher_note", "content": {"text": "x"}}
    request = EditRequest(target_block_ids=["b_05"], instruction="Add a note")
    provider = MockProvider(
        responses=[draft_reply(added_blocks=[{"after_block_id": None, "block": note}])] * 2
    )
    with pytest.raises(EditRejected) as raised:
        propose_edit(unplaced, request, provider=provider, sources=SOURCES, curriculum=CURRICULUM)
    assert "not placed in any section" in raised.value.violations[0]


def test_an_unplaced_uncited_target_can_be_edited(lesson: Lesson) -> None:
    unplaced = lesson.model_copy(deep=True)
    for section in unplaced.sections:
        section.block_ids = [block_id for block_id in section.block_ids if block_id != "b_11"]
    unplaced = Lesson.model_validate(unplaced.model_dump())
    request = EditRequest(target_block_ids=["b_11"], instruction="Shorter")
    reply = draft_reply(
        replacements={"b_11": {"content": {"text": "Praise effort."}}}, rationale="Shortened."
    )
    proposal = propose_edit(
        unplaced,
        request,
        provider=MockProvider(responses=[reply]),
        sources=SOURCES,
        curriculum=CURRICULUM,
    )
    assert proposal.after.blocks["b_11"].content.text == "Praise effort."  # type: ignore[union-attr]
    assert proposal.after.section_of("b_11") is None


def test_affected_prefers_a_direct_dependency_over_an_indirect_one(lesson: Lesson) -> None:
    affected = compute_affected(lesson, ["b_03", "b_02"])
    reasons = {a.block_id: a.reason for a in affected}
    assert reasons["b_07"] == "depends_on b_03 \u2014 sentence_pattern"
    assert reasons["b_10"] == "depends_on b_02 \u2014 vocabulary"
    assert reasons["b_11"] == "depends_on b_03 indirectly \u2014 sentence_pattern"
    assert reasons["b_08"] == "depends_on b_02 \u2014 vocabulary"
    assert [a.block_id for a in affected] == ["b_10", "b_07", "b_11", "b_08"]
