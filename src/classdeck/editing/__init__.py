"""Editing: block-level proposals the teacher previews and accepts (``docs/editing.md``)."""

from __future__ import annotations

from classdeck.editing.affected import compute_affected
from classdeck.editing.context import (
    MAX_EXTRA_SPANS,
    EditContext,
    build_edit_context,
    render_context,
)
from classdeck.editing.engine import EditEngine
from classdeck.editing.history import (
    PATCHED_KEYS,
    accept,
    apply_patch,
    make_patch,
    redo,
    snapshot,
    undo,
)
from classdeck.editing.models import (
    AddedBlock,
    Affected,
    CheckPreview,
    EditProposal,
    EditProposalDraft,
    EditRejected,
    EditRequest,
)
from classdeck.editing.proposal import (
    EDIT_PROMPT,
    ValidatedDraft,
    apply_draft,
    direct_edit,
    propose_edit,
    validate_draft,
)

__all__ = [
    "EDIT_PROMPT",
    "MAX_EXTRA_SPANS",
    "PATCHED_KEYS",
    "AddedBlock",
    "Affected",
    "CheckPreview",
    "EditContext",
    "EditEngine",
    "EditProposal",
    "EditProposalDraft",
    "EditRejected",
    "EditRequest",
    "ValidatedDraft",
    "accept",
    "apply_draft",
    "apply_patch",
    "build_edit_context",
    "compute_affected",
    "direct_edit",
    "make_patch",
    "propose_edit",
    "redo",
    "render_context",
    "snapshot",
    "undo",
    "validate_draft",
]
