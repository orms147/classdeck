"""PDF extraction with PyMuPDF.

Each physical page becomes one ``Page``. Text blocks become headings, list items or paragraphs,
embedded pictures become cropped ``image`` spans and ruled tables become ``table`` spans, all in
reading order. Bounding boxes are in PDF points with the origin at the top-left of the page.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from classdeck.ingest.base import (
    ImageSink,
    PageBuilder,
    is_list_text,
    normalize_text,
    table_text,
)
from classdeck.lesson import Page, SpanKind

logger = logging.getLogger(__name__)

HEADING_SIZE_RATIO = 1.3
MIN_IMAGE_SIDE_PT = 24.0
IMAGE_DPI = 150

_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES

type _BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class _Item:
    kind: SpanKind
    text: str
    bbox: _BBox


@dataclass(frozen=True)
class _Line:
    text: str
    size: float
    bbox: _BBox


class PdfExtractor:
    media_type = "application/pdf"

    def extract(self, path: Path, *, doc_id: str, image_dir: Path | None) -> list[Page]:
        sink = ImageSink(image_dir, doc_id)
        with pymupdf.open(path) as document:
            return [
                _extract_page(page, number, sink) for number, page in enumerate(document, start=1)
            ]


def _extract_page(page: pymupdf.Page, number: int, sink: ImageSink) -> Page:
    tables = _table_items(page)
    items = [*_text_items(page, tables), *tables, *_image_items(page)]
    items.sort(key=lambda item: (round(item.bbox[1]), item.bbox[0]))

    builder = PageBuilder(number)
    for item in items:
        if item.kind is SpanKind.IMAGE:
            path = _save_image(page, item.bbox, sink, number, builder.image_count + 1)
            builder.add(item.kind, "", bbox=item.bbox, image_path=path)
        else:
            builder.add(item.kind, item.text, bbox=item.bbox)
    return builder.build()


# --- text --------------------------------------------------------------------------------------


def _text_items(page: pymupdf.Page, tables: list[_Item]) -> list[_Item]:
    blocks = [
        block
        for block in page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]
        if block.get("type") == 0
    ]
    sizes = [
        span["size"]
        for block in blocks
        for line in block["lines"]
        for span in line["spans"]
        if span["text"].strip()
    ]
    if not sizes:
        return []
    # median_low so that a short page with one large heading still has a body-sized baseline.
    heading_size = HEADING_SIZE_RATIO * statistics.median_low(sizes)

    items = []
    for block in blocks:
        for group in _group_lines(_block_lines(block), heading_size):
            bbox = _union([line.bbox for line in group])
            if _inside_any(bbox, tables):
                continue
            text = normalize_text(" ".join(line.text for line in group))
            if not text:
                continue
            items.append(_Item(_text_kind(text, _dominant_size(group), heading_size), text, bbox))
    return items


def _block_lines(block: dict) -> list[_Line]:
    lines = []
    for line in block["lines"]:
        text = normalize_text("".join(span["text"] for span in line["spans"]))
        if not text:
            continue
        weights: dict[float, int] = {}
        for span in line["spans"]:
            size = round(span["size"], 1)
            weights[size] = weights.get(size, 0) + len(span["text"].strip())
        size = max(weights.items(), key=lambda pair: (pair[1], pair[0]))[0]
        lines.append(_Line(text, size, _bbox(line["bbox"])))
    return lines


def _group_lines(lines: list[_Line], heading_size: float) -> list[list[_Line]]:
    """Merge the lines of a block into paragraphs.

    A block is one paragraph, except that a numbered or bulleted line starts a new one and so
    does a change between heading-sized and body-sized text: tightly set activity lines are
    sometimes delivered as one block, but each must be citable on its own.
    """
    groups: list[list[_Line]] = []
    for line in lines:
        if groups:
            previous = groups[-1][-1]
            same_scale = (previous.size >= heading_size) == (line.size >= heading_size)
            if same_scale and not is_list_text(line.text):
                groups[-1].append(line)
                continue
        groups.append([line])
    return groups


def _dominant_size(group: list[_Line]) -> float:
    weights: dict[float, int] = {}
    for line in group:
        weights[line.size] = weights.get(line.size, 0) + len(line.text)
    return max(weights.items(), key=lambda pair: (pair[1], pair[0]))[0]


def _text_kind(text: str, size: float, heading_size: float) -> SpanKind:
    if size >= heading_size:
        return SpanKind.HEADING
    if is_list_text(text):
        return SpanKind.LIST_ITEM
    return SpanKind.PARAGRAPH


# --- tables ------------------------------------------------------------------------------------


def _table_items(page: pymupdf.Page) -> list[_Item]:
    try:
        found = page.find_tables().tables
        extracted = [(_bbox(table.bbox), table.extract()) for table in found]
    except Exception as error:
        logger.debug("page %s: table detection unavailable: %s", page.number + 1, error)
        return []
    items = []
    for bbox, rows in extracted:
        # A single ruled box around some text is a frame, not a table.
        if len(rows) < 2 or max(len(row) for row in rows) < 2:
            continue
        text = table_text(rows)
        if text:
            items.append(_Item(SpanKind.TABLE, text, bbox))
    return items


def _inside_any(bbox: _BBox, tables: list[_Item]) -> bool:
    center_x = (bbox[0] + bbox[2]) / 2
    center_y = (bbox[1] + bbox[3]) / 2
    return any(
        table.bbox[0] <= center_x <= table.bbox[2] and table.bbox[1] <= center_y <= table.bbox[3]
        for table in tables
    )


# --- images ------------------------------------------------------------------------------------


def _image_items(page: pymupdf.Page) -> list[_Item]:
    items = []
    # One xref is listed once per use on the page, while get_image_rects returns every use.
    for xref in dict.fromkeys(image[0] for image in page.get_images(full=True)):
        try:
            rects = page.get_image_rects(xref)
        except Exception as error:
            logger.debug("page %s: image xref %s skipped: %s", page.number + 1, xref, error)
            continue
        for rect in rects:
            if rect.width >= MIN_IMAGE_SIDE_PT and rect.height >= MIN_IMAGE_SIDE_PT:
                items.append(_Item(SpanKind.IMAGE, "", _bbox(rect)))
    return items


def _save_image(
    page: pymupdf.Page, bbox: _BBox, sink: ImageSink, number: int, index: int
) -> str | None:
    if not sink.enabled:
        return None
    try:
        blob = page.get_pixmap(clip=pymupdf.Rect(bbox), dpi=IMAGE_DPI).tobytes("png")
    except Exception as error:
        logger.debug("page %s image %s: render failed: %s", number, index, error)
        return None
    return sink.save(blob, page=number, index=index)


# --- geometry ----------------------------------------------------------------------------------


def _bbox(value: Iterable[float]) -> _BBox:
    x0, y0, x1, y1 = (float(coordinate) for coordinate in value)
    return (x0, y0, x1, y1)


def _union(boxes: list[_BBox]) -> _BBox:
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )
