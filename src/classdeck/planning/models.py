"""Planning models: the outline, the model reply shapes, and the span references between them.

An :class:`Outline` is the plan for one period (``docs/planning.md``, stage 1).
:class:`OutlineRefinement` and :class:`DraftSection` are the shapes a model replies with; the
engine validates them against the outline before anything reaches the lesson.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from classdeck.lesson.schema import ActivityType, BlockKind, CurriculumRef, Phase, Strict


class SpanRef(Strict):
    """A span of a source document, as the outline points at it."""

    doc_id: str = Field(min_length=1)
    page: int
    span_id: str = Field(min_length=1)


class OutlineSection(Strict):
    """One phase of the period: what it drills, which outcomes it serves, which spans it uses."""

    phase: Phase
    title: str
    minutes: int = Field(ge=0)
    activity_type: ActivityType | None = None
    outcome_ids: list[str] = []
    span_refs: list[SpanRef] = []
    notes: str = ""
    review: bool = False
    """A period-2 presentation that reviews the previous period: its ``span_refs`` are the dialogue
    and pattern spans of activities 1-2, and the drafter opens it with vocabulary and pattern
    blocks built from them (``docs/planning.md``, "Period 2 shape")."""


class Outline(Strict):
    title: str = Field(min_length=1)
    grade: int = Field(ge=3, le=5)
    curriculum: CurriculumRef
    doc_ids: list[str] = []
    sections: list[OutlineSection]

    def total_minutes(self) -> int:
        return sum(section.minutes for section in self.sections)


class RefinedSection(Strict):
    """One section as the model returns it from outline refinement."""

    title: str
    outcome_ids: list[str]
    notes: str = ""
    span_ids: list[str]


class OutlineRefinement(Strict):
    """The model's reply to ``outline_refine``: the skeleton's sections, in the same order."""

    sections: list[RefinedSection]


class DraftBlock(Strict):
    """A proposed block: typed content, proposed citations, dependencies as reply indices."""

    kind: BlockKind
    content: dict[str, Any]
    span_ids: list[str] = []
    depends_on: list[int] = []
    teacher_note: str | None = None


class DraftSection(Strict):
    """The model's reply to ``draft_section``; the template drafter returns the same shape."""

    blocks: list[DraftBlock]
