"""Models of the editing stage: the request, the model's draft and the proposal the engine builds.

The model returns only ``replacements``, ``added_blocks``, ``removed_block_ids`` and
``rationale`` (:class:`EditProposalDraft`); the engine validates them and fills in the rest of
:class:`EditProposal`. See ``docs/editing.md`` section 4.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from classdeck.lesson.schema import Block, CheckStatus, Lesson, PatchOp, Strict

Actor = Literal["teacher", "model"]
SuggestedAction = Literal["regenerate", "review"]


class EditRequest(Strict):
    """What the teacher selected and asked for."""

    target_block_ids: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)


class AddedBlock(Strict):
    """A new block and the selected block it follows."""

    after_block_id: str | None = None
    block: Block


class DraftAddedBlock(Strict):
    """An added block as the model writes it: raw JSON, placed after a selected block or last."""

    after_block_id: str | None = None
    block: dict[str, Any]


class EditProposalDraft(Strict):
    """The reply shape asked of the model. Block JSON is parsed and checked by the engine."""

    replacements: dict[str, dict[str, Any]] = {}
    added_blocks: list[DraftAddedBlock] = []
    removed_block_ids: list[str] = []
    rationale: str


class Affected(Strict):
    """A block that depends on changed content; flagged, never rewritten by the engine."""

    block_id: str
    reason: str
    suggested_action: SuggestedAction


class CheckPreview(Strict):
    """One check result on the proposed lesson; ``block_id`` is ``None`` for a lesson-level one."""

    block_id: str | None
    name: str
    status: CheckStatus
    detail: str | None = None


class EditProposal(Strict):
    """A validated, applied and re-checked edit, ready for the teacher to preview and accept."""

    id: str
    instruction: str | None
    actor: Actor
    target_block_ids: list[str]
    replacements: dict[str, Block]
    added_blocks: list[AddedBlock]
    removed_block_ids: list[str]
    rationale: str
    affected: list[Affected]
    checks_preview: list[CheckPreview]
    before: Lesson
    after: Lesson
    patch: list[PatchOp]
    inverse: list[PatchOp]


class EditRejected(ValueError):
    """No valid edit after the re-prompt; ``violations`` name the guardrails that were hit."""

    def __init__(self, violations: list[str], rationale: str | None = None) -> None:
        super().__init__("could not produce a valid edit: " + "; ".join(violations))
        self.violations = list(violations)
        self.rationale = rationale
