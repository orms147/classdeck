"""Per-case metrics (``docs/evaluation.md#metrics``), computed from a finished lesson."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from classdeck.curriculum import Curriculum
from classdeck.english.validators import GRAMMAR_SCOPE, TIMING, VOCAB_LEVEL
from classdeck.evaluation.cases import CaseExpect, EditCase
from classdeck.evaluation.judge import CRITERIA, JudgeScores
from classdeck.grounding import ai_added_ratio, grounded_ratio
from classdeck.lesson import ActivityBlock, CheckStatus, Lesson, ObjectiveBlock

_OFFENDERS = re.compile(r":\s*(.+)$")
_SKILL_ORDER = ("listening", "speaking", "reading", "writing")


class EditResult(BaseModel):
    """What happened to one scripted edit."""

    model_config = ConfigDict(extra="forbid")

    target_kind: str
    instruction: str
    status: str
    """``applied``, ``rejected`` or ``skipped``."""
    reason: str | None = None
    target_block_ids: list[str] = []
    changed_block_ids: list[str] = []
    affected_kinds: list[str] = []
    locality: bool | None = None
    success: bool | None = None
    expectations: dict[str, bool] = {}


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grounded_ratio: float | None = None
    ai_added_ratio: float | None = None
    vocab_violations: int | None = None
    grammar_violations: int | None = None
    skill_coverage: float | None = None
    outcome_coverage: float | None = None
    covered_skills: list[str] = []
    covered_outcomes: list[str] = []
    timing_ok: bool | None = None

    edit_locality: float | None = None
    edit_success: float | None = None
    edit_note: str | None = None

    judge_source: str | None = None
    judge_faithfulness: int | None = None
    judge_age_fit: int | None = None
    judge_step_clarity: int | None = None
    judge_outcome_alignment: int | None = None
    judge_flow: int | None = None
    judge_mean: float | None = None

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    judge_tokens_in: int = 0
    judge_tokens_out: int = 0
    judge_cost_usd: float | None = None
    cached_calls: int = 0
    latency_s: dict[str, float] = Field(default_factory=dict)

    expectations: dict[str, bool] = {}
    passed: bool = True


def _skill_names(skills: Iterable[Any]) -> set[str]:
    return {str(getattr(skill, "value", skill)) for skill in skills}


def sort_skills(skills: Iterable[str]) -> list[str]:
    order = {name: index for index, name in enumerate(_SKILL_ORDER)}
    return sorted(skills, key=lambda name: (order.get(name, len(order)), name))


def count_offenders(lesson: Lesson, check_name: str, separator: str) -> int:
    """Sum the offenders listed in the details of the named check's ``warn``/``fail`` results.

    The validators report surface words (vocabulary, joined by ``", "``) and rule hits
    (grammar, joined by ``"; "``) in the check detail, so that is what is counted.
    """
    total = 0
    for block in lesson.blocks.values():
        for check in block.checks:
            if check.name != check_name or check.status is CheckStatus.PASS or not check.detail:
                continue
            if separator == ", ":
                found = _OFFENDERS.search(check.detail)
                text = found.group(1) if found else check.detail
            else:
                text = check.detail.split(" - ", 1)[-1]
            total += len([part for part in text.split(separator) if part.strip()])
    return total


def covered_skills(lesson: Lesson) -> set[str]:
    present: set[str] = set()
    for block in lesson.blocks.values():
        if isinstance(block, ActivityBlock):
            present |= _skill_names(block.content.skills)
    return present


def outcome_status(lesson: Lesson, curriculum: Curriculum) -> dict[str, bool]:
    """Per outcome of the period: has an objective naming it and an activity for its skills."""
    referenced = {
        outcome_id
        for block in lesson.blocks.values()
        if isinstance(block, ObjectiveBlock)
        for outcome_id in block.content.outcome_ids
    }
    activities = [b for b in lesson.blocks.values() if isinstance(b, ActivityBlock)]
    status: dict[str, bool] = {}
    for outcome_id in lesson.curriculum.outcome_ids:
        try:
            skills = _skill_names(curriculum.outcome(outcome_id).skills)
        except KeyError:
            status[outcome_id] = False
            continue
        practised = any(not skills or skills & _skill_names(a.content.skills) for a in activities)
        status[outcome_id] = outcome_id in referenced and practised
    return status


def required_skills(lesson: Lesson, curriculum: Curriculum) -> set[str]:
    required: set[str] = set()
    for outcome_id in lesson.curriculum.outcome_ids:
        try:
            required |= _skill_names(curriculum.outcome(outcome_id).skills)
        except KeyError:
            continue
    return required


def lesson_timing_ok(lesson: Lesson) -> bool | None:
    for check in lesson.checks:
        if check.name == TIMING:
            return check.status is CheckStatus.PASS
    return None


def evaluate_expectations(
    expect: CaseExpect, metrics: CaseMetrics, edits: list[EditResult]
) -> dict[str, bool]:
    """Each ``expect`` key that can be evaluated, as pass/fail.

    Edit expectations appear as ``edit[<n>].<key>`` and only for edits that actually ran.
    """
    result: dict[str, bool] = {}
    if expect.min_grounded_ratio is not None:
        result["min_grounded_ratio"] = (metrics.grounded_ratio or 0.0) >= expect.min_grounded_ratio
    if expect.max_vocab_violations is not None:
        found = metrics.vocab_violations or 0
        result["max_vocab_violations"] = found <= expect.max_vocab_violations
    if expect.max_grammar_violations is not None:
        found = metrics.grammar_violations or 0
        result["max_grammar_violations"] = found <= expect.max_grammar_violations
    if expect.must_cover_outcomes:
        result["must_cover_outcomes"] = set(expect.must_cover_outcomes) <= set(
            metrics.covered_outcomes
        )
    if expect.must_cover_skills:
        result["must_cover_skills"] = set(expect.must_cover_skills) <= set(metrics.covered_skills)
    if expect.timing_ok is not None:
        result["timing_ok"] = metrics.timing_ok == expect.timing_ok
    for index, edit in enumerate(edits):
        for key, passed in edit.expectations.items():
            result[f"edit[{index}].{key}"] = passed
    return result


def evaluate_edit_expectations(
    edit: EditCase, result: EditResult, after: Lesson | None
) -> dict[str, bool]:
    """Check an applied or rejected edit against ``edit.expect``. Unknown keys fail."""
    if result.status == "skipped":
        return {}
    checks: dict[str, bool] = {}
    for key, wanted in edit.expect.items():
        if result.status == "rejected":
            checks[key] = False
        elif key == "affected_includes_kind":
            checks[key] = str(wanted) in result.affected_kinds
        elif key == "edit_locality":
            checks[key] = result.locality is bool(wanted)
        elif key == "changed_blocks":
            checks[key] = len(result.changed_block_ids) == int(wanted)
        elif key == "interaction":
            checks[key] = _has_interaction(after, result.changed_block_ids, str(wanted))
        elif key == "checks_pass_after_regenerate":
            checks[key] = result.success is bool(wanted)
        else:
            checks[key] = False
    return checks


def _has_interaction(lesson: Lesson | None, block_ids: list[str], wanted: str) -> bool:
    if lesson is None:
        return False
    for block_id in block_ids:
        block = lesson.blocks.get(block_id)
        if isinstance(block, ActivityBlock) and block.content.interaction.value == wanted:
            return True
    return False


def compute_metrics(
    lesson: Lesson,
    curriculum: Curriculum,
    expect: CaseExpect,
    *,
    edits: list[EditResult] | None = None,
    edit_note: str | None = None,
    judge: JudgeScores | None = None,
) -> CaseMetrics:
    """Lesson-derived metrics and expectations; usage and latency are filled in by the runner."""
    edits = edits or []
    required = required_skills(lesson, curriculum)
    present = covered_skills(lesson)
    status = outcome_status(lesson, curriculum)
    metrics = CaseMetrics(
        grounded_ratio=round(grounded_ratio(lesson), 4),
        ai_added_ratio=round(ai_added_ratio(lesson), 4),
        vocab_violations=count_offenders(lesson, VOCAB_LEVEL, ", "),
        grammar_violations=count_offenders(lesson, GRAMMAR_SCOPE, "; "),
        skill_coverage=len(required & present) / len(required) if required else 1.0,
        outcome_coverage=sum(status.values()) / len(status) if status else 1.0,
        covered_skills=sort_skills(present),
        covered_outcomes=[outcome_id for outcome_id, ok in status.items() if ok],
        timing_ok=lesson_timing_ok(lesson),
        edit_note=edit_note,
    )
    ran = [edit for edit in edits if edit.status != "skipped"]
    if ran:
        local = [edit.locality for edit in ran if edit.locality is not None]
        metrics.edit_locality = sum(local) / len(local) if local else None
        metrics.edit_success = sum(bool(edit.success) for edit in ran) / len(ran)
    if judge is not None:
        metrics.judge_source = judge.source
        scores = judge.scores()
        for name in CRITERIA:
            setattr(metrics, f"judge_{name}", scores.get(name))
        metrics.judge_mean = judge.mean
    metrics.expectations = evaluate_expectations(expect, metrics, ran)
    metrics.passed = all(metrics.expectations.values())
    return metrics
