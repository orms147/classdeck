"""LLM judge: grades a finished lesson on a fixed rubric (``docs/evaluation.md#llm-judge``)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from classdeck.lesson import Block, Lesson, SourceDocument
from classdeck.llm import LLMProvider, Message, MockProvider, complete_json, load_prompt

JUDGE_PROMPT = "eval_judge"
CRITERIA = ("faithfulness", "age_fit", "step_clarity", "outcome_alignment", "flow")
JudgeSource = Literal["independent", "generator", "skipped"]

_SPAN_CHARS = 400
_CONTENT_CHARS = 1500


class CriterionScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    justification: str


class JudgeRubric(BaseModel):
    """The reply asked of the judge: one score and one sentence per criterion."""

    model_config = ConfigDict(extra="forbid")

    faithfulness: CriterionScore
    age_fit: CriterionScore
    step_clarity: CriterionScore
    outcome_alignment: CriterionScore
    flow: CriterionScore


class JudgeScores(BaseModel):
    """What eval records: the rubric (``None`` when skipped), its mean and who judged."""

    model_config = ConfigDict(extra="forbid")

    source: JudgeSource
    rubric: JudgeRubric | None = None
    note: str | None = None

    @property
    def mean(self) -> float | None:
        if self.rubric is None:
            return None
        scores = [getattr(self.rubric, name).score for name in CRITERIA]
        return sum(scores) / len(scores)

    def scores(self) -> dict[str, int]:
        if self.rubric is None:
            return {}
        return {name: getattr(self.rubric, name).score for name in CRITERIA}

    def justifications(self) -> dict[str, str]:
        if self.rubric is None:
            return {}
        return {name: getattr(self.rubric, name).justification for name in CRITERIA}


def is_plain_mock(provider: LLMProvider | None) -> bool:
    """True for a ``MockProvider`` with neither canned replies nor a handler."""
    if not isinstance(provider, MockProvider):
        return False
    return getattr(provider, "_queue", None) is None and getattr(provider, "_handler", None) is None


def judge_lesson(
    lesson: Lesson,
    sources: Mapping[str, SourceDocument],
    judge_provider: LLMProvider | None,
    *,
    independent: bool = False,
) -> JudgeScores:
    """Grade ``lesson`` with the judge model.

    ``independent`` says whether the provider is a separately configured judge
    (``Settings.judge_configured``); otherwise the scores are marked as coming from the
    generator. A missing provider or a plain mock provider skips the judge.
    """
    if judge_provider is None or is_plain_mock(judge_provider):
        return JudgeScores(source="skipped", note="no judge model available offline")
    prompt = load_prompt(
        JUDGE_PROMPT, grade=str(lesson.grade), lesson=render_lesson(lesson, sources)
    )
    rubric = complete_json(
        judge_provider, [Message(role="user", content=prompt)], JudgeRubric, retries=1
    )
    return JudgeScores(source="independent" if independent else "generator", rubric=rubric)


def render_lesson(lesson: Lesson, sources: Mapping[str, SourceDocument]) -> str:
    """The lesson as plain text for the judge: sections, blocks and the spans each block cites."""
    spans = _span_index(sources)
    outcome_ids = ", ".join(lesson.curriculum.outcome_ids) or "(none)"
    lines = [f"Title: {lesson.title}", f"Outcomes: {outcome_ids}", ""]
    for section in lesson.sections:
        lines.append(f"## {section.phase.value}: {section.title} ({section.minutes} min)")
        for block_id in section.block_ids:
            lines.extend(_render_block(lesson.blocks[block_id], spans))
        lines.append("")
    return "\n".join(lines).rstrip()


def _render_block(block: Block, spans: Mapping[tuple[str, str], str]) -> list[str]:
    content = json.dumps(block.content.model_dump(mode="json", exclude_none=True))
    if len(content) > _CONTENT_CHARS:
        content = content[:_CONTENT_CHARS] + "..."
    lines = [f"- [{block.id}] {block.kind} ({block.provenance.value}): {content}"]
    for citation in block.sources:
        text = spans.get((citation.doc_id, citation.span_id))
        shown = "(span not found)" if text is None else text[:_SPAN_CHARS]
        lines.append(f"    SOURCE p{citation.page} {citation.span_id}: {shown}")
    return lines


def _span_index(sources: Mapping[str, SourceDocument]) -> dict[tuple[str, str], str]:
    return {
        (doc.doc_id, span.span_id): span.text
        for doc in sources.values()
        for page in doc.pages
        for span in page.spans
    }
