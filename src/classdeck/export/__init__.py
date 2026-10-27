"""Export: a lesson as PowerPoint slides and a Word lesson plan, both with a source map."""

from __future__ import annotations

from classdeck.export.docx import export_docx
from classdeck.export.html import export_html
from classdeck.export.pptx import export_pptx
from classdeck.export.source_map import (
    AI_ADDED_BADGE,
    AI_ADDED_NOTE,
    PROVENANCE_LABEL,
    ExportBlocked,
    SourceMapRow,
    build_source_map,
    collect_failures,
    collect_warnings,
    ensure_exportable,
    format_check,
    format_citation,
    origin_text,
)

__all__ = [
    "AI_ADDED_BADGE",
    "AI_ADDED_NOTE",
    "PROVENANCE_LABEL",
    "ExportBlocked",
    "SourceMapRow",
    "build_source_map",
    "collect_failures",
    "collect_warnings",
    "ensure_exportable",
    "export_docx",
    "export_html",
    "export_pptx",
    "format_check",
    "format_citation",
    "origin_text",
]
