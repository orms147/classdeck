"""Segmentation of source documents into activity chunks, and the BM25 fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from classdeck.ingest import ingest
from classdeck.lesson import Page, SourceDocument, Span, SpanKind
from classdeck.planning import (
    INSTRUCTION_RE,
    Chunk,
    SpanRef,
    bm25_rank,
    bm25_rank_all,
    instruction_number,
    is_qualified,
    lesson_runs,
    match_ref,
    segment_document,
    span_lines,
    span_text,
)

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "public"


@pytest.fixture(scope="module")
def document() -> SourceDocument:
    return ingest(FIXTURES / "mini-unit-hello.pdf")


def make_document(pages: list[list[tuple[str, str]]], doc_id: str = "d_notes") -> SourceDocument:
    """A document from ``[(kind, text), ...]`` per page; image spans have empty text."""
    return SourceDocument(
        doc_id=doc_id,
        filename="notes.pdf",
        sha256="0" * 64,
        media_type="application/pdf",
        pages=[
            Page(
                number=number,
                spans=[
                    Span(span_id=f"p{number}.s{order}", kind=kind, text=text, order=order)
                    for order, (kind, text) in enumerate(spans, start=1)
                ],
            )
            for number, spans in enumerate(pages, start=1)
        ],
    )


def texts_of(document: SourceDocument, chunk: Chunk) -> list[str]:
    return [span_text(document, ref) for ref in chunk.span_refs]


# --- segmentation ------------------------------------------------------------------------------


def test_fixture_yields_six_numbered_chunks(document: SourceDocument) -> None:
    chunks = segment_document(document)
    assert [chunk.number for chunk in chunks] == [1, 2, 3, 4, 5, 6]
    assert {chunk.doc_id for chunk in chunks} == {document.doc_id}
    assert all(chunk.instruction is not None for chunk in chunks)


def test_first_chunk_takes_the_heading_and_the_dialogue(document: SourceDocument) -> None:
    first = segment_document(document)[0]
    assert first.instruction == SpanRef(doc_id=document.doc_id, page=1, span_id="p1.s2")
    assert [ref.span_id for ref in first.span_refs] == ["p1.s1", "p1.s2", "p1.s3", "p1.s4", "p1.s5"]
    texts = texts_of(document, first)
    assert texts[0] == "Unit 1 Hello"
    assert "Hello, I'm Ha." in texts
    assert "Hello, I'm ___." not in texts
    assert first.text.splitlines() == [
        "Unit 1 Hello",
        "1. Look, listen and repeat.",
        "Hello, I'm Ha.",
        "Hi, Ha. I'm Tien.",
    ]


def test_chunk_four_is_on_page_two_with_its_heading(document: SourceDocument) -> None:
    fourth = segment_document(document)[3]
    assert fourth.number == 4
    assert {ref.page for ref in fourth.span_refs} == {2}
    assert texts_of(document, fourth)[:2] == [
        "Unit 1 Hello (Lesson 1, Period 2)",
        "4. Listen and tick.",
    ]


def test_every_span_belongs_to_exactly_one_chunk(document: SourceDocument) -> None:
    seen = [ref.span_id for chunk in segment_document(document) for ref in chunk.span_refs]
    assert seen == [span.span_id for page in document.pages for span in page.spans]


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("1. Look", 1),
        ("12) Listen", 12),
        ("  3. Talk", 3),
        ("1.Look", None),
        ("123. x", None),
        ("a. Hello", None),
        ("Hello, I'm Ha.", None),
    ],
)
def test_instruction_regex(text: str, number: int | None) -> None:
    match = INSTRUCTION_RE.match(text)
    assert (int(match.group(1)) if match else None) == number
    span = Span(span_id="p1.s1", kind=SpanKind.LIST_ITEM, text=text, order=1)
    assert instruction_number(span) == number


def test_only_list_items_are_instructions() -> None:
    span = Span(span_id="p1.s1", kind=SpanKind.PARAGRAPH, text="1. Look, listen.", order=1)
    assert instruction_number(span) is None


def test_page_without_instruction_continues_the_open_chunk() -> None:
    document = make_document(
        [
            [("list_item", "1. Look."), ("paragraph", "Hello.")],
            [("heading", "Notes"), ("paragraph", "More.")],
        ]
    )
    chunks = segment_document(document)
    assert len(chunks) == 1
    assert [ref.span_id for ref in chunks[0].span_refs] == ["p1.s1", "p1.s2", "p2.s1", "p2.s2"]
    assert [ref.page for ref in chunks[0].span_refs] == [1, 1, 2, 2]


def test_document_without_instructions_has_no_chunks() -> None:
    document = make_document([[("heading", "Notes"), ("paragraph", "Hello, I'm Ha.")]])
    assert segment_document(document) == []


def test_chunk_numbers_come_from_the_page() -> None:
    document = make_document([[("list_item", "4. Listen and tick."), ("paragraph", "a. Hello")]])
    chunks = segment_document(document)
    assert [chunk.number for chunk in chunks] == [4]
    assert chunks[0].text == "4. Listen and tick.\na. Hello"


# --- retrieval fallback ------------------------------------------------------------------------


def test_bm25_finds_the_dialogue_span(document: SourceDocument) -> None:
    refs = bm25_rank(document, "hello ha")
    assert refs[0] == SpanRef(doc_id=document.doc_id, page=1, span_id="p1.s4")
    assert len(refs) <= 6
    for ref in refs:
        span = document.find_span(ref.page, ref.span_id)
        assert span is not None
        assert span.kind is not SpanKind.IMAGE


def test_bm25_limits_and_filters(document: SourceDocument) -> None:
    assert bm25_rank(document, "zebra") == []
    assert bm25_rank(document, "") == []
    assert len(bm25_rank(document, "hello", k=2)) == 2
    assert bm25_rank(document, "hello", k=0) == []


def test_bm25_on_a_single_text_span() -> None:
    document = make_document([[("paragraph", "Hello, I'm Ha."), ("image", "")]])
    assert [ref.span_id for ref in bm25_rank(document, "hello")] == ["p1.s1"]
    assert bm25_rank(make_document([[("image", "")]]), "hello") == []


# --- span helpers ------------------------------------------------------------------------------


def test_span_text_and_lines(document: SourceDocument) -> None:
    ref = SpanRef(doc_id=document.doc_id, page=1, span_id="p1.s4")
    assert span_text(document, ref) == "Hello, I'm Ha."
    assert span_text(document, SpanRef(doc_id=document.doc_id, page=9, span_id="p9.s9")) == ""
    lines = span_lines(
        {document.doc_id: document},
        [
            SpanRef(doc_id=document.doc_id, page=1, span_id="p1.s3"),
            ref,
            SpanRef(doc_id="d_other", page=1, span_id="p1.s1"),
        ],
    )
    assert lines == ["[p1.s3] (image)", "[p1.s4] Hello, I'm Ha."]


def test_spans_before_the_first_instruction_join_the_first_chunk() -> None:
    document = make_document(
        [
            [("heading", "English 3"), ("paragraph", "Teacher's note.")],
            [("list_item", "1. Look and say."), ("paragraph", "Hello.")],
        ]
    )
    chunks = segment_document(document)
    assert len(chunks) == 1
    assert [(ref.page, ref.span_id) for ref in chunks[0].span_refs] == [
        (1, "p1.s1"),
        (1, "p1.s2"),
        (2, "p2.s1"),
        (2, "p2.s2"),
    ]


def test_lesson_runs_split_where_numbering_restarts() -> None:
    document = make_document(
        [
            [("list_item", "1. A."), ("list_item", "2. B.")],
            [("list_item", "1. C."), ("list_item", "2. D."), ("list_item", "3. E.")],
        ]
    )
    runs = lesson_runs(segment_document(document))
    assert [[chunk.number for chunk in run] for run in runs] == [[1, 2], [1, 2, 3]]
    assert lesson_runs([]) == []


def test_bm25_rank_all_ranks_across_documents() -> None:
    first = make_document([[("paragraph", "Hello there."), ("paragraph", "Other words.")]], "d_a")
    second = make_document([[("paragraph", "Hello, hello, hello Ha.")]], "d_b")
    refs = bm25_rank_all([first, second], "hello", k=2)
    assert [(ref.doc_id, ref.span_id) for ref in refs] == [("d_b", "p1.s1"), ("d_a", "p1.s1")]
    assert len(bm25_rank_all([first, second], "hello", k=1)) == 1
    assert bm25_rank_all([], "hello") == []


def test_qualified_labels_and_matching() -> None:
    a = SpanRef(doc_id="d_a", page=1, span_id="p1.s1")
    b = SpanRef(doc_id="d_b", page=1, span_id="p1.s1")
    assert is_qualified([a, b])
    assert not is_qualified([a, a])
    assert match_ref([a, b], "p1.s1") == a
    assert match_ref([a, b], "d_b:p1.s1") == b
    assert match_ref([a, b], "d_c:p1.s1") is None
    assert match_ref([a, b], "p9.s9") is None
    one = make_document([[("paragraph", "Hello.")]], "d_a")
    two = make_document([[("paragraph", "Bye.")]], "d_b")
    lines = span_lines({"d_a": one, "d_b": two}, [a, b], qualified=True)
    assert lines == ["[d_a:p1.s1] Hello.", "[d_b:p1.s1] Bye."]
