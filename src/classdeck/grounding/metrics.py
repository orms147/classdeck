"""Grounding numbers for one lesson (``docs/source-grounding.md`` section 5).

Both ratios run over the content blocks grounding verifies (:func:`blocks_to_ground`, which
includes blocks outside every section): objectives, teacher notes and summaries are expected to
be derived or generated and are not counted, and neither is any other ``derived`` block that
cites nothing (the wrap-up's recap activity is built from the summary; there is no span to cite).
They are reported side by side because low grounding is not a failure when the teacher asked for
extra material.
"""

from __future__ import annotations

from classdeck.grounding.provenance import blocks_to_ground
from classdeck.grounding.verifier import CITATION_SUPPORTED
from classdeck.lesson.schema import Block, CheckStatus, Lesson, Provenance


def counted_blocks(lesson: Lesson) -> list[Block]:
    """The content blocks the ratios run over: derived blocks that cite nothing are left out."""
    return [
        block
        for block in blocks_to_ground(lesson)
        if not (block.provenance is Provenance.DERIVED and not block.sources)
    ]


def _grounding_status(block: Block) -> CheckStatus | None:
    for check in block.checks:
        if check.name == CITATION_SUPPORTED:
            return check.status
    return None


def is_grounded(block: Block) -> bool:
    """Whether the block's ``grounding.citation_supported`` check passed."""
    return _grounding_status(block) is CheckStatus.PASS


def grounded_ratio(lesson: Lesson) -> float:
    """Content blocks with at least one supported citation over all content blocks."""
    blocks = counted_blocks(lesson)
    if not blocks:
        return 0.0
    return sum(is_grounded(block) for block in blocks) / len(blocks)


def ai_added_ratio(lesson: Lesson) -> float:
    """Content blocks with ``provenance = generated`` over all content blocks."""
    blocks = counted_blocks(lesson)
    if not blocks:
        return 0.0
    return sum(block.provenance is Provenance.GENERATED for block in blocks) / len(blocks)


def grounding_summary(lesson: Lesson) -> dict[str, int]:
    """Counts behind the ratios: content blocks, grounded, generated, dangling.

    ``dangling`` counts blocks whose grounding check failed: nothing supported them and at
    least one citation did not resolve.
    """
    blocks = counted_blocks(lesson)
    return {
        "content_blocks": len(blocks),
        "grounded": sum(is_grounded(block) for block in blocks),
        "generated": sum(block.provenance is Provenance.GENERATED for block in blocks),
        "dangling": sum(_grounding_status(block) is CheckStatus.FAIL for block in blocks),
    }
