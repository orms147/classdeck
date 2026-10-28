"""Document ingest: PDF, DOCX and PPTX files into ``SourceDocument`` pages of spans."""

from __future__ import annotations

from classdeck.ingest.base import (
    EXTRACTION_VERSION,
    Extractor,
    UnsupportedFormat,
    ingest,
    sha256_of,
)
from classdeck.ingest.vision import PageLayout, ScanReport, is_scanned_page, read_page

__all__ = [
    "EXTRACTION_VERSION",
    "Extractor",
    "PageLayout",
    "ScanReport",
    "UnsupportedFormat",
    "ingest",
    "is_scanned_page",
    "read_page",
    "sha256_of",
]
