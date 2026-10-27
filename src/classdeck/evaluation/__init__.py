"""Evaluation: fixed cases run end to end, scored by code and an LLM judge.

See ``docs/evaluation.md``.
"""

from __future__ import annotations

from classdeck.evaluation.cases import (
    CaseExpect,
    EditCase,
    EvalCase,
    load_case,
    load_cases,
)
from classdeck.evaluation.compare import compare
from classdeck.evaluation.judge import (
    CRITERIA,
    CriterionScore,
    JudgeRubric,
    JudgeScores,
    judge_lesson,
)
from classdeck.evaluation.metrics import CaseMetrics, EditResult, compute_metrics
from classdeck.evaluation.report import CaseResult, EvalReport
from classdeck.evaluation.runner import MeteredProvider, run_case, run_cases

__all__ = [
    "CRITERIA",
    "CaseExpect",
    "CaseMetrics",
    "CaseResult",
    "CriterionScore",
    "EditCase",
    "EditResult",
    "EvalCase",
    "EvalReport",
    "JudgeRubric",
    "JudgeScores",
    "MeteredProvider",
    "compare",
    "compute_metrics",
    "judge_lesson",
    "load_case",
    "load_cases",
    "run_case",
    "run_cases",
]
