"""Grounding: resolve citations, verify them, assign provenance (``docs/source-grounding.md``)."""

from __future__ import annotations

from classdeck.grounding.linker import (
    QUOTE_LENGTH,
    Sources,
    comparable_text,
    fill_quotes,
    quote_of,
    resolve_citation,
)
from classdeck.grounding.metrics import (
    ai_added_ratio,
    grounded_ratio,
    grounding_summary,
    is_grounded,
)
from classdeck.grounding.provenance import assign_provenance, blocks_to_ground, provenance_for
from classdeck.grounding.verifier import (
    CITATION_SUPPORTED,
    IMAGE_CITING_KINDS,
    JUDGE_PROMPT,
    CitationVerdict,
    JudgeVerdict,
    Method,
    Verification,
    judge_confidence,
    token_f1,
    verify_citation,
    verify_lesson,
)

verify = verify_lesson
"""The stage entry point under the name ``docs/planning.md`` uses (``grounding.verify``)."""

__all__ = [
    "CITATION_SUPPORTED",
    "IMAGE_CITING_KINDS",
    "JUDGE_PROMPT",
    "QUOTE_LENGTH",
    "CitationVerdict",
    "JudgeVerdict",
    "Method",
    "Sources",
    "Verification",
    "ai_added_ratio",
    "assign_provenance",
    "blocks_to_ground",
    "comparable_text",
    "fill_quotes",
    "grounded_ratio",
    "grounding_summary",
    "is_grounded",
    "judge_confidence",
    "provenance_for",
    "quote_of",
    "resolve_citation",
    "token_f1",
    "verify",
    "verify_citation",
    "verify_lesson",
]
