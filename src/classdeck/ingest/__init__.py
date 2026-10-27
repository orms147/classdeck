"""Document ingest: PDF, DOCX and PPTX files into ``SourceDocument`` pages of spans."""

from __future__ import annotations

from classdeck.ingest.base import (
    EXTRACTION_VERSION,
    Extractor,
    UnsupportedFormat,
    ingest,
    sha256_of,
)

__all__ = [
    "EXTRACTION_VERSION",
    "Extractor",
    "UnsupportedFormat",
    "ingest",
    "sha256_of",
]
