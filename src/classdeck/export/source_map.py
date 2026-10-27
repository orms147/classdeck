"""What both export writers share: labels, citation text, the source map and the export gate.

A teacher reading a slide or a printed plan must be able to tell where each block came from and
what the model added on its own (``docs/source-grounding.md`` section 4). The writers therefore
use one set of labels and one source map. The gate that refuses to export a lesson with failed
checks (``docs/architecture.md`` section 6) lives here too.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from classdeck.lesson import (
    Block,
    BlockKind,
    CheckResult,
    CheckStatus,
    Citation,
    Lesson,
    Provenance,
    SourceDocument,
    Strict,
)

AI_ADDED_BADGE = "AI-added"
AI_ADDED_NOTE = "AI-added — check before teaching"
AI_PICTURE_CAPTION = "AI picture"
PROVENANCE_LABEL: dict[Provenance, str] = {
    Provenance.EXTRACTED: "Source",
    Provenance.DERIVED: "Derived",
    Provenance.GENERATED: AI_ADDED_BADGE,
}

QUOTE_LIMIT = 80
"""Characters of span text used as the quote when a citation carries none."""

_SPAN_ID = re.compile(r"p\d+\.s(?P<paragraph>\d+)")


def format_citation(citation: Citation) -> str:
    """``p. 6, ¶3`` for span ``p6.s3`` on page 6; unknown span id shapes are shown verbatim."""
    match = _SPAN_ID.fullmatch(citation.span_id)
    if match is None:
        return f"p. {citation.page}, {citation.span_id}"
    return f"p. {citation.page}, ¶{match['paragraph']}"


# --- source map --------------------------------------------------------------------------------


class SourceMapRow(Strict):
    """One block of the lesson with its provenance and, where it has them, citations."""

    block_id: str
    kind: BlockKind
    provenance: Provenance
    citations: list[str]
    quote: str | None = None
    depends_on: list[str] = []


def build_source_map(
    lesson: Lesson, sources: Mapping[str, SourceDocument] | None = None
) -> list[SourceMapRow]:
    """One row per placed block, in lesson order.

    The quote is the first citation's own quote; when a citation has none and ``sources`` is
    given, it is the start of the cited span's text.
    """
    return [_row(block, sources) for block in lesson.ordered_blocks()]


def origin_text(row: SourceMapRow) -> str:
    """The citation column of the source map: citations, else what the block derives from."""
    if row.citations:
        return ", ".join(row.citations)
    if row.provenance is Provenance.DERIVED and row.depends_on:
        return "Derived from " + ", ".join(row.depends_on)
    return "—"


def _row(block: Block, sources: Mapping[str, SourceDocument] | None) -> SourceMapRow:
    quote: str | None = None
    for citation in block.sources:
        quote = citation.quote or _span_excerpt(citation, sources)
        if quote:
            break
    return SourceMapRow(
        block_id=block.id,
        kind=BlockKind(block.kind),
        provenance=block.provenance,
        citations=[format_citation(citation) for citation in block.sources],
        quote=quote or None,
        depends_on=list(block.depends_on),
    )


def _span_excerpt(citation: Citation, sources: Mapping[str, SourceDocument] | None) -> str | None:
    document = sources.get(citation.doc_id) if sources else None
    span = document.find_span(citation.page, citation.span_id) if document else None
    if span is None:
        return None
    return " ".join(span.text.split())[:QUOTE_LIMIT] or None


# --- the export gate ---------------------------------------------------------------------------

CheckEntry = tuple[str | None, CheckResult]
"""A check result and the id of the block it belongs to (``None`` for lesson-level checks)."""


def format_check(block_id: str | None, check: CheckResult) -> str:
    """``b_07 — english.steps_present: detail``; lesson-level checks are labelled ``Lesson``."""
    text = f"{block_id or 'Lesson'} — {check.name}"
    return f"{text}: {check.detail}" if check.detail else text


class ExportBlocked(ValueError):
    """The lesson has failed checks, so no file is written."""

    def __init__(self, failures: list[CheckEntry]) -> None:
        self.failures = failures
        listed = "; ".join(format_check(block_id, check) for block_id, check in failures)
        super().__init__(f"export blocked by {len(failures)} failed check(s): {listed}")


def collect_failures(lesson: Lesson) -> list[CheckEntry]:
    return _collect(lesson, CheckStatus.FAIL)


def collect_warnings(lesson: Lesson) -> list[CheckEntry]:
    return _collect(lesson, CheckStatus.WARN)


def ensure_exportable(lesson: Lesson) -> None:
    """Raise :class:`ExportBlocked` if any check failed; warnings do not block."""
    failures = collect_failures(lesson)
    if failures:
        raise ExportBlocked(failures)


def _collect(lesson: Lesson, status: CheckStatus) -> list[CheckEntry]:
    found: list[CheckEntry] = [
        (block.id, check)
        for block in lesson.ordered_blocks()
        for check in block.checks
        if check.status is status
    ]
    found.extend((None, check) for check in lesson.checks if check.status is status)
    return found
