"""Identifier helpers shared by ingest, planning and editing."""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """A short random id such as ``b_3fa91c``. Callers that need uniqueness must check."""
    return f"{prefix}_{secrets.token_hex(3)}"


def doc_id_from_sha256(sha256: str) -> str:
    """Document id derived from the file hash, so re-ingesting the same file keeps its id."""
    return "d_" + sha256[:8]


def span_id(page: int, order: int) -> str:
    """Span id derived from position, stable for a given extraction version."""
    return f"p{page}.s{order}"
