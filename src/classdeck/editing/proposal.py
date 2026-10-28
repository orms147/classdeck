"""Turn an instruction into an :class:`EditProposal`: ask the model, validate, apply, re-check.

The model proposes replacements, additions and removals inside the selection. The engine
enforces the guardrails of ``docs/editing.md`` section 6, re-prompts once with the violations
named, then applies the draft to a copy of the lesson and runs grounding and the English
validators on it, so the teacher previews the checked result. A direct (hand) edit takes the same
path without the model (section 7).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from pydantic import ValidationError

from classdeck.curriculum.loader import Curriculum
from classdeck.editing.affected import compute_affected
from classdeck.editing.context import build_edit_context, render_context
from classdeck.editing.history import make_patch
from classdeck.editing.models import (
    Actor,
    AddedBlock,
    CheckPreview,
    EditProposal,
    EditProposalDraft,
    EditRejected,
    EditRequest,
)
from classdeck.editing.scope import restrict_results
from classdeck.english.validators import mark_stale, run_all
from classdeck.grounding.verifier import verify_lesson
from classdeck.lesson.ids import new_id
from classdeck.lesson.schema import (
    ActivityBlock,
    Block,
    BlockAdapter,
    CheckStatus,
    Lesson,
    Provenance,
    SourceDocument,
)
from classdeck.llm.base import LLMProvider, Message
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import complete_json

EDIT_PROMPT = "edit_blocks"
STEPS_PRESENT = "english.steps_present"

Sources = Mapping[str, SourceDocument]

_MAX_REPORTED_ERRORS = 5


class ValidatedDraft(NamedTuple):
    """The parts of a draft that passed validation, as typed blocks."""

    replacements: dict[str, Block]
    added_blocks: list[AddedBlock]
    removed_block_ids: list[str]


def propose_edit(
    lesson: Lesson,
    request: EditRequest,
    *,
    provider: LLMProvider,
    sources: Sources | None = None,
    curriculum: Curriculum | None = None,
    grounding_provider: LLMProvider | None = None,
) -> EditProposal:
    """Ask the model for an edit and return the validated, applied and checked proposal.

    A draft that breaks a guardrail is sent back once with the violations named; a second
    invalid draft raises :class:`EditRejected` with the model's rationale.
    """
    context = build_edit_context(lesson, request, sources=sources, curriculum=curriculum)
    target_ids = list(dict.fromkeys(request.target_block_ids))
    messages = [
        Message(role="system", content=load_prompt(EDIT_PROMPT, rules=context.rules)),
        Message(role="user", content=render_context(context, request)),
    ]
    draft = complete_json(provider, messages, EditProposalDraft, retries=1)
    validated, violations = validate_draft(lesson, target_ids, draft)
    if violations:
        retry = [
            *messages,
            Message(role="assistant", content=draft.model_dump_json()),
            Message(role="user", content=_violation_message(violations)),
        ]
        draft = complete_json(provider, retry, EditProposalDraft, retries=1)
        validated, violations = validate_draft(lesson, target_ids, draft)
        if violations:
            raise EditRejected(violations, draft.rationale)
    return _build_proposal(
        lesson,
        target_ids,
        validated,
        actor="model",
        instruction=request.instruction,
        rationale=draft.rationale,
        sources=sources,
        curriculum=curriculum,
        grounding_provider=grounding_provider,
    )


def direct_edit(
    lesson: Lesson,
    block_id: str,
    new_block: Block,
    *,
    sources: Sources | None = None,
    curriculum: Curriculum | None = None,
    grounding_provider: LLMProvider | None = None,
) -> EditProposal:
    """A teacher's hand edit of one block as a proposal (``actor = teacher``, no instruction).

    The id and kind must stay (:class:`EditRejected` otherwise); grounding and the validators run
    as for a model edit, so an ``extracted`` block whose text drifted from its citation is
    downgraded in ``after``.
    """
    return direct_edit_many(
        lesson,
        {block_id: new_block},
        sources=sources,
        curriculum=curriculum,
        grounding_provider=grounding_provider,
    )


def direct_edit_many(
    lesson: Lesson,
    new_blocks: Mapping[str, Block],
    *,
    instruction: str | None = None,
    sources: Sources | None = None,
    curriculum: Curriculum | None = None,
    grounding_provider: LLMProvider | None = None,
) -> EditProposal:
    """A teacher's hand edit of several blocks as ONE proposal, so accepting it is one revision.

    Same rules as :func:`direct_edit` for every block. ``instruction`` is only a label for the
    history (``None`` shows as a direct edit).
    """
    if not new_blocks:
        raise EditRejected(["no block to edit"])
    violations: list[str] = []
    for block_id, new_block in new_blocks.items():
        if block_id not in lesson.blocks:
            raise KeyError(f"unknown block {block_id!r}")
        original = lesson.blocks[block_id]
        if new_block.id != block_id:
            violations.append(
                f"the edited block has id {new_block.id!r}; it must stay {block_id!r}"
            )
        if new_block.kind != original.kind:
            violations.append(
                f"the edited block {block_id!r} changes kind from "
                f"{original.kind!r} to {new_block.kind!r}"
            )
        unknown = [dep for dep in new_block.depends_on if dep not in lesson.blocks]
        if unknown:
            violations.append(
                f"the edited block {block_id!r} depends on unknown block(s): {', '.join(unknown)}"
            )
    if violations:
        raise EditRejected(violations)
    validated = ValidatedDraft(dict(new_blocks), [], [])
    return _build_proposal(
        lesson,
        list(new_blocks),
        validated,
        actor="teacher",
        instruction=instruction,
        rationale="",
        sources=sources,
        curriculum=curriculum,
        grounding_provider=grounding_provider,
    )


# --- validation --------------------------------------------------------------------------------


def validate_draft(
    lesson: Lesson, target_ids: list[str], draft: EditProposalDraft
) -> tuple[ValidatedDraft, list[str]]:
    """Check a draft against the guardrails: what parsed, and every violation found.

    Replacements and removals must stay inside the selection, a replacement keeps its id and
    kind, added blocks get fresh ids and follow a selected block, dependencies must exist and an
    activity must pass ``english.steps_present``. A missing ``id`` or ``kind`` on a replacement
    is taken from the original.
    """
    violations: list[str] = []
    targets = set(target_ids)
    removed = list(dict.fromkeys(draft.removed_block_ids))
    for block_id in removed:
        if block_id not in targets:
            violations.append(
                f"removed_block_ids names {block_id!r}, which is outside the selection {target_ids}"
            )

    replacements: dict[str, Block] = {}
    for block_id, data in draft.replacements.items():
        if block_id not in targets:
            violations.append(
                f"replacements names {block_id!r}, which is outside the selection {target_ids}"
            )
            continue
        if block_id in removed:
            violations.append(f"{block_id!r} is both replaced and removed; choose one")
            continue
        original = lesson.blocks[block_id]
        label = f"the replacement for {block_id!r}"
        block = _parse_block({"id": block_id, "kind": original.kind, **data}, label, violations)
        if block is None:
            continue
        valid = True
        if block.id != block_id:
            violations.append(f"{label} has id {block.id!r}; the id must stay")
            valid = False
        if block.kind != original.kind:
            violations.append(
                f"{label} changes kind from {original.kind!r} to {block.kind!r}; the kind must stay"
            )
            valid = False
        if valid:
            replacements[block_id] = block

    added: list[AddedBlock] = []
    taken = set(lesson.blocks)
    for index, entry in enumerate(draft.added_blocks):
        label = f"added_blocks[{index}]"
        if entry.after_block_id is not None and entry.after_block_id not in targets:
            violations.append(
                f"{label} has after_block_id {entry.after_block_id!r}, "
                "which is not a selected block"
            )
        elif lesson.section_of(entry.after_block_id or target_ids[-1]) is None:
            violations.append(f"{label} would follow a block that is not placed in any section")
        data = {**entry.block, "id": entry.block.get("id") or "b_new"}
        block = _parse_block(data, label, violations)
        if block is None:
            continue
        fresh = _fresh_id(taken)
        taken.add(fresh)
        block = block.model_copy(
            update={"id": fresh, "checks": [], "provenance": Provenance.GENERATED}
        )
        added.append(AddedBlock(after_block_id=entry.after_block_id, block=block))

    known = (set(lesson.blocks) - set(removed)) | {entry.block.id for entry in added}
    labelled = [
        *((f"the replacement for {block_id!r}", block) for block_id, block in replacements.items()),
        *((f"the added block {entry.block.id!r}", entry.block) for entry in added),
    ]
    for label, block in labelled:
        if block.id in block.depends_on:
            violations.append(f"{label} depends on itself")
        unknown = [dependency for dependency in block.depends_on if dependency not in known]
        if unknown:
            violations.append(f"{label} depends on unknown block(s): {', '.join(unknown)}")
        missing = _steps_missing(block)
        if missing:
            violations.append(f"{label} fails {STEPS_PRESENT}: missing {', '.join(missing)}")

    if not (replacements or added or removed or violations):
        violations.append("the proposal changes nothing: no replacements, additions or removals")
    return ValidatedDraft(replacements, added, removed), violations


def _parse_block(data: dict[str, Any], label: str, violations: list[str]) -> Block | None:
    try:
        return BlockAdapter.validate_python(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        reported = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in errors[:_MAX_REPORTED_ERRORS]
        )
        violations.append(f"{label} does not parse as a block: {reported}")
        return None


def _fresh_id(taken: set[str]) -> str:
    candidate = new_id("b")
    while candidate in taken:
        candidate = new_id("b")
    return candidate


def _steps_missing(block: Block) -> list[str]:
    """What ``english.steps_present`` would report as missing on an activity."""
    if not isinstance(block, ActivityBlock):
        return []
    content = block.content
    present = (
        ("skills", bool(content.skills)),
        ("teacher_steps", any(step.strip() for step in content.teacher_steps)),
        ("student_steps", any(step.strip() for step in content.student_steps)),
    )
    return [name for name, ok in present if not ok]


def _violation_message(violations: list[str]) -> str:
    listed = "\n".join(f"- {violation}" for violation in violations)
    return (
        "The editing engine rejected that proposal:\n"
        f"{listed}\n"
        "Respond again with a corrected proposal. Replace or remove only the selected blocks, "
        "keep each block's id and kind, and put new blocks in added_blocks."
    )


# --- applying ----------------------------------------------------------------------------------


def apply_draft(lesson: Lesson, validated: ValidatedDraft) -> Lesson:
    """A copy of the lesson with the replacements, insertions and removals applied.

    Replacements keep the original block's provenance and checks (grounding and the validators
    reassign them). Each added block goes into its anchor's section right after the anchor, or
    after the block added there before it; an anchor outside every section leaves it unplaced.
    Removed ids disappear from the sections and from every ``depends_on``.
    """
    after = lesson.model_copy(deep=True)
    for block_id, block in validated.replacements.items():
        original = after.blocks[block_id]
        after.blocks[block_id] = block.model_copy(
            deep=True, update={"provenance": original.provenance, "checks": original.checks}
        )
    cursor: dict[str, str] = {}
    for added in validated.added_blocks:
        anchor = added.after_block_id
        if anchor is None:
            raise ValueError("added block without an anchor; resolve after_block_id first")
        block = added.block.model_copy(deep=True)
        after.blocks[block.id] = block
        section = after.section_of(anchor)
        if section is not None:
            previous = cursor.get(anchor, anchor)
            section.block_ids.insert(section.block_ids.index(previous) + 1, block.id)
            cursor[anchor] = block.id
    for block_id in validated.removed_block_ids:
        del after.blocks[block_id]
        for section in after.sections:
            section.block_ids = [bid for bid in section.block_ids if bid != block_id]
        for block in after.blocks.values():
            block.depends_on = [dep for dep in block.depends_on if dep != block_id]
    return Lesson.model_validate(after.model_dump())


def _build_proposal(
    lesson: Lesson,
    target_ids: list[str],
    validated: ValidatedDraft,
    *,
    actor: Actor,
    instruction: str | None,
    rationale: str,
    sources: Sources | None,
    curriculum: Curriculum | None,
    grounding_provider: LLMProvider | None,
) -> EditProposal:
    added = [
        AddedBlock(after_block_id=entry.after_block_id or target_ids[-1], block=entry.block)
        for entry in validated.added_blocks
    ]
    replaced_ids = [block_id for block_id in target_ids if block_id in validated.replacements]
    added_ids = [entry.block.id for entry in added]
    applied = apply_draft(
        lesson, ValidatedDraft(validated.replacements, added, validated.removed_block_ids)
    )
    # Replaced blocks keep their earlier results only as ``stale``: grounding and the validators
    # replace what they own, and anything they cannot re-run stays visibly out of date.
    applied = mark_stale(applied, replaced_ids)
    documents = sources or {}
    checked = verify_lesson(applied, documents, grounding_provider)
    if curriculum is not None:
        checked = run_all(checked, curriculum, sources=sources)
    # Both passes look at the whole lesson; only the changed blocks take their results.
    after = restrict_results(applied, checked, [*replaced_ids, *added_ids])

    affected = compute_affected(lesson, [*replaced_ids, *validated.removed_block_ids])
    patch, inverse = make_patch(lesson, after)
    return EditProposal(
        id=new_id("ep"),
        instruction=instruction,
        actor=actor,
        target_block_ids=list(target_ids),
        replacements={block_id: after.blocks[block_id] for block_id in replaced_ids},
        added_blocks=[
            AddedBlock(after_block_id=entry.after_block_id, block=after.blocks[entry.block.id])
            for entry in added
        ],
        removed_block_ids=list(validated.removed_block_ids),
        rationale=rationale,
        affected=affected,
        checks_preview=_checks_preview(lesson, after, [*replaced_ids, *added_ids]),
        before=lesson.model_copy(deep=True),
        after=after,
        patch=patch,
        inverse=inverse,
    )


def _checks_preview(before: Lesson, after: Lesson, block_ids: list[str]) -> list[CheckPreview]:
    """Every check on the given blocks, plus lesson-level checks that changed or are not a pass.

    A lesson-level warning is always shown, so a removal that leaves an outcome uncovered
    (``curriculum.outcome_covered``) is visible even when the lesson already warned.
    """
    preview = [
        CheckPreview(block_id=block_id, name=check.name, status=check.status, detail=check.detail)
        for block_id in block_ids
        for check in after.blocks[block_id].checks
    ]
    previous = {check.name: (check.status, check.detail) for check in before.checks}
    preview.extend(
        CheckPreview(block_id=None, name=check.name, status=check.status, detail=check.detail)
        for check in after.checks
        if check.status is not CheckStatus.PASS
        or previous.get(check.name) != (check.status, check.detail)
    )
    return preview
