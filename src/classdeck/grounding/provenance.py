"""Assign block provenance from verified citations.

Provenance is decided here, never by the model (``docs/lesson-model.md``, "Provenance"): a
content block is ``extracted`` when at least one citation verified and ``generated`` otherwise,
unless it is ``derived`` from other blocks. Framing blocks (objective, teacher note, summary)
are not grounded and keep what they have.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from classdeck.english.rules import NON_CONTENT_KINDS
from classdeck.lesson.schema import Block, Lesson, Provenance

if TYPE_CHECKING:
    from classdeck.grounding.verifier import CitationVerdict


def blocks_to_ground(lesson: Lesson) -> list[Block]:
    """Content blocks in lesson order, followed by any content block not placed in a section.

    ``Lesson.content_blocks()`` leaves unplaced blocks out; grounding must not, or they would
    keep the drafter's provenance and stale checks.
    """
    ordered = lesson.ordered_blocks()
    placed = {block.id for block in ordered}
    unplaced = (block for key, block in lesson.blocks.items() if key not in placed)
    return [block for block in (*ordered, *unplaced) if block.kind not in NON_CONTENT_KINDS]


def provenance_for(block: Block, supported: bool) -> Provenance:
    """The provenance grounding gives ``block`` when it has (or lacks) a supported citation.

    A block that is already ``derived`` and has ``depends_on`` edges was built from other
    blocks (invariant 4) -- an activity drafted from a textbook instruction, a summary --
    and stays ``derived`` whether or not its citation is supported: the citation says
    where it came from, the dependencies say what it was built from.
    """
    if block.kind in NON_CONTENT_KINDS:
        return block.provenance
    if block.provenance is Provenance.DERIVED and block.depends_on:
        return Provenance.DERIVED
    if supported:
        return Provenance.EXTRACTED
    return Provenance.GENERATED


def assign_provenance(lesson: Lesson, verdicts: Iterable[CitationVerdict]) -> Lesson:
    """A copy of the lesson with every content block's provenance set from the verdicts."""
    supported_ids = {verdict.block_id for verdict in verdicts if verdict.supported}
    result = lesson.model_copy(deep=True)
    for block in blocks_to_ground(result):
        block.provenance = provenance_for(block, block.id in supported_ids)
    return result
