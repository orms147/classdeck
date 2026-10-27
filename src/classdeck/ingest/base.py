"""Document ingest: turn a PDF, DOCX or PPTX file into a ``SourceDocument`` of addressable spans.

One extractor per format returns the pages; this module owns what they share: format dispatch,
hashing, span numbering, text normalisation and image output. See ``docs/source-grounding.md``
section 1. Span ids come from ``(page, order)`` only, so the same file ingested with the same
``EXTRACTION_VERSION`` always yields the same ids.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

from classdeck.lesson import (
    Page,
    SourceDocument,
    Span,
    SpanKind,
    doc_id_from_sha256,
    span_id,
)

logger = logging.getLogger(__name__)

EXTRACTION_VERSION = 1
"""Bump whenever span segmentation changes: stored citations then go stale."""

_WHITESPACE = re.compile(r"\s+")
_LIST_START = re.compile(r"^\s*(?:\d{1,2}[.)]\s|[\u2022\u25cf\u25aa\u25e6\uf0b7]|[-\u2013]\s)")
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class UnsupportedFormat(ValueError):
    """The file suffix is not one of ``.pdf``, ``.docx`` or ``.pptx``."""


class Extractor(Protocol):
    """Reads one file format into pages of spans."""

    media_type: str

    def extract(self, path: Path, *, doc_id: str, image_dir: Path | None) -> list[Page]: ...


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest(path: Path, *, image_dir: Path | None = None) -> SourceDocument:
    """Extract ``path`` into a ``SourceDocument``.

    Images are written to ``image_dir / doc_id / p<page>_img<n>.<ext>`` and the spans carry the
    path relative to ``image_dir``. Without ``image_dir`` image spans are still emitted, with
    ``image_path=None``.
    """
    path = Path(path)
    extractor = _extractor_for(path.suffix.lower())
    sha256 = sha256_of(path)
    doc_id = doc_id_from_sha256(sha256)
    pages = extractor.extract(path, doc_id=doc_id, image_dir=image_dir)
    return SourceDocument(
        doc_id=doc_id,
        filename=path.name,
        sha256=sha256,
        media_type=extractor.media_type,
        extraction_version=EXTRACTION_VERSION,
        pages=pages,
    )


def _extractor_for(suffix: str) -> Extractor:
    # Imported on demand: the format libraries are heavy and each is needed only for its format.
    if suffix == ".pdf":
        from classdeck.ingest.pdf import PdfExtractor

        return PdfExtractor()
    if suffix == ".docx":
        from classdeck.ingest.docx import DocxExtractor

        return DocxExtractor()
    if suffix == ".pptx":
        from classdeck.ingest.pptx import PptxExtractor

        return PptxExtractor()
    raise UnsupportedFormat(f"unsupported file type {suffix!r}: expected .pdf, .docx or .pptx")


# --- helpers shared by the extractors ----------------------------------------------------------


def normalize_text(text: str) -> str:
    """Collapse every whitespace run (newlines and tabs included) to one space."""
    return _WHITESPACE.sub(" ", text).strip()


def is_list_text(text: str) -> bool:
    """True for numbered lines ("1. ...", "2) ...") and bulleted lines."""
    return _LIST_START.match(text) is not None


def table_text(rows: Iterable[Iterable[str | None]]) -> str:
    """Cells joined with `` | `` and rows with newlines; rows without any text are dropped."""
    lines = []
    for row in rows:
        cells = [normalize_text(cell or "") for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


class PageBuilder:
    """Collects the spans of one page in reading order and numbers them from 1."""

    def __init__(self, number: int) -> None:
        self.number = number
        self._spans: list[Span] = []
        self.image_count = 0

    def __len__(self) -> int:
        return len(self._spans)

    def add(
        self,
        kind: SpanKind,
        text: str,
        *,
        bbox: Sequence[float] | None = None,
        image_path: str | None = None,
    ) -> None:
        """Append a span. Only ``image`` spans may be empty; other empty spans are dropped."""
        if kind is SpanKind.IMAGE:
            self.image_count += 1
        elif not text.strip():
            return
        order = len(self._spans) + 1
        self._spans.append(
            Span(
                span_id=span_id(self.number, order),
                kind=kind,
                text=text,
                order=order,
                bbox=_round_bbox(bbox),
                image_path=image_path,
            )
        )

    def build(self) -> Page:
        return Page(number=self.number, spans=list(self._spans))


def _round_bbox(bbox: Sequence[float] | None) -> tuple[float, float, float, float] | None:
    if bbox is None:
        return None
    x0, y0, x1, y1 = (round(float(value), 2) for value in bbox)
    return (x0, y0, x1, y1)


class ImageSink:
    """Writes extracted images under ``image_dir / doc_id`` and returns their relative paths."""

    def __init__(self, image_dir: Path | None, doc_id: str) -> None:
        self._doc_id = doc_id
        self._root = None if image_dir is None else Path(image_dir)

    @property
    def enabled(self) -> bool:
        return self._root is not None

    def save(self, blob: bytes, *, page: int, index: int) -> str | None:
        """Store ``blob`` as ``p<page>_img<index>`` and return its path relative to ``image_dir``.

        PNG and JPEG bytes are kept as they are; other raster formats are re-encoded as PNG.
        Returns ``None`` when image output is disabled or the bytes are not a usable image.
        """
        if self._root is None:
            return None
        found = _png_or_jpeg(blob)
        if found is None:
            logger.debug("page %s image %s: unreadable image data skipped", page, index)
            return None
        data, extension = found
        relative = f"{self._doc_id}/p{page}_img{index}.{extension}"
        target = self._root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return relative


def _png_or_jpeg(blob: bytes) -> tuple[bytes, str] | None:
    if blob.startswith(_PNG_MAGIC):
        return blob, "png"
    if blob.startswith(_JPEG_MAGIC):
        return blob, "jpeg"
    try:
        import pymupdf

        return pymupdf.Pixmap(blob).tobytes("png"), "png"
    except Exception as error:
        logger.debug("could not convert image data: %s", error)
        return None
