from __future__ import annotations

import pytest

from classdeck.editing import (
    EditEngine,
    EditProposal,
    EditRequest,
    accept,
    apply_patch,
    direct_edit,
    make_patch,
    propose_edit,
    redo,
    snapshot,
    undo,
)
from classdeck.english import presets_for
from classdeck.english.validators import run_all
from classdeck.lesson import BlockKind, CheckResult, CheckStatus, Lesson, PatchOp
from classdeck.llm import MockProvider
from tests.test_editing_context import CURRICULUM, FOUR, REQUEST, SOURCES, make_edit_lesson
from tests.test_editing_proposal import draft_reply, four_words_reply, words_of


@pytest.fixture
def lesson() -> Lesson:
    return make_edit_lesson()


def four_words_proposal(lesson: Lesson) -> EditProposal:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    return propose_edit(lesson, REQUEST, provider=provider, sources=SOURCES, curriculum=CURRICULUM)


def note_edit(lesson: Lesson) -> EditProposal:
    note = lesson.blocks["b_11"].model_copy(deep=True)
    note.content.text = "Praise effort."  # type: ignore[union-attr]
    return direct_edit(lesson, "b_11", note, sources=SOURCES, curriculum=CURRICULUM)


# --- patches -----------------------------------------------------------------------------------


def test_make_patch_and_apply_patch_round_trip(lesson: Lesson) -> None:
    after = lesson.model_copy(deep=True)
    after.blocks["b_02"].content.items = after.blocks["b_02"].content.items[:4]  # type: ignore[union-attr]
    after.sections[1].title = "Presentation: four words"
    after.checks.append(
        CheckResult(name="english.timing", status=CheckStatus.PASS, detail="35 min")
    )
    before_snapshot = snapshot(lesson)

    patch, inverse = make_patch(lesson, after)

    assert patch and inverse
    assert all(isinstance(op, PatchOp) for op in [*patch, *inverse])
    assert all(
        op.path.split("/")[1] in {"blocks", "sections", "checks"} for op in [*patch, *inverse]
    )
    assert snapshot(apply_patch(lesson, patch)) == snapshot(after)
    assert snapshot(apply_patch(after, inverse)) == before_snapshot
    assert snapshot(lesson) == before_snapshot  # inputs are not changed
    assert make_patch(lesson, lesson) == ([], [])


def test_apply_patch_handles_move_operations_and_keeps_the_rest_of_the_lesson(
    lesson: Lesson,
) -> None:
    move = PatchOp.model_validate(
        {"op": "move", "path": "/sections/0/block_ids/0", "from": "/sections/1/block_ids/0"}
    )
    moved = apply_patch(lesson, [move])
    assert moved.sections[0].block_ids == ["b_01", "b_04"]
    assert moved.sections[1].block_ids == ["b_02", "b_03", "b_05"]
    assert (moved.id, moved.title, moved.blocks.keys()) == (
        lesson.id,
        lesson.title,
        lesson.blocks.keys(),
    )
    assert lesson.sections[0].block_ids == ["b_04"]


# --- accept ------------------------------------------------------------------------------------


def test_accept_appends_a_revision_and_marks_affected_checks_stale(lesson: Lesson) -> None:
    lesson = run_all(lesson, CURRICULUM, sources=SOURCES)  # every block carries results
    proposal = four_words_proposal(lesson)
    accepted = accept(lesson, proposal, curriculum=CURRICULUM, sources=SOURCES)

    assert words_of(accepted.blocks["b_02"]) == FOUR
    assert len(accepted.revisions) == 1
    revision = accepted.revisions[0]
    assert revision.id.startswith("r_")
    assert revision.parent is None
    assert revision.actor == "model"
    assert revision.instruction == "Reduce new words to four"
    assert revision.target_block_ids == ["b_02"]
    assert revision.patch and revision.inverse
    assert revision.flagged_block_ids == [a.block_id for a in proposal.affected]
    assert revision.flagged_block_ids == ["b_10", "b_07", "b_11", "b_08"]
    for block_id in ("b_10", "b_07", "b_08"):
        checks = accepted.blocks[block_id].checks
        assert checks
        assert all(check.status is CheckStatus.STALE for check in checks)
    assert accepted.blocks["b_02"].checks
    assert all(check.status is not CheckStatus.STALE for check in accepted.blocks["b_02"].checks)
    for block_id in ("b_01", "b_03", "b_04", "b_05"):  # neither selected nor affected
        assert accepted.blocks[block_id] == lesson.blocks[block_id]
    assert accepted.updated_at > lesson.updated_at
    # the input is untouched
    assert lesson.revisions == []
    assert words_of(lesson.blocks["b_02"]) == [*FOUR, "nice", "meet"]


def test_accept_chains_revisions_and_refuses_a_proposal_for_another_version(
    lesson: Lesson,
) -> None:
    first = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    second = accept(first, note_edit(first), curriculum=CURRICULUM, sources=SOURCES)
    assert [revision.actor for revision in second.revisions] == ["model", "teacher"]
    assert second.revisions[1].parent == second.revisions[0].id
    assert second.revisions[1].instruction is None
    assert second.revisions[1].target_block_ids == ["b_11"]
    assert second.revisions[1].flagged_block_ids == []
    assert second.blocks["b_11"].content.text == "Praise effort."  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="different version"):
        accept(second, four_words_proposal(lesson), curriculum=CURRICULUM)


def test_accept_without_curriculum_keeps_the_proposal_checks(lesson: Lesson) -> None:
    proposal = four_words_proposal(lesson)
    accepted = accept(lesson, proposal)
    assert snapshot(accepted)["blocks"]["b_02"] == snapshot(proposal.after)["blocks"]["b_02"]
    assert all(check.status is CheckStatus.STALE for check in accepted.blocks["b_07"].checks)
    assert len(accepted.revisions) == 1


# --- undo / redo -------------------------------------------------------------------------------


def test_undo_restores_blocks_sections_and_checks_exactly(lesson: Lesson) -> None:
    before = snapshot(lesson)
    accepted = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    assert snapshot(accepted) != before

    restored, revision = undo(accepted)

    assert revision == accepted.revisions[-1]
    assert snapshot(restored) == before
    assert restored.revisions == []
    assert restored.id == lesson.id
    assert len(accepted.revisions) == 1  # the input is not changed
    with pytest.raises(ValueError, match="nothing to undo"):
        undo(restored)


def test_redo_reapplies_the_revision(lesson: Lesson) -> None:
    accepted = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    restored, revision = undo(accepted)

    redone = redo(restored, revision)

    assert snapshot(redone) == snapshot(accepted)
    assert redone.revisions == [revision]
    assert words_of(redone.blocks["b_02"]) == FOUR
    with pytest.raises(ValueError, match="last revision"):
        redo(redone, revision)


def test_undo_twice_walks_back_two_revisions(lesson: Lesson) -> None:
    before = snapshot(lesson)
    first = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    second = accept(first, note_edit(first), curriculum=CURRICULUM, sources=SOURCES)
    once, undone = undo(second)
    assert undone.actor == "teacher"
    assert snapshot(once) == snapshot(first)
    twice, _ = undo(once)
    assert snapshot(twice) == before
    assert twice.revisions == []


# --- engine ------------------------------------------------------------------------------------


def test_engine_delegates_to_the_stage_functions(lesson: Lesson) -> None:
    provider = MockProvider(responses=[four_words_reply(lesson)])
    engine = EditEngine(provider, sources=SOURCES, curriculum=CURRICULUM)

    proposal = engine.propose(lesson, REQUEST)
    assert words_of(proposal.after.blocks["b_02"]) == FOUR
    accepted = engine.accept(lesson, proposal)
    assert len(accepted.revisions) == 1
    restored, revision = engine.undo(accepted)
    assert snapshot(restored) == snapshot(lesson)
    assert snapshot(engine.redo(restored, revision)) == snapshot(accepted)
    direct = engine.direct_edit(lesson, "b_02", lesson.blocks["b_02"])
    assert direct.actor == "teacher"
    assert len(provider.calls) == 1


def test_engine_presets_for_delegates_to_english_presets() -> None:
    engine = EditEngine(MockProvider())
    assert [preset.id for preset in engine.presets_for(BlockKind.VOCABULARY)] == [
        "reduce_words",
        "bilingual_notes",
    ]
    assert engine.presets_for(BlockKind.ACTIVITY) == presets_for(BlockKind.ACTIVITY)
    assert engine.presets_for(BlockKind.ACTIVITY)
    assert engine.presets_for(BlockKind.OBJECTIVE) == []


def test_undo_restores_a_lesson_after_additions_and_removals(lesson: Lesson) -> None:
    before = snapshot(lesson)
    note = {"kind": "teacher_note", "content": {"text": "Use flashcards."}}
    request = EditRequest(target_block_ids=["b_02", "b_03"], instruction="Rework")
    reply = draft_reply(
        added_blocks=[{"after_block_id": "b_02", "block": note}], removed_block_ids=["b_03"]
    )
    proposal = propose_edit(
        lesson,
        request,
        provider=MockProvider(responses=[reply]),
        sources=SOURCES,
        curriculum=CURRICULUM,
    )
    accepted = accept(lesson, proposal, curriculum=CURRICULUM, sources=SOURCES)
    assert proposal.added_blocks[0].block.id in accepted.blocks
    assert "b_03" not in accepted.blocks
    assert accepted.blocks["b_07"].depends_on == ["b_02"]

    restored, revision = undo(accepted)

    assert snapshot(restored) == before
    assert snapshot(redo(restored, revision)) == snapshot(accepted)


def test_undo_survives_a_save_and_load_round_trip(lesson: Lesson) -> None:
    accepted = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    reloaded = Lesson.model_validate_json(accepted.model_dump_json())
    restored, _ = undo(reloaded)
    assert snapshot(restored) == snapshot(lesson)


def test_undo_and_redo_report_a_patch_that_no_longer_applies(lesson: Lesson) -> None:
    accepted = accept(lesson, four_words_proposal(lesson), curriculum=CURRICULUM, sources=SOURCES)
    drifted = accepted.model_copy(deep=True)
    drifted.sections[1].block_ids.remove("b_02")
    del drifted.blocks["b_02"]
    for block in drifted.blocks.values():
        block.depends_on = [dep for dep in block.depends_on if dep != "b_02"]
    with pytest.raises(ValueError, match="cannot undo"):
        undo(Lesson.model_validate(drifted.model_dump()))
    restored, revision = undo(accepted)
    emptied = restored.model_copy(deep=True)
    emptied.sections[1].block_ids.remove("b_02")
    del emptied.blocks["b_02"]
    for block in emptied.blocks.values():
        block.depends_on = [dep for dep in block.depends_on if dep != "b_02"]
    with pytest.raises(ValueError, match="cannot redo"):
        redo(Lesson.model_validate(emptied.model_dump()), revision)


def test_accept_changes_only_the_edited_block(lesson: Lesson) -> None:
    lesson = run_all(lesson, CURRICULUM, sources=SOURCES)
    accepted = accept(lesson, note_edit(lesson), curriculum=CURRICULUM, sources=SOURCES)
    for block_id, block in lesson.blocks.items():
        if block_id != "b_11":
            assert accepted.blocks[block_id] == block
    patched = {op.path.split("/")[2] for op in accepted.revisions[0].patch if "/blocks/" in op.path}
    assert patched == {"b_11"}
