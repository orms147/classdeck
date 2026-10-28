"""Scanned pages: detection, reading by a (mock) vision model, span mapping, caching, fallbacks."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import pymupdf
import pytest

from classdeck.ingest import ScanReport, ingest, is_scanned_page, read_page
from classdeck.ingest.vision import PageLayout
from classdeck.lesson import SourceDocument, Span, SpanKind
from classdeck.llm import CachingProvider, MockProvider

FIXTURE = Path(__file__).resolve().parent.parent / "eval" / "fixtures" / "public"
PAGE_W, PAGE_H = 400, 600
RED = (220, 30, 30)

LAYOUT = {
    "blocks": [
        {"kind": "section", "text": "GETTING STARTED", "bbox": [0.05, 0.02, 0.60, 0.07]},
        {"kind": "heading", "text": "A visit to a craft village", "bbox": [0.05, 0.08, 0.9, 0.12]},
        {"kind": "instruction", "text": "1 Listen and read.", "bbox": [0.05, 0.13, 0.5, 0.16]},
        {
            "kind": "picture",
            "text": "Two girls talking in a garden",
            "bbox": [0.5, 0.5, 0.9, 0.7],
        },
        {
            "kind": "speaker_line",
            "speaker": "Ann",
            "text": "Hi, Mi. Long time no see.",
            "bbox": [0.05, 0.18, 0.48, 0.24],
            "column": 1,
        },
        {
            "kind": "speaker_line",
            "speaker": "Mi",
            "text": "Mi: Hi, Ann!",
            "bbox": [0.05, 0.25, 0.48, 0.30],
            "column": 1,
        },
        {"kind": "paragraph", "text": "Right column\ncomes after.", "column": 2},
        {"kind": "instruction", "text": "2 Read it again.", "bbox": [0.05, 0.75, 0.9, 0.78]},
        {"kind": "table_row", "text": "1. a. pottery | art of making pots"},
        {"kind": "table_row", "text": "2. b. weave | make cloth"},
        {"kind": "caption", "text": "A potter at work", "bbox": [0.5, 0.71, 0.9, 0.74]},
        {"kind": "list_item", "text": "3 Make notes", "bbox": [0.05, 0.9, 0.9, 0.93]},
    ]
}


def scanned_page_pdf(path: Path, pages: int = 1) -> Path:
    """A PDF whose pages are one full-page raster picture each, with a red patch on it."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, PAGE_W, PAGE_H), False)
    pix.set_rect(pix.irect, (250, 250, 250))
    # The patch lies where the "picture" block of LAYOUT points: x 0.5-0.9, y 0.5-0.7.
    pix.set_rect(pymupdf.IRect(200, 300, 360, 420), RED)
    blob = pix.tobytes("png")
    with pymupdf.open() as document:
        for _ in range(pages):
            page = document.new_page(width=PAGE_W, height=PAGE_H)
            page.insert_image(page.rect, stream=blob)
        document.save(path)
    return path


def reply(layout: dict | None = None) -> str:
    return json.dumps(LAYOUT if layout is None else layout)


def spans_of(document: SourceDocument, page: int = 1) -> list[Span]:
    return next(p for p in document.pages if p.number == page).spans


@pytest.fixture
def scan(tmp_path: Path) -> Path:
    return scanned_page_pdf(tmp_path / "scan.pdf")


# --- detection ---------------------------------------------------------------------------------


def test_scanned_page_is_detected(scan: Path) -> None:
    with pymupdf.open(scan) as document:
        assert is_scanned_page(document[0])


def test_text_pages_are_not_scanned() -> None:
    with pymupdf.open(FIXTURE / "mini-unit-hello.pdf") as document:
        assert not any(is_scanned_page(page) for page in document)


def test_full_page_picture_with_text_is_not_scanned(tmp_path: Path) -> None:
    path = tmp_path / "poster.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=PAGE_W, height=PAGE_H)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), False)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        page.insert_text((50, 50), "A text layer")
        document.save(path)
    with pymupdf.open(path) as document:
        assert not is_scanned_page(document[0])


def test_small_picture_without_text_is_not_scanned(tmp_path: Path) -> None:
    path = tmp_path / "thumb.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=PAGE_W, height=PAGE_H)
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 60), False)
        page.insert_image(pymupdf.Rect(10, 10, 200, 300), stream=pix.tobytes("png"))
        document.save(path)
    with pymupdf.open(path) as document:
        assert not is_scanned_page(document[0])


# --- reading a page ----------------------------------------------------------------------------


def test_read_page_sends_the_image_and_parses_the_layout() -> None:
    provider = MockProvider([reply()])
    layout = read_page(b"\x89PNG-bytes", provider, page_number=7)

    assert isinstance(layout, PageLayout)
    assert [block.kind for block in layout.blocks][:3] == ["section", "heading", "instruction"]
    (call,) = provider.calls
    content = call["messages"][0]["content"]
    image = next(part for part in content if part["type"] == "image_url")["image_url"]["url"]
    assert image == "data:image/png;base64," + base64.b64encode(b"\x89PNG-bytes").decode()
    text = " ".join(part["text"] for part in content if part["type"] == "text")
    assert "page 7" in text and "reading order" in text.lower()
    # the shape is in the prompt; constrained decoding garbles long replies on the Meta endpoint
    assert call["temperature"] == 0.0 and call["json_schema"] is None


def test_layout_accepts_keys_that_echo_the_prompt() -> None:
    layout = PageLayout.model_validate_json(
        '{"blocks": [{"kind": "paragraph", "text": "a", '
        '"bbox [x0, y0, x1, y1]": [0.1, 0.1, 0.5, 0.2], "column [1 or 2]": 1}]}'
    )
    assert layout.blocks[0].bbox == [0.1, 0.1, 0.5, 0.2] and layout.blocks[0].column == 1


def test_layout_is_lenient_about_boxes_and_kinds() -> None:
    layout = PageLayout.model_validate(
        {
            "blocks": [
                {"kind": "Title", "text": "Unit 1", "bbox": [50, 20, 950, 80]},
                {"kind": "paragraph", "text": "x", "bbox": [0.5, 0.5, 0.2, 0.9]},
                {"kind": "paragraph", "text": None, "bbox": [-0.1, 0, 1.2, 0.5]},
                {"kind": "paragraph", "text": "y", "bbox": [0.1, 0.2]},
            ]
        }
    )
    first, inverted, clamped, short = layout.blocks
    assert first.kind == "heading" and first.bbox == [0.05, 0.02, 0.95, 0.08]
    assert inverted.bbox is None and short.bbox is None
    assert clamped.text == "" and clamped.bbox == [0.0, 0.0, 1.0, 0.5]


# --- spans -------------------------------------------------------------------------------------


def test_scanned_page_becomes_spans_in_reading_order(scan: Path, tmp_path: Path) -> None:
    provider = MockProvider([reply()])
    document = ingest(scan, image_dir=tmp_path / "images", provider=provider)

    spans = spans_of(document)
    assert [(span.span_id, span.kind.value, span.text) for span in spans] == [
        ("p1.s1", "heading", "GETTING STARTED"),
        ("p1.s2", "heading", "A visit to a craft village"),
        ("p1.s3", "instruction", "1. Listen and read."),
        ("p1.s4", "image", "Two girls talking in a garden"),
        ("p1.s5", "paragraph", "Ann: Hi, Mi. Long time no see."),
        ("p1.s6", "paragraph", "Mi: Hi, Ann!"),
        ("p1.s7", "paragraph", "Right column comes after."),
        ("p1.s8", "instruction", "2. Read it again."),
        ("p1.s9", "table", "1. a. pottery | art of making pots\n2. b. weave | make cloth"),
        ("p1.s10", "caption", "A potter at work"),
        ("p1.s11", "list_item", "3. Make notes"),
    ]
    assert [span.order for span in spans] == list(range(1, 12))
    assert document.pages[0].number == 1 and document.media_type == "application/pdf"


def test_boxes_are_converted_to_pdf_points(scan: Path) -> None:
    document = ingest(scan, provider=MockProvider([reply()]))
    spans = {span.span_id: span for span in spans_of(document)}

    assert spans["p1.s1"].bbox == pytest.approx((20.0, 12.0, 240.0, 42.0))
    assert spans["p1.s4"].bbox == pytest.approx((200.0, 300.0, 360.0, 420.0))
    assert spans["p1.s7"].bbox is None
    # a table's box is the union of its rows' boxes; none of its rows had one
    assert spans["p1.s9"].bbox is None


def test_picture_blocks_are_cropped_from_the_page(scan: Path, tmp_path: Path) -> None:
    images = tmp_path / "images"
    document = ingest(scan, image_dir=images, provider=MockProvider([reply()]))

    (picture,) = [span for span in spans_of(document) if span.kind is SpanKind.IMAGE]
    assert picture.image_path == f"{document.doc_id}/p1_img1.png"
    crop = pymupdf.Pixmap(str(images / picture.image_path))
    # 160 x 120 pt at 200 dpi
    assert (crop.width, crop.height) == (pytest.approx(444, abs=2), pytest.approx(333, abs=2))
    assert crop.pixel(crop.width // 2, crop.height // 2) == RED
    assert crop.pixel(2, 2) == RED


def test_picture_without_a_usable_box_is_skipped(scan: Path) -> None:
    layout = {"blocks": [{"kind": "picture", "text": "photo"}, {"kind": "paragraph", "text": "a"}]}
    document = ingest(scan, provider=MockProvider([reply(layout)]))
    assert [span.kind.value for span in spans_of(document)] == ["paragraph"]


def test_without_image_dir_picture_spans_have_no_path(scan: Path) -> None:
    document = ingest(scan, provider=MockProvider([reply()]))
    (picture,) = [span for span in spans_of(document) if span.kind is SpanKind.IMAGE]
    assert picture.image_path is None


def test_span_ids_are_stable(scan: Path) -> None:
    first = ingest(scan, provider=MockProvider([reply()]))
    second = ingest(scan, provider=MockProvider([reply()]))
    assert first.model_dump() == second.model_dump()


# --- cache, cost -------------------------------------------------------------------------------


def test_second_ingest_makes_no_call(scan: Path, tmp_path: Path) -> None:
    inner = MockProvider([reply()])
    provider = CachingProvider(inner, tmp_path / "cache")

    first = ingest(scan, provider=provider)
    second = ingest(scan, provider=provider)

    assert len(inner.calls) == 1
    assert first.model_dump() == second.model_dump()


def test_report_counts_tokens_once_and_zero_when_cached(scan: Path, tmp_path: Path) -> None:
    provider = CachingProvider(MockProvider([reply()], model="muse-spark-1.3"), tmp_path / "c")
    reports: list[ScanReport] = []

    ingest(scan, provider=provider, report=reports.append)
    ingest(scan, provider=provider, report=reports.append)

    first, second = reports
    assert first.ok and first.page == 1 and not first.cached
    assert first.input_tokens > 0 and first.output_tokens > 0
    assert first.cost_usd is not None and first.cost_usd > 0
    assert second.cached and second.input_tokens == 0 and second.cost_usd == 0.0


# --- fallbacks ---------------------------------------------------------------------------------


def test_without_a_provider_the_page_is_one_picture(
    scan: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        document = ingest(scan, image_dir=tmp_path / "images")

    (span,) = spans_of(document)
    assert span.kind is SpanKind.IMAGE and span.text == ""
    assert span.bbox == pytest.approx((0.0, 0.0, PAGE_W, PAGE_H))
    assert span.image_path and (tmp_path / "images" / span.image_path).is_file()
    assert any("scan" in record.message and "page 1" in record.message for record in caplog.records)


def test_bad_json_is_retried_once_then_falls_back(
    scan: Path, caplog: pytest.LogCaptureFixture
) -> None:
    provider = MockProvider(["this is not json", "{still: not json"] * 3)
    reports: list[ScanReport] = []
    with caplog.at_level(logging.WARNING):
        document = ingest(scan, provider=provider, report=reports.append)

    assert len(provider.calls) == 6  # two replies per read, the whole read tried three times
    (span,) = spans_of(document)
    assert span.kind is SpanKind.IMAGE
    assert any("reading the scan failed" in record.message for record in caplog.records)
    (report,) = reports
    assert not report.ok and report.error and report.input_tokens > 0


def test_a_bad_reply_followed_by_a_good_one_succeeds(scan: Path) -> None:
    provider = MockProvider(["oops", reply()])
    document = ingest(scan, provider=provider)
    assert len(provider.calls) == 2
    assert spans_of(document)[0].text == "GETTING STARTED"


def test_a_failing_endpoint_does_not_crash_ingest(scan: Path) -> None:
    provider = MockProvider([])  # raises LLMError on the first call
    document = ingest(scan, provider=provider)
    assert [span.kind for span in spans_of(document)] == [SpanKind.IMAGE]


# --- pages, text PDFs --------------------------------------------------------------------------


def test_pages_option_limits_the_pages_read(tmp_path: Path) -> None:
    scan = scanned_page_pdf(tmp_path / "three.pdf", pages=3)
    provider = MockProvider([reply()])
    document = ingest(scan, provider=provider, pages={2})

    assert [page.number for page in document.pages] == [2]
    assert spans_of(document, 2)[0].span_id == "p2.s1"
    assert len(provider.calls) == 1


def test_text_pdf_is_unchanged_and_never_calls_the_model() -> None:
    path = FIXTURE / "mini-unit-hello.pdf"
    provider = MockProvider([])
    with_provider = ingest(path, provider=provider)
    assert provider.calls == []
    assert with_provider.model_dump() == ingest(path).model_dump()


def test_mixed_document_reads_only_the_scanned_pages(tmp_path: Path) -> None:
    path = tmp_path / "mixed.pdf"
    with pymupdf.open(scanned_page_pdf(tmp_path / "s.pdf")) as scanned, pymupdf.open() as mixed:
        text_page = mixed.new_page(width=PAGE_W, height=PAGE_H)
        text_page.insert_text((50, 80), "A normal text page.")
        mixed.insert_pdf(scanned)
        mixed.save(path)
    provider = MockProvider([reply()])

    document = ingest(path, provider=provider)

    assert len(provider.calls) == 1
    assert [span.text for span in spans_of(document, 1)] == ["A normal text page."]
    assert spans_of(document, 2)[0].text == "GETTING STARTED"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("classdeck.ingest.vision.time.sleep", lambda _s: None)


class _Failing:
    """A provider that raises ``LLMError`` for its first ``failures`` calls, then replies."""

    name = "failing"
    model = "failing"

    def __init__(self, failures: int, status: int | None = None) -> None:
        self.failures, self.status, self.calls = failures, status, 0

    def complete(self, messages: list, **kwargs: object):
        from classdeck.llm import LLMError, LLMResponse
        from classdeck.llm.base import Usage

        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError("timed out", status=self.status)
        return LLMResponse(text=reply(), model="failing", usage=Usage())


def test_a_failed_read_is_tried_again(scan: Path) -> None:
    provider = _Failing(failures=1)
    reports: list[ScanReport] = []
    document = ingest(scan, provider=provider, report=reports.append)
    assert provider.calls == 2
    assert spans_of(document)[0].text == "GETTING STARTED"
    assert [r.ok for r in reports] == [True]


def test_a_page_that_keeps_failing_is_reported_not_silent(scan: Path) -> None:
    provider = _Failing(failures=9)
    reports: list[ScanReport] = []
    document = ingest(scan, provider=provider, report=reports.append)
    assert provider.calls == 3
    assert spans_of(document)[0].kind is SpanKind.IMAGE
    (report,) = reports
    assert not report.ok and "timed out" in report.error


def test_a_rejected_key_is_not_retried(scan: Path) -> None:
    provider = _Failing(failures=5, status=401)
    ingest(scan, provider=provider)
    assert provider.calls == 1


def test_no_endpoint_is_reported_as_a_failed_page(scan: Path) -> None:
    reports: list[ScanReport] = []
    ingest(scan, report=reports.append)
    (report,) = reports
    assert not report.ok and "no model endpoint" in report.error


def test_highlights_and_track_become_span_fields(scan: Path) -> None:
    layout = {
        "blocks": [
            {"kind": "instruction", "text": "1 Listen and read.", "track": "Track 7"},
            {
                "kind": "speaker_line",
                "speaker": "Ann",
                "text": "Could you help me, please?",
                "highlight": ["Could you help me", "not in the text"],
            },
        ]
    }
    document = ingest(scan, provider=MockProvider([json.dumps(layout)]))
    instruction, line = spans_of(document)
    assert instruction.track == 7
    assert line.highlights == ["Could you help me"]
