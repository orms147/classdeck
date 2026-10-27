"""The composed stages every lesson goes through: draft, infer, ground, check, repair, check.

``docs/planning.md``, "Shared pipeline": the CLI, eval and the hand-off import call these two
functions instead of repeating the sequence, so they cannot drift apart. ``planning``,
``grounding`` and ``english`` stay independent stages; this module is the one place that
composes them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from classdeck.curriculum.loader import Curriculum
from classdeck.english.dependencies import infer
from classdeck.english.validators import run_all
from classdeck.grounding import fill_quotes, verify_lesson
from classdeck.lesson.schema import Lesson, SourceDocument
from classdeck.llm.base import LLMError, LLMProvider
from classdeck.planning.drafter import draft_lesson_report
from classdeck.planning.models import Outline
from classdeck.planning.repair import repair_lesson

logger = logging.getLogger(__name__)

Sources = Mapping[str, SourceDocument]

StageCallback = Callable[[str], None]
"""Called with a stage name -- ``draft``, ``grounding``, ``validators``, ``repair`` -- when it
starts; eval uses it to time the stages and to attribute token usage to them."""


def _no_stage(name: str) -> None:
    return None


def finish_lesson(
    lesson: Lesson,
    sources: Sources,
    curriculum: Curriculum,
    provider: LLMProvider | None,
    *,
    on_stage: StageCallback | None = None,
) -> Lesson:
    """Dependency inference, citation quotes, grounding, level checks: the re-check of a lesson.

    ``provider`` is the judge for borderline citations; ``None`` applies the midpoint rule. This
    is what ``classdeck check`` runs on a saved lesson and what the hand-off import runs on an
    assembled one. The input lesson is not changed.
    """
    stage = on_stage or _no_stage
    stage("grounding")
    lesson = infer(lesson)
    lesson = fill_quotes(lesson, sources)
    lesson = verify_lesson(lesson, sources, provider)
    stage("validators")
    return run_all(lesson, curriculum, sources=sources)


def build_lesson(
    outline: Outline,
    sources: Sources,
    curriculum: Curriculum,
    provider: LLMProvider | None,
    *,
    repair: bool = True,
    judge: LLMProvider | None = None,
    on_stage: StageCallback | None = None,
) -> Lesson:
    """Draft, finish and -- with a provider -- repair a lesson from an outline.

    ``provider`` drafts every section and, unless ``judge`` is given, settles borderline
    citations; ``None`` means the template drafter, the midpoint rule and no repair. With
    ``repair`` the model-written blocks that still warn on ``english.vocab_level`` or
    ``english.grammar_scope`` go through one repair round (:mod:`classdeck.planning.repair`)
    and the level checks run again when a block was replaced. A model failure during the repair
    leaves the lesson as drafted.
    """
    stage = on_stage or _no_stage
    stage("draft")
    lesson, report = draft_lesson_report(outline, list(sources.values()), curriculum, provider)
    grounding_judge = judge if judge is not None else provider
    lesson = finish_lesson(lesson, sources, curriculum, grounding_judge, on_stage=on_stage)
    if not repair:
        return lesson
    if provider is None:
        logger.info("repair skipped: no provider")
        return lesson
    stage("repair")
    try:
        repaired_lesson, repaired = repair_lesson(
            lesson, curriculum, provider, sources=sources, model_block_ids=report.model_block_ids
        )
    except LLMError as exc:  # the repair is best effort: a drafted lesson is not thrown away
        logger.warning("repair abandoned, the drafted blocks are kept: %s", exc)
        return lesson
    lesson = repaired_lesson
    if repaired.replaced:
        lesson = run_all(lesson, curriculum, sources=sources)
    return lesson
