"""Cut a source document into textbook activities, with a BM25 fallback for unstructured notes.

An *instruction line* is a ``list_item`` span such as ``1. Look, listen and repeat.``; the
spans from one instruction up to the next form the chunk of that activity. Headings and spans
before a page's first instruction belong to the first chunk of that page (spans of pages
before the document's first instruction join the first chunk too); a page with no instruction
line continues the chunk open from the page before. See ``docs/planning.md``, section 1a.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence

from rank_bm25 import BM25Okapi

from classdeck.lesson.schema import SourceDocument, Span, SpanKind, Strict
from classdeck.planning.models import SpanRef

INSTRUCTION_RE = re.compile(r"^\s*(\d{1,2})[.)]\s")
"""Matches an activity instruction line and captures the activity number."""

_TOKEN = re.compile(r"\w+")


class Chunk(Strict):
    """The spans of one textbook activity, in reading order."""

    number: int
    doc_id: str
    instruction: SpanRef | None = None
    span_refs: list[SpanRef] = []
    text: str = ""


def by_doc_id(sources: Iterable[SourceDocument]) -> dict[str, SourceDocument]:
    return {document.doc_id: document for document in sources}


def instruction_number(span: Span) -> int | None:
    """The activity number of an instruction line, or ``None`` for any other span."""
    if span.kind is not SpanKind.LIST_ITEM:
        return None
    match = INSTRUCTION_RE.match(span.text)
    return int(match.group(1)) if match else None


def resolve_ref(sources: Mapping[str, SourceDocument], ref: SpanRef) -> Span | None:
    document = sources.get(ref.doc_id)
    return document.find_span(ref.page, ref.span_id) if document is not None else None


def span_text(doc: SourceDocument, ref: SpanRef) -> str:
    """The text of the span ``ref`` points at in ``doc``; ``""`` when it does not resolve."""
    span = doc.find_span(ref.page, ref.span_id)
    return span.text if span is not None else ""


def is_qualified(refs: Iterable[SpanRef]) -> bool:
    """Whether ``refs`` span more than one document, so bare span ids would be ambiguous."""
    return len({ref.doc_id for ref in refs}) > 1


def span_token(ref: SpanRef, *, qualified: bool = False) -> str:
    """How a prompt names a span: ``p1.s3``, or ``d_8f2a1c:p1.s3`` when ``qualified``."""
    return f"{ref.doc_id}:{ref.span_id}" if qualified else ref.span_id


def match_ref(refs: Iterable[SpanRef], token: str) -> SpanRef | None:
    """The first ref that ``token`` (``p1.s3`` or ``d_8f2a1c:p1.s3``) names, if any."""
    doc_id, _, span_id = token.strip().rpartition(":")
    for ref in refs:
        if ref.span_id == span_id and (not doc_id or ref.doc_id == doc_id):
            return ref
    return None


def label_span(ref: SpanRef, span: Span, *, qualified: bool = False) -> str:
    """A prompt line ``[p1.s3] text``; an image span reads ``[p1.s3] (image)``."""
    text = " ".join(span.text.split())
    if span.kind is SpanKind.IMAGE:
        text = "(image)"
    return f"[{span_token(ref, qualified=qualified)}] {text}"


def span_lines(
    sources: Mapping[str, SourceDocument], refs: Iterable[SpanRef], *, qualified: bool = False
) -> list[str]:
    """:func:`label_span` for every ref that resolves to a span with text or an image."""
    lines: list[str] = []
    for ref in refs:
        span = resolve_ref(sources, ref)
        if span is None or (span.kind is not SpanKind.IMAGE and not span.text.strip()):
            continue
        lines.append(label_span(ref, span, qualified=qualified))
    return lines


# --- segmentation ------------------------------------------------------------------------------


class _ChunkBuilder:
    def __init__(self, doc_id: str, number: int, page: int, instruction: Span) -> None:
        self.doc_id = doc_id
        self.number = number
        self.instruction = SpanRef(doc_id=doc_id, page=page, span_id=instruction.span_id)
        self._spans: list[tuple[int, Span]] = [(page, instruction)]

    def prepend(self, page: int, spans: Iterable[Span]) -> None:
        self._spans[:0] = [(page, span) for span in spans]

    def prepend_pairs(self, pairs: Iterable[tuple[int, Span]]) -> None:
        self._spans[:0] = list(pairs)

    def extend(self, page: int, spans: Iterable[Span]) -> None:
        self._spans.extend((page, span) for span in spans)

    def build(self) -> Chunk:
        return Chunk(
            number=self.number,
            doc_id=self.doc_id,
            instruction=self.instruction,
            span_refs=[
                SpanRef(doc_id=self.doc_id, page=page, span_id=span.span_id)
                for page, span in self._spans
            ],
            text="\n".join(span.text for _, span in self._spans if span.text.strip()),
        )


def segment_document(doc: SourceDocument) -> list[Chunk]:
    """Chunks of ``doc`` in reading order, one per instruction line; ``[]`` when there is none.

    ``Chunk.number`` is the number written on the instruction line, so a document whose page
    starts at activity 4 yields chunks 4, 5, 6. Spans on a page with no instruction line join
    the chunk open from the previous page; those on pages before the first instruction line
    join the first chunk.
    """
    builders: list[_ChunkBuilder] = []
    current: _ChunkBuilder | None = None
    carried: list[tuple[int, Span]] = []
    for page in doc.pages:
        leading: list[Span] = []
        opened_on_page = False
        for span in page.spans:
            number = instruction_number(span)
            if number is None:
                if current is not None and opened_on_page:
                    current.extend(page.number, [span])
                else:
                    leading.append(span)
                continue
            first = current is None
            current = _ChunkBuilder(doc.doc_id, number, page.number, span)
            builders.append(current)
            if not opened_on_page:
                current.prepend(page.number, leading)
                if first:
                    current.prepend_pairs(carried)
                    carried = []
                leading = []
                opened_on_page = True
        if leading:
            if current is not None:
                current.extend(page.number, leading)
            else:
                carried.extend((page.number, span) for span in leading)
    return [builder.build() for builder in builders]


def lesson_runs(chunks: Sequence[Chunk]) -> list[list[Chunk]]:
    """Split one document's chunks into runs, a new run starting where the numbering restarts.

    A unit document that holds several lessons numbers each lesson's activities from 1 again,
    so every run is one lesson's activities.
    """
    runs: list[list[Chunk]] = []
    previous = 0
    for chunk in chunks:
        if not runs or (chunk.number == 1 and previous > 1):
            runs.append([])
        runs[-1].append(chunk)
        previous = chunk.number
    return runs


# --- retrieval fallback ------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def bm25_rank_all(documents: Sequence[SourceDocument], query: str, k: int = 6) -> list[SpanRef]:
    """The ``k`` text spans across ``documents`` that best match ``query`` under BM25.

    All documents are ranked together, so the result is the top ``k`` of the whole set, not
    ``k`` per document. Image spans are skipped, as is any span that shares no token with the
    query, so the result may be shorter than ``k``. Ties keep reading order.
    """
    candidates: list[tuple[SpanRef, list[str]]] = []
    for doc in documents:
        for page in doc.pages:
            for span in page.spans:
                if span.kind is SpanKind.IMAGE:
                    continue
                tokens = _tokens(span.text)
                if tokens:
                    ref = SpanRef(doc_id=doc.doc_id, page=page.number, span_id=span.span_id)
                    candidates.append((ref, tokens))
    query_tokens = set(_tokens(query))
    if not candidates or not query_tokens or k <= 0:
        return []
    scores = BM25Okapi([tokens for _, tokens in candidates]).get_scores(list(query_tokens))
    ranked = sorted(
        (index for index, (_, tokens) in enumerate(candidates) if query_tokens & set(tokens)),
        key=lambda index: (-float(scores[index]), index),
    )
    return [candidates[index][0] for index in ranked[:k]]


def bm25_rank(doc: SourceDocument, query: str, k: int = 6) -> list[SpanRef]:
    """The ``k`` text spans of ``doc`` that best match ``query`` under BM25, best first."""
    return bm25_rank_all([doc], query, k)
