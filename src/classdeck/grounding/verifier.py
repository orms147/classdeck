"""Citation verification: lexical overlap, a model judge for the borderline band, one check.

Each citation a block carries is a proposal. Clear cases are settled by token overlap, the
borderline band goes to a short yes/no judge prompt (or to the midpoint rule when no provider is
available or the judge gives no usable answer), and the result lands on the block as
``grounding.citation_supported``. See ``docs/source-grounding.md`` section 3.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from classdeck.english.rules import GROUNDING_THRESHOLDS
from classdeck.english.validators import CheckOutcome, apply_outcomes
from classdeck.grounding.linker import (
    Sources,
    comparable_text,
    fill_quotes,
    resolve_citation,
    steps_text,
)
from classdeck.grounding.provenance import assign_provenance, blocks_to_ground, provenance_for
from classdeck.lesson.schema import (
    Block,
    BlockKind,
    CheckResult,
    CheckStatus,
    Citation,
    Lesson,
    Provenance,
    Span,
    SpanKind,
    Strict,
)
from classdeck.llm.base import LLMProvider, Message
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, complete_json

CITATION_SUPPORTED = "grounding.citation_supported"
JUDGE_PROMPT = "grounding_judge"

IMAGE_CITING_KINDS: frozenset[BlockKind] = frozenset({BlockKind.IMAGE_REF, BlockKind.VOCABULARY})
"""Block kinds an image span can support: the picture itself is reused (spec section 6)."""

Method = Literal["lexical", "judge", "midpoint", "image", "dangling"]

_TOKEN = re.compile(r"[^\W_]+")
"""Word tokens; underscores are excluded so pattern slots (``___``) never count as words."""


def token_f1(a: str, b: str) -> float:
    """Bag-of-words F1 over lower-cased word tokens; ``0.0`` when either text has none."""
    tokens_a = Counter(_TOKEN.findall(a.lower()))
    tokens_b = Counter(_TOKEN.findall(b.lower()))
    if not tokens_a or not tokens_b:
        return 0.0
    common = sum((tokens_a & tokens_b).values())
    if common == 0:
        return 0.0
    precision = common / tokens_a.total()
    recall = common / tokens_b.total()
    return 2 * precision * recall / (precision + recall)


def judge_confidence(overlap: float, supported: bool) -> float:
    """Confidence of a judge decision: ``(1 + overlap) / 2`` when accepted, ``overlap / 2`` if not.

    Both stay monotone in the overlap, so two accepted citations keep the order their lexical
    evidence gives them.
    """
    return (1 + overlap) / 2 if supported else overlap / 2


class JudgeVerdict(BaseModel):
    """The judge's reply. ``reason`` lets the model explain itself; it is never copied anywhere."""

    supported: bool
    reason: str = ""


class CitationVerdict(Strict):
    """What verification decided about one citation of one block."""

    block_id: str
    citation: Citation
    supported: bool
    confidence: float = Field(ge=0, le=1)
    method: Method
    detail: str


class Verification(NamedTuple):
    """Result of :func:`verify_citation`: ``(supported, confidence, method, detail)``."""

    supported: bool
    confidence: float
    method: Method
    detail: str


def _judge(block_text: str, span_text: str, provider: LLMProvider) -> JudgeVerdict:
    prompt = load_prompt(JUDGE_PROMPT, block_text=block_text, span_text=span_text)
    messages = [Message(role="user", content=prompt)]
    return complete_json(provider, messages, JudgeVerdict, retries=1)


def _midpoint(overlap: float, thresholds: tuple[float, float], why: str) -> Verification:
    low, high = thresholds
    midpoint = (low + high) / 2
    detail = f"lexical overlap {overlap:.2f}; midpoint rule at {midpoint:.2f} ({why})"
    return Verification(overlap >= midpoint, overlap, "midpoint", detail)


def verify_citation(
    block_text: str,
    span: Span,
    provider: LLMProvider | None,
    *,
    block_kind: BlockKind | None = None,
    thresholds: tuple[float, float] = GROUNDING_THRESHOLDS,
) -> Verification:
    """Decide whether ``span`` supports ``block_text``.

    Overlap at or above ``HIGH`` supports, below ``LOW`` does not; in between the judge decides
    when a provider is given, else the midpoint rule ``overlap >= (LOW + HIGH) / 2`` applies.
    The midpoint rule is also the fallback when the judge never answers valid JSON; other
    ``LLMError``s propagate. An image span has no text to compare: it supports a block whose
    kind is in :data:`IMAGE_CITING_KINDS` (the picture itself is reused) and nothing else.
    """
    if span.kind is SpanKind.IMAGE:
        if block_kind in IMAGE_CITING_KINDS:
            return Verification(True, 1.0, "image", "image span resolved; the picture is reused")
        return Verification(False, 0.0, "image", "image span cannot support text content")
    low, high = thresholds
    overlap = token_f1(block_text, span.text)
    summary = f"lexical overlap {overlap:.2f}"
    if overlap >= high or overlap < low:
        return Verification(overlap >= high, overlap, "lexical", summary)
    if provider is None:
        return _midpoint(overlap, thresholds, "no judge available")
    try:
        verdict = _judge(block_text, span.text, provider)
    except StructuredOutputError:
        return _midpoint(overlap, thresholds, "judge gave no usable answer")
    answer = "supported" if verdict.supported else "not supported"
    detail = f"judge: {answer} ({summary})"
    return Verification(
        verdict.supported, judge_confidence(overlap, verdict.supported), "judge", detail
    )


def _verify_one(
    block: Block,
    block_text: str,
    citation: Citation,
    sources: Sources,
    provider: LLMProvider | None,
    thresholds: tuple[float, float],
    steps: str = "",
) -> CitationVerdict:
    span = resolve_citation(citation, sources)
    if span is None:
        detail = (
            f"span {citation.span_id!r} not found on page {citation.page} of {citation.doc_id!r}"
        )
        return CitationVerdict(
            block_id=block.id,
            citation=citation,
            supported=False,
            confidence=0.0,
            method="dangling",
            detail=detail,
        )
    outcome = verify_citation(
        block_text, span, provider, block_kind=block.kind, thresholds=thresholds
    )
    if not outcome.supported and steps:
        # an activity that copies the span's lines into its steps is supported by the steps
        copied = verify_citation(
            steps, span, provider, block_kind=block.kind, thresholds=thresholds
        )
        if copied.supported:
            outcome = copied
    return CitationVerdict(block_id=block.id, citation=citation, **outcome._asdict())


def _check_for(block: Block, verdicts: Sequence[CitationVerdict]) -> CheckResult:
    """One ``grounding.citation_supported`` result from the verdicts of a block's citations.

    ``pass`` when any citation is supported (dangling ones are named in the detail), ``fail``
    when none is and at least one dangles, ``warn`` otherwise. Provenance follows the same
    rule, so an ``extracted`` block always carries a ``pass`` (invariant 3).
    """
    total = len(verdicts)
    dangling = [verdict for verdict in verdicts if verdict.method == "dangling"]
    supported = [verdict for verdict in verdicts if verdict.supported]
    where = ", ".join(
        f"{v.citation.span_id} (page {v.citation.page}, {v.citation.doc_id})" for v in dangling
    )
    if supported:
        parts = [f"{len(supported)}/{total} supported", *(verdict.detail for verdict in supported)]
        if dangling:
            parts.append(f"dangling: {where}")
        return CheckResult(
            name=CITATION_SUPPORTED, status=CheckStatus.PASS, detail="; ".join(parts)
        )
    if dangling:
        detail = f"{len(dangling)}/{total} citation(s) dangling: {where}"
        return CheckResult(name=CITATION_SUPPORTED, status=CheckStatus.FAIL, detail=detail)
    provenance = provenance_for(block, supported=False)
    outcome = (
        "provenance set to generated"
        if provenance is Provenance.GENERATED
        else f"provenance kept {provenance.value}"
    )
    detail = f"no supported citation; {outcome}"
    return CheckResult(name=CITATION_SUPPORTED, status=CheckStatus.WARN, detail=detail)


def verify_lesson(
    lesson: Lesson,
    sources: Sources,
    provider: LLMProvider | None,
    *,
    thresholds: tuple[float, float] = GROUNDING_THRESHOLDS,
) -> Lesson:
    """Verify every citation of every content block and return a new lesson.

    Content blocks outside every section are verified too. Each citation gets its
    ``confidence``; each cited block gets one ``grounding.citation_supported`` result (see
    :func:`_check_for`), replacing any earlier result of that name on any block; provenance is
    then assigned and missing quotes filled. Blocks without citations get no check. The input
    lesson is not changed.
    """
    result = lesson.model_copy(deep=True)
    for block in result.blocks.values():
        block.checks = [check for check in block.checks if check.name != CITATION_SUPPORTED]
    verdicts: list[CitationVerdict] = []
    outcomes: list[CheckOutcome] = []
    for block in blocks_to_ground(result):
        if not block.sources:
            continue
        text = comparable_text(block)
        block_verdicts: list[CitationVerdict] = []
        for citation in block.sources:
            verdict = _verify_one(
                block, text, citation, sources, provider, thresholds, steps_text(block)
            )
            citation.confidence = verdict.confidence
            block_verdicts.append(verdict)
        verdicts.extend(block_verdicts)
        outcomes.append((block.id, _check_for(block, block_verdicts)))
    result = assign_provenance(apply_outcomes(result, outcomes), verdicts)
    return fill_quotes(result, sources)
