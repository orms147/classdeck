"""Run evaluation cases end to end and collect their metrics (``docs/evaluation.md#running``)."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from classdeck.config import Settings
from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.editing import EditEngine, EditRejected, EditRequest
from classdeck.english.validators import has_failures
from classdeck.evaluation.cases import EvalCase, missing_fixture_reason, source_path
from classdeck.evaluation.judge import JudgeScores, is_plain_mock, judge_lesson
from classdeck.evaluation.metrics import (
    CaseMetrics,
    EditResult,
    compute_metrics,
    evaluate_edit_expectations,
)
from classdeck.evaluation.report import CaseResult, EvalReport
from classdeck.ingest import ingest
from classdeck.lesson import Block, Lesson, LessonStore, SourceDocument, SourceStore
from classdeck.llm import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    UsageMeter,
    estimate_cost_usd,
)
from classdeck.pipeline import build_lesson
from classdeck.planning import outline

logger = logging.getLogger(__name__)

OFFLINE_EDIT_REASON = "offline provider has no scripted replies, so edits are not simulated"
_UNCOMPARED_FIELDS = {"checks", "sources", "provenance"}


class MeteredProvider:
    """Wraps a provider and tallies usage and cost per pipeline stage."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.stage = "setup"
        self.meters: dict[str, UsageMeter] = {}
        self.cost_usd: float | None = None

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        response = self.inner.complete(messages, **kwargs)
        self.meters.setdefault(self.stage, UsageMeter()).add(response)
        if not response.cached:
            cost = estimate_cost_usd(response.model, response.usage)
            if cost is not None:
                self.cost_usd = (self.cost_usd or 0.0) + cost
        return response

    def totals(self) -> tuple[int, int, int]:
        """Input tokens, output tokens and cached calls over all stages."""
        meters = self.meters.values()
        return (
            sum(m.input_tokens for m in meters),
            sum(m.output_tokens for m in meters),
            sum(m.cached_calls for m in meters),
        )


def is_offline(provider: LLMProvider | None) -> bool:
    return provider is None or is_plain_mock(provider)


def run_case(
    case: EvalCase,
    *,
    settings: Settings,
    provider: LLMProvider | None,
    judge_provider: LLMProvider | None,
    eval_dir: Path,
    workdir: Path,
) -> CaseResult:
    """Run one case. Failures of the pipeline are reported in the result, not raised."""
    reason = case.skipped_reason or missing_fixture_reason(case, eval_dir)
    if reason:
        return CaseResult(case_id=case.id, grade=case.grade, status="skipped", reason=reason)
    try:
        return _run(case, settings, provider, judge_provider, eval_dir, workdir)
    except Exception as exc:  # one broken case must not stop the batch
        logger.exception("case %s failed", case.id)
        return CaseResult(
            case_id=case.id, grade=case.grade, status="error", reason=f"{type(exc).__name__}: {exc}"
        )


def run_cases(
    cases: Sequence[EvalCase],
    *,
    settings: Settings,
    provider: LLMProvider | None,
    judge_provider: LLMProvider | None = None,
    eval_dir: Path,
    workdir: Path,
    timestamp: datetime | str | None = None,
) -> EvalReport:
    """Run every case and return the report. ``timestamp`` defaults to the current UTC time."""
    results = [
        run_case(
            case,
            settings=settings,
            provider=provider,
            judge_provider=judge_provider,
            eval_dir=eval_dir,
            workdir=workdir,
        )
        for case in cases
    ]
    judge_name = _judge_name(provider, judge_provider, settings)
    return EvalReport.build(
        results,
        timestamp=timestamp or datetime.now(UTC),
        provider=provider.name if provider is not None else "mock",
        model=provider.model if provider is not None else None,
        judge=judge_name,
    )


def _judge_name(
    provider: LLMProvider | None, judge_provider: LLMProvider | None, settings: Settings
) -> str:
    chosen = judge_provider or provider
    if is_offline(chosen):
        return "skipped"
    assert chosen is not None
    kind = "independent" if _independent(provider, judge_provider, settings) else "generator"
    return f"{chosen.name}:{chosen.model} ({kind})"


def _independent(
    provider: LLMProvider | None, judge_provider: LLMProvider | None, settings: Settings
) -> bool:
    return (
        judge_provider is not None and judge_provider is not provider and settings.judge_configured
    )


def _run(
    case: EvalCase,
    settings: Settings,
    provider: LLMProvider | None,
    judge_provider: LLMProvider | None,
    eval_dir: Path,
    workdir: Path,
) -> CaseResult:
    offline = is_offline(provider)
    metered = None if offline or provider is None else MeteredProvider(provider)
    notes: list[str] = []
    latency: dict[str, float] = {}
    case_dir = Path(workdir) / case.id

    def stage(name: str) -> None:
        if metered is not None:
            metered.stage = name

    def timed(name: str, started: float) -> None:
        latency[name] = round(time.perf_counter() - started, 3)

    started = time.perf_counter()
    document = ingest(source_path(case, eval_dir), image_dir=case_dir / "images")
    document = _restrict_pages(document, case.pages, notes)
    timed("ingest", started)
    sources = {document.doc_id: document}
    curriculum = load_curriculum(settings.curriculum_dir, case.curriculum.version, case.grade)

    stage("outline")
    started = time.perf_counter()
    plan = outline(
        [document],
        curriculum,
        unit_id=case.curriculum.unit_id,
        lesson_no=case.curriculum.lesson_no,
        period=case.curriculum.period,
        grade=case.grade,
        provider=metered,
    )
    timed("outline", started)

    open_stage: list[tuple[str, float]] = []

    def on_stage(name: str) -> None:
        """Close the running pipeline stage and open ``name`` for timing and token tallies."""
        if open_stage:
            timed(*open_stage.pop())
        stage(name)
        open_stage.append((name, time.perf_counter()))

    lesson = build_lesson(plan, sources, curriculum, metered, repair=True, on_stage=on_stage)
    if open_stage:
        timed(*open_stage.pop())
    drafted = lesson

    stage("edits")
    started = time.perf_counter()
    lesson, edits, edit_note = _run_edits(case, lesson, sources, curriculum, metered)
    timed("edits", started)

    judge_source = judge_provider or provider
    judge_meter = (
        None if is_offline(judge_source) or judge_source is None else MeteredProvider(judge_source)
    )
    started = time.perf_counter()
    if judge_meter is not None:
        judge_meter.stage = "judge"
    judge = _judge(drafted, sources, judge_meter, _independent(provider, judge_provider, settings))
    timed("judge", started)

    metrics = compute_metrics(
        drafted, curriculum, case.expect, edits=edits, edit_note=edit_note, judge=judge
    )
    _add_usage(metrics, metered, judge_meter, latency)
    if edit_note:
        notes.append(edit_note)
    if judge.note:
        notes.append(f"judge: {judge.note}")

    LessonStore(case_dir).save(lesson)
    SourceStore(case_dir).save(document)
    return CaseResult(
        case_id=case.id,
        grade=case.grade,
        status="ok",
        metrics=metrics,
        edits=edits,
        judge_justifications=judge.justifications(),
        notes=notes,
        lesson_path=str(LessonStore(case_dir).path_for(lesson.id)),
    )


def _restrict_pages(
    document: SourceDocument, pages: list[int] | None, notes: list[str]
) -> SourceDocument:
    """Keep only the listed pages; the ingest API reads whole files, so filter afterwards."""
    if not pages:
        return document
    kept = [page for page in document.pages if page.number in set(pages)]
    if not kept:
        notes.append(f"pages {pages} not found in the source; all pages used")
        return document
    notes.append(f"ingested all pages, restricted to {sorted(page.number for page in kept)}")
    return document.model_copy(update={"pages": kept})


def _judge(
    lesson: Lesson,
    sources: dict[str, SourceDocument],
    judge: MeteredProvider | None,
    independent: bool,
) -> JudgeScores:
    try:
        return judge_lesson(lesson, sources, judge, independent=independent)
    except LLMError as exc:
        return JudgeScores(source="skipped", note=f"judge failed: {exc}")


def _add_usage(
    metrics: CaseMetrics,
    generator: MeteredProvider | None,
    judge: MeteredProvider | None,
    latency: dict[str, float],
) -> None:
    metrics.latency_s = latency
    if generator is not None:
        metrics.tokens_in, metrics.tokens_out, metrics.cached_calls = generator.totals()
        metrics.cost_usd = generator.cost_usd
    if judge is not None:
        metrics.judge_tokens_in, metrics.judge_tokens_out, _ = judge.totals()
        metrics.judge_cost_usd = judge.cost_usd


def _run_edits(
    case: EvalCase,
    lesson: Lesson,
    sources: dict[str, SourceDocument],
    curriculum: Curriculum,
    provider: MeteredProvider | None,
) -> tuple[Lesson, list[EditResult], str | None]:
    if not case.edits:
        return lesson, [], None
    if provider is None:
        skipped = [
            EditResult(
                target_kind=edit.target_kind,
                instruction=edit.instruction,
                status="skipped",
                reason=OFFLINE_EDIT_REASON,
            )
            for edit in case.edits
        ]
        return lesson, skipped, f"edits skipped: {OFFLINE_EDIT_REASON}"
    engine = EditEngine(
        provider, sources=sources, curriculum=curriculum, grounding_provider=provider
    )
    results: list[EditResult] = []
    for edit in case.edits:
        target = _first_block(lesson, edit.target_kind)
        result = EditResult(
            target_kind=edit.target_kind, instruction=edit.instruction, status="skipped"
        )
        if target is None:
            result.reason = f"no block of kind {edit.target_kind} in the lesson"
            results.append(result)
            continue
        result.target_block_ids = [target.id]
        try:
            proposal = engine.propose(
                lesson, EditRequest(target_block_ids=[target.id], instruction=edit.instruction)
            )
            after = engine.accept(lesson, proposal)
        except (EditRejected, LLMError) as exc:
            result.status = "rejected"
            result.reason = str(exc)
            result.success = False
        else:
            changed = changed_block_ids(lesson, after)
            allowed = {
                target.id,
                *(added.block.id for added in proposal.added_blocks),
                *proposal.removed_block_ids,
            }
            result.status = "applied"
            result.changed_block_ids = changed
            result.affected_kinds = sorted(
                {
                    after.blocks[a.block_id].kind
                    for a in proposal.affected
                    if a.block_id in after.blocks
                }
            )
            result.locality = set(changed) <= allowed
            result.success = not has_failures(after)
            lesson = after
        result.expectations = evaluate_edit_expectations(
            edit, result, lesson if result.status == "applied" else None
        )
        results.append(result)
    return lesson, results, None


def _first_block(lesson: Lesson, kind: str) -> Block | None:
    return next((block for block in lesson.ordered_blocks() if block.kind == kind), None)


def changed_block_ids(before: Lesson, after: Lesson) -> list[str]:
    """Ids of blocks added, removed or changed, comparing content and structure only.

    Checks, citations and provenance are re-derived for the whole lesson after every edit, so
    they are left out of the comparison.
    """
    changed = []
    for block_id in [*before.blocks, *(b for b in after.blocks if b not in before.blocks)]:
        old, new = before.blocks.get(block_id), after.blocks.get(block_id)
        if old is None or new is None or _dump(old) != _dump(new):
            changed.append(block_id)
    return changed


def _dump(block: Block) -> dict[str, Any]:
    return block.model_dump(mode="json", exclude=_UNCOMPARED_FIELDS)
