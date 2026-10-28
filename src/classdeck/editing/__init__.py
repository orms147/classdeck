"""Editing: block-level proposals the teacher previews and accepts (``docs/editing.md``)."""

from __future__ import annotations

from classdeck.editing.affected import compute_affected
from classdeck.editing.context import (
    MAX_EXTRA_SPANS,
    EditContext,
    build_edit_context,
    render_context,
)
from classdeck.editing.deck_edits import (
    EDITS_SCRIPT_ID,
    DeckEdit,
    DeckEditResult,
    Notice,
    apply_deck_edits,
    read_embedded_edits,
)
from classdeck.editing.engine import EditEngine
from classdeck.editing.fields import (
    FieldEditError,
    editable_fields,
    set_field,
    set_fields,
)
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
    direct_edit_many,
    propose_edit,
    validate_draft,
)

__all__ = [
    "EDITS_SCRIPT_ID",
    "EDIT_PROMPT",
    "MAX_EXTRA_SPANS",
    "PATCHED_KEYS",
    "AddedBlock",
    "Affected",
    "CheckPreview",
    "DeckEdit",
    "DeckEditResult",
    "EditContext",
    "EditEngine",
    "EditProposal",
    "EditProposalDraft",
    "EditRejected",
    "EditRequest",
    "FieldEditError",
    "Notice",
    "ValidatedDraft",
    "accept",
    "apply_deck_edits",
    "apply_draft",
    "apply_patch",
    "build_edit_context",
    "compute_affected",
    "direct_edit",
    "direct_edit_many",
    "editable_fields",
    "make_patch",
    "propose_edit",
    "read_embedded_edits",
    "redo",
    "render_context",
    "set_field",
    "set_fields",
    "snapshot",
    "undo",
    "validate_draft",
]
