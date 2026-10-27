"""Flashcard pictures: draw the vocabulary items that have no textbook picture.

``illustrate`` picks the items, builds a fixed-style prompt for each, asks an
:class:`~classdeck.llm.images.ImageProvider` for a picture, downscales it and records the file in
``VocabItem.generated_picture``. Files are named by prompt hash, so a re-run is free. See
``docs/output-formats.md`` ("Flashcard pictures").
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict

from classdeck.lesson import Lesson, VocabItem, VocabularyBlock
from classdeck.llm.images import ImageProvider
from classdeck.llm.pricing import estimate_image_cost_usd

STYLE_GUIDE = (
    "Flat, friendly children's-textbook illustration. A single clear subject for the word, "
    "plain white background, bright pastel colours, simple shapes. "
    "No text, no letters, no logo."
)

# Parts of speech that can be drawn; a missing `pos` is treated as content.
CONTENT_POS = {"noun", "verb", "adjective", "interjection", "adverb"}

MAX_SIDE = 512


class IllustrateReport(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    lesson: Lesson
    generated: int = 0
    reused: int = 0
    skipped: int = 0
    # Pictures that still have to be generated (what a dry run would pay for).
    planned: int = 0
    words: list[str] = []
    prompts: list[str] = []
    cost_usd: float | None = None


def _is_content(item: VocabItem) -> bool:
    pos = (item.pos or "").strip().lower()
    return not pos or pos in CONTENT_POS


def candidates(lesson: Lesson, *, all_items: bool = False) -> list[tuple[str, int, VocabItem]]:
    """``(block id, item index, item)`` for items with neither a cited nor a generated picture."""
    ordered = [block.id for block in lesson.ordered_blocks()]
    ids = [*ordered, *(key for key in lesson.blocks if key not in set(ordered))]
    found: list[tuple[str, int, VocabItem]] = []
    for block_id in ids:
        block = lesson.blocks[block_id]
        if not isinstance(block, VocabularyBlock):
            continue
        for index, item in enumerate(block.content.items):
            if item.image is not None or item.generated_picture:
                continue
            if all_items or _is_content(item):
                found.append((block_id, index, item))
    return found


def build_prompt(item: VocabItem, lesson: Lesson) -> str:
    """The style guide plus what the model needs to pick the right sense of the word."""
    lines = [STYLE_GUIDE, f"word: {item.word}"]
    if item.meaning_vi:
        lines.append(f"meaning: {item.meaning_vi}")
    if item.example:
        lines.append(f"example: {item.example}")
    return "\n".join(lines)


def picture_path(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return f"generated/{digest}.png"


def downscale_png(png: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Re-encode ``png`` as PNG with the long side at most ``max_side`` pixels."""
    pixmap = pymupdf.Pixmap(png)
    longest = max(pixmap.width, pixmap.height)
    if longest > max_side:
        scale = max_side / longest
        width = max(1, round(pixmap.width * scale))
        height = max(1, round(pixmap.height * scale))
        pixmap = pymupdf.Pixmap(pixmap, width, height)
    return pixmap.tobytes("png")


def illustrate(
    lesson: Lesson,
    *,
    provider: ImageProvider,
    image_root: Path,
    max_items: int = 8,
    all_items: bool = False,
    dry_run: bool = False,
) -> IllustrateReport:
    """Draw up to ``max_items`` new pictures; the input lesson is left untouched."""
    updated = lesson.model_copy(deep=True)
    root = Path(image_root)
    report = IllustrateReport(lesson=updated)
    for block_id, index, item in candidates(updated, all_items=all_items):
        prompt = build_prompt(item, updated)
        relative = picture_path(prompt)
        target = root / relative
        if target.is_file():
            report.reused += 1
        elif report.planned < max_items:
            report.planned += 1
            report.words.append(item.word)
            report.prompts.append(prompt)
            if dry_run:
                continue
            result = provider.generate(prompt)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(downscale_png(result.png))
            report.generated += 1
        else:
            report.skipped += 1
            continue
        block = updated.blocks[block_id]
        assert isinstance(block, VocabularyBlock)
        block.content.items[index].generated_picture = relative
    report.cost_usd = estimate_image_cost_usd(
        provider.model, report.planned if dry_run else report.generated
    )
    return report
