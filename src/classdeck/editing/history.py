"""Revisions as JSON Patch pairs: accept, undo and redo (``docs/editing.md`` section 5).

A patch covers ``blocks``, ``sections`` and ``checks`` of the lesson. The history itself and the
timestamps stay outside it, so undo restores the content exactly and keeps the audit trail.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import jsonpatch

from classdeck.curriculum.loader import Curriculum
from classdeck.editing.models import EditProposal
from classdeck.editing.scope import restrict_results
from classdeck.english.validators import mark_stale, run_all
from classdeck.lesson.ids import new_id
from classdeck.lesson.schema import Lesson, PatchOp, Revision, SourceDocument

PATCHED_KEYS: tuple[str, ...] = ("blocks", "sections", "checks")
"""The top-level lesson keys a revision's patch may touch."""

Sources = Mapping[str, SourceDocument]

_PATCH_ERRORS = (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException)


def snapshot(lesson: Lesson) -> dict[str, Any]:
    """The patched part of a lesson as JSON-ready data: ``blocks``, ``sections``, ``checks``."""
    data = lesson.model_dump(mode="json")
    return {key: data[key] for key in PATCHED_KEYS}


def make_patch(before: Lesson, after: Lesson) -> tuple[list[PatchOp], list[PatchOp]]:
    """``(patch, inverse)``: ``patch`` turns ``before`` into ``after``, ``inverse`` the reverse."""
    source, target = snapshot(before), snapshot(after)
    patch = _to_ops(jsonpatch.make_patch(source, target))
    inverse = _to_ops(jsonpatch.make_patch(target, source))
    return patch, inverse


def _to_ops(patch: jsonpatch.JsonPatch) -> list[PatchOp]:
    return [PatchOp.model_validate(operation) for operation in patch.patch]


def apply_patch(lesson: Lesson, ops: Iterable[PatchOp]) -> Lesson:
    """A new lesson with the operations applied; the input is not changed."""
    data = lesson.model_dump(mode="json")
    operations = [op.model_dump(mode="json") for op in ops]
    return Lesson.model_validate(jsonpatch.apply_patch(data, operations))


def accept(
    lesson: Lesson,
    proposal: EditProposal,
    *,
    curriculum: Curriculum | None = None,
    sources: Sources | None = None,
) -> Lesson:
    """The lesson with the proposal applied and a :class:`Revision` appended.

    The proposal must have been built against ``lesson`` as it stands (``ValueError``
    otherwise), since its ``after`` is what gets accepted. With a curriculum the validators run
    again, taking over only the replaced and added blocks and the lesson-level results; checks
    on the affected blocks are then marked ``stale``. The revision's patch pair is computed from
    that final state, so undo restores the lesson exactly.
    """
    if snapshot(proposal.before) != snapshot(lesson):
        raise ValueError("the proposal was built against a different version of the lesson")
    result = proposal.after.model_copy(deep=True)
    result.revisions = [revision.model_copy(deep=True) for revision in lesson.revisions]
    if curriculum is not None:
        changed = [*proposal.replacements, *(added.block.id for added in proposal.added_blocks)]
        result = restrict_results(result, run_all(result, curriculum, sources=sources), changed)
    flagged = [affected.block_id for affected in proposal.affected]
    result = mark_stale(result, flagged)
    patch, inverse = make_patch(lesson, result)
    result.revisions.append(
        Revision(
            id=new_id("r"),
            parent=lesson.revisions[-1].id if lesson.revisions else None,
            actor=proposal.actor,
            instruction=proposal.instruction,
            target_block_ids=list(proposal.target_block_ids),
            patch=patch,
            inverse=inverse,
            flagged_block_ids=flagged,
        )
    )
    result.touch()
    return result


def undo(lesson: Lesson) -> tuple[Lesson, Revision]:
    """Apply the inverse of the last revision and drop it; returns ``(lesson, revision)``."""
    if not lesson.revisions:
        raise ValueError("nothing to undo: the lesson has no revisions")
    revision = lesson.revisions[-1]
    try:
        result = apply_patch(lesson, revision.inverse)
    except _PATCH_ERRORS as exc:
        raise ValueError(f"cannot undo revision {revision.id!r}: {exc}") from exc
    result.revisions = result.revisions[:-1]
    result.touch()
    return result, revision


def redo(lesson: Lesson, revision: Revision) -> Lesson:
    """Re-apply an undone revision's patch and append the revision again.

    History is linear: the revision's ``parent`` must be the lesson's last revision.
    """
    last = lesson.revisions[-1].id if lesson.revisions else None
    if revision.parent != last:
        raise ValueError(
            f"revision {revision.id!r} follows {revision.parent!r}, "
            f"but the last revision is {last!r}"
        )
    try:
        result = apply_patch(lesson, revision.patch)
    except _PATCH_ERRORS as exc:
        raise ValueError(f"cannot redo revision {revision.id!r}: {exc}") from exc
    result.revisions = [*result.revisions, revision.model_copy(deep=True)]
    result.touch()
    return result
