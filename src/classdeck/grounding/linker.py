"""Resolve citations to spans and read the block text that verification compares them with.

A citation is ``(doc_id, page, span_id)``; the span is the evidence. See
``docs/source-grounding.md`` sections 1 and 3.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from classdeck.lesson.exercises import page_strings
from classdeck.lesson.schema import (
    EXERCISE_KINDS,
    ActivityBlock,
    Block,
    Citation,
    DialogueBlock,
    ImageRefBlock,
    Lesson,
    PhonicsBlock,
    SentencePatternBlock,
    SourceDocument,
    Span,
    SummaryBlock,
    VocabularyBlock,
)

Sources = Mapping[str, SourceDocument]
"""Source documents keyed by ``doc_id``."""

QUOTE_LENGTH = 80
"""Characters of span text kept in ``Citation.quote``."""


def resolve_citation(citation: Citation, sources: Sources) -> Span | None:
    """The span a citation points at, or ``None`` when the document or the span is unknown."""
    document = sources.get(citation.doc_id)
    if document is None:
        return None
    return document.find_span(citation.page, citation.span_id)


def _join(parts: Iterable[str | None]) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


def comparable_text(block: Block) -> str:
    """The content of a block as one string, the way verification compares it with span text.

    Only what restates the page is included: teacher steps, materials, Vietnamese glosses and
    notes are left out. Objectives and teacher notes have nothing to compare and give ``""``.
    """
    match block:
        case VocabularyBlock():
            items = block.content.items
            return _join([*(item.word for item in items), *(item.example for item in items)])
        case SentencePatternBlock():
            return _join([block.content.pattern, *block.content.examples])
        case DialogueBlock():
            return _join(line.text for line in block.content.lines)
        case PhonicsBlock():
            content = block.content
            return _join([content.sound, *content.words, *content.chant_lines])
        case ActivityBlock():
            return _join([block.content.title, block.content.instructions])
        case SummaryBlock():
            return _join(block.content.bullets)
        case ImageRefBlock():
            return _join([block.content.caption])
        case _ if block.kind in EXERCISE_KINDS:
            return _join(page_strings(block))
        case _:
            return ""


def steps_text(block: Block) -> str:
    """An activity's pupil steps as one string, the second text a cited span may support.

    A song, chant, tick or gap-fill activity copies the textbook's lines into its steps, so the
    steps (not the title and instructions) are what the cited span restates. Other blocks give
    ``""``.
    """
    if isinstance(block, ActivityBlock):
        return _join(block.content.student_steps)
    return ""


def quote_of(span: Span) -> str:
    """The first :data:`QUOTE_LENGTH` characters of the span text, whitespace collapsed."""
    return " ".join(span.text.split())[:QUOTE_LENGTH]


def fill_quotes(lesson: Lesson, sources: Sources) -> Lesson:
    """A copy of the lesson with every missing ``Citation.quote`` filled from its span.

    Existing quotes are kept. Citations that do not resolve, or whose span has no text
    (images), are left as they are.
    """
    result = lesson.model_copy(deep=True)
    for block in result.blocks.values():
        for citation in block.sources:
            if citation.quote:
                continue
            span = resolve_citation(citation, sources)
            if span is None:
                continue
            quote = quote_of(span)
            if quote:
                citation.quote = quote
    return result
