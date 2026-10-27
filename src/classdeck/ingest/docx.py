"""DOCX extraction with python-docx.

Word files have no fixed pages, so pages are synthetic: a new page starts after an explicit page
break or a section break, and otherwise after every ``PARAGRAPHS_PER_PAGE`` spans. Body
paragraphs and tables are read in document order. There are no reliable coordinates, so spans
carry no ``bbox``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from docx import Document
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

from classdeck.ingest.base import (
    ImageSink,
    PageBuilder,
    is_list_text,
    normalize_text,
    table_text,
)
from classdeck.lesson import Page, SpanKind

logger = logging.getLogger(__name__)

PARAGRAPHS_PER_PAGE = 40


class DocxExtractor:
    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    def extract(self, path: Path, *, doc_id: str, image_dir: Path | None) -> list[Page]:
        document = Document(str(path))
        sink = ImageSink(image_dir, doc_id)
        pager = _Pager()
        for element in document.element.body.iterchildren():
            if isinstance(element, CT_P):
                paragraph = Paragraph(element, document)
                text = normalize_text(paragraph.text)
                break_before, break_after = _page_breaks(element)
                if break_before:
                    pager.break_page()
                if text:
                    pager.add_text(_paragraph_kind(paragraph, text), text)
                for blob in _image_blobs(element, document):
                    page = pager.page
                    saved = sink.save(blob, page=page.number, index=page.image_count + 1)
                    page.add(SpanKind.IMAGE, "", image_path=saved)
                if break_after:
                    pager.break_page()
            elif isinstance(element, CT_Tbl):
                text = table_text(_table_rows(Table(element, document)))
                if text:
                    pager.add_text(SpanKind.TABLE, text)
        return pager.finish()


class _Pager:
    """Builds the synthetic pages: a break on request, or after ``PARAGRAPHS_PER_PAGE`` spans."""

    def __init__(self) -> None:
        self.page = PageBuilder(1)
        self._finished: list[Page] = []
        self._text_spans = 0

    def break_page(self) -> None:
        self._finished.append(self.page.build())
        self.page = PageBuilder(self.page.number + 1)
        self._text_spans = 0

    def add_text(self, kind: SpanKind, text: str) -> None:
        if self._text_spans >= PARAGRAPHS_PER_PAGE:
            self.break_page()
        self.page.add(kind, text)
        self._text_spans += 1

    def finish(self) -> list[Page]:
        """All pages, without the empty ones a trailing break leaves at the end."""
        pages = [*self._finished, self.page.build()]
        while pages and not pages[-1].spans:
            pages.pop()
        return pages


def _paragraph_kind(paragraph: Paragraph, text: str) -> SpanKind:
    style = paragraph.style
    name = (style.name if style is not None else None) or ""
    if name.startswith("Heading") or name == "Title":
        return SpanKind.HEADING
    if "List" in name or is_list_text(text):
        return SpanKind.LIST_ITEM
    return SpanKind.PARAGRAPH


def _page_breaks(paragraph: CT_P) -> tuple[bool, bool]:
    """Whether a page break falls before the paragraph's text and whether one follows it.

    A section break paragraph ends its section, so it counts as a break after the text.
    """
    before = after = False
    seen_text = False
    for element in paragraph.iter():
        if element.tag == qn("w:t") and (element.text or "").strip():
            seen_text = True
        elif element.tag == qn("w:br") and element.get(qn("w:type")) == "page":
            if seen_text:
                after = True
            else:
                before = True
    properties = paragraph.pPr
    if properties is not None and properties.find(qn("w:sectPr")) is not None:
        after = True
    return before, after


def _image_blobs(paragraph: CT_P, document: DocxDocument) -> list[bytes]:
    blobs = []
    for blip in paragraph.iter(qn("a:blip")):
        relationship_id = blip.get(qn("r:embed"))
        if relationship_id is None:
            continue
        try:
            blobs.append(document.part.related_parts[relationship_id].blob)
        except KeyError:
            logger.debug("image relationship %s not found", relationship_id)
    return blobs


def _table_rows(table: Table) -> list[list[str]]:
    """Cell texts per row; a merged cell shows its text once and is blank where it repeats."""
    seen: set[object] = set()
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            cells.append("" if cell._tc in seen else cell.text)
            seen.add(cell._tc)
        rows.append(cells)
    return rows
