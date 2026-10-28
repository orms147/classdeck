"""Apply the edits a teacher made on the slides: one revision, then the usual re-checks.

The HTML deck sends (or embeds, for a standalone file) a list of ``{block_id, field, value}``
(``docs/output-formats.md``, "Editing on the slide"). :func:`apply_deck_edits` turns it into ONE
teacher direct edit through the editing module -- so grounding and the English validators run
as for any edit, the history gets a single :class:`~classdeck.lesson.Revision` and *Undo* takes
all of it back -- and then re-runs the lesson re-check (``pipeline.finish_lesson``) on the edited
blocks. The local app and ``classdeck import-edits`` both call it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from pydantic import ConfigDict, ValidationError

from classdeck.curriculum.loader import Curriculum
from classdeck.editing.fields import FieldEditError, set_fields
from classdeck.editing.history import accept, make_patch
from classdeck.editing.models import EditRejected
from classdeck.editing.proposal import direct_edit_many
from classdeck.editing.scope import restrict_results
from classdeck.lesson.schema import (
    Block,
    CheckStatus,
    Lesson,
    SourceDocument,
    Strict,
)
from classdeck.llm.base import LLMProvider

EDITS_SCRIPT_ID = "classdeck-edits"
"""``id`` of the ``<script type="application/json">`` a saved standalone deck carries."""
EDIT_LABEL = "Edited on the slide"
"""The history label of a revision made this way."""

Sources = Mapping[str, SourceDocument]


class DeckEdit(Strict):
    """One changed field: the block, the path into it and the new text."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=False)

    block_id: str
    field: str
    value: str


@dataclass
class Notice:
    """Something the teacher should know after the save (shown as a toast)."""

    block_id: str
    kind: str  # "provenance" | "check"
    message: str

    def as_json(self) -> dict[str, str]:
        return {"block_id": self.block_id, "kind": self.kind, "message": self.message}


@dataclass
class DeckEditResult:
    lesson: Lesson
    changed_block_ids: list[str] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changed_block_ids)


def parse_edits(raw: Sequence[Mapping[str, Any]] | Sequence[DeckEdit]) -> list[DeckEdit]:
    """Typed edits from JSON-ish data; the last edit of a field wins (``FieldEditError`` if bad)."""
    edits: dict[tuple[str, str], DeckEdit] = {}
    for position, entry in enumerate(raw):
        try:
            edit = entry if isinstance(entry, DeckEdit) else DeckEdit.model_validate(entry)
        except ValidationError as exc:
            raise FieldEditError(
                f"edit {position + 1} is malformed: {exc.errors()[0]['msg']}"
            ) from exc
        edits[(edit.block_id, edit.field)] = edit
    return list(edits.values())


class _EditsFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lesson_id: str | None = None
        self.payloads: list[str] = []
        self._inside = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and values.get("id") == EDITS_SCRIPT_ID:
            self._inside = True
            self.payloads.append("")
        if tag == "meta" and values.get("name") == "classdeck-lesson":
            self.lesson_id = values.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self.payloads[-1] += data


def read_embedded_edits(document: str) -> tuple[str | None, list[DeckEdit]]:
    """``(lesson id, edits)`` from the edit list a saved standalone deck embeds.

    The lesson id comes from the deck's ``classdeck-lesson`` meta tag (or the embedded list).
    Raises ``ValueError`` when the page has no readable edit list.
    """
    finder = _EditsFinder()
    finder.feed(document)
    finder.close()
    if not finder.payloads:
        raise ValueError(
            "this page has no embedded edits (id="
            f"{EDITS_SCRIPT_ID!r}); save it from the deck with the Save button first"
        )
    try:
        data = json.loads(finder.payloads[-1])
    except ValueError as exc:
        raise ValueError(f"the embedded edit list is not valid JSON: {exc}") from exc
    lesson_id = finder.lesson_id
    if isinstance(data, dict):
        lesson_id = data.get("lesson_id") or lesson_id
        data = data.get("edits", [])
    if not isinstance(data, list):
        raise ValueError("the embedded edit list must be a list of edits")
    return lesson_id, parse_edits(data)


def _notices(before: Lesson, after: Lesson, block_ids: Sequence[str]) -> list[Notice]:
    found: list[Notice] = []
    for block_id in block_ids:
        old, new = before.blocks[block_id], after.blocks[block_id]
        if old.provenance != new.provenance:
            found.append(
                Notice(
                    block_id,
                    "provenance",
                    f"{block_id} is now {new.provenance.value}, no longer {old.provenance.value}: "
                    "its text does not match the textbook source any more.",
                )
            )
        previous = {(c.name, c.status, c.detail) for c in old.checks}
        for check in new.checks:
            if check.status in (CheckStatus.WARN, CheckStatus.FAIL) and (
                (check.name, check.status, check.detail) not in previous
            ):
                found.append(
                    Notice(
                        block_id,
                        "check",
                        f"{block_id}: {check.name} {check.status.value}"
                        + (f" - {check.detail}" if check.detail else ""),
                    )
                )
    return found


def apply_deck_edits(
    lesson: Lesson,
    edits: Sequence[DeckEdit],
    *,
    sources: Sources,
    curriculum: Curriculum,
    judge: LLMProvider | None = None,
    label: str = EDIT_LABEL,
) -> DeckEditResult:
    """The lesson with all ``edits`` applied as one revision and re-checked.

    Edits that leave a field as it is are ignored; when nothing changes the lesson is returned
    as it is with no revision. Raises :class:`FieldEditError` (naming the block and field) for
    an edit the content model refuses and :class:`~classdeck.editing.EditRejected` if the edited
    blocks break an editing guardrail. Nothing is saved here.
    """
    from classdeck.pipeline import finish_lesson

    grouped: dict[str, list[tuple[str, str]]] = {}
    for edit in parse_edits(list(edits)):
        grouped.setdefault(edit.block_id, []).append((edit.field, edit.value))

    new_blocks: dict[str, Block] = {}
    for block_id, pairs in grouped.items():
        block = lesson.blocks.get(block_id)
        if block is None:
            raise FieldEditError(
                f"There is no block {block_id!r} in this lesson.",
                block_id=block_id,
                field=pairs[0][0],
            )
        updated = set_fields(block, pairs)
        if updated != block:
            new_blocks[block_id] = updated
    if not new_blocks:
        return DeckEditResult(lesson)

    try:
        proposal = direct_edit_many(
            lesson,
            new_blocks,
            instruction=label,
            sources=sources,
            curriculum=curriculum,
            grounding_provider=judge,
        )
    except EditRejected as exc:
        raise FieldEditError("; ".join(exc.violations)) from exc
    accepted = accept(lesson, proposal, curriculum=curriculum, sources=sources)

    # The full re-check (dependency inference, citation quotes, grounding, level checks); only the
    # edited blocks and the lesson-level results take its output, so nothing else moves.
    edited = list(new_blocks)
    final = restrict_results(accepted, finish_lesson(accepted, sources, curriculum, judge), edited)
    patch, inverse = make_patch(lesson, final)
    final.revisions[-1] = final.revisions[-1].model_copy(
        update={"patch": patch, "inverse": inverse}
    )
    final.touch()
    return DeckEditResult(final, edited, _notices(lesson, final, edited))
