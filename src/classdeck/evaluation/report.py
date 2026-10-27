"""The results file: per-case results plus a summary row, as JSON and as a plain-text table."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from classdeck.evaluation.metrics import CaseMetrics, EditResult

CaseStatus = Literal["ok", "skipped", "error"]

MEAN_METRICS = (
    "grounded_ratio",
    "ai_added_ratio",
    "vocab_violations",
    "grammar_violations",
    "skill_coverage",
    "outcome_coverage",
    "edit_locality",
    "edit_success",
    "judge_faithfulness",
    "judge_age_fit",
    "judge_step_clarity",
    "judge_outcome_alignment",
    "judge_flow",
    "judge_mean",
    "tokens_in",
    "tokens_out",
    "cost_usd",
)
"""Metrics averaged over the cases that ran; a case where one is ``None`` is left out of it."""


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    grade: int
    status: CaseStatus
    reason: str | None = None
    """Why a case was skipped or failed."""
    metrics: CaseMetrics | None = None
    edits: list[EditResult] = []
    judge_justifications: dict[str, str] = {}
    notes: list[str] = []
    lesson_path: str | None = None


class EvalReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp: str
    provider: str
    model: str | None = None
    judge: str | None = None
    cases: list[CaseResult] = []
    summary: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def build(
        cls,
        cases: list[CaseResult],
        *,
        timestamp: datetime | str,
        provider: str,
        model: str | None = None,
        judge: str | None = None,
    ) -> EvalReport:
        stamp = timestamp if isinstance(timestamp, str) else timestamp.isoformat(timespec="seconds")
        return cls(
            timestamp=stamp,
            provider=provider,
            model=model,
            judge=judge,
            cases=cases,
            summary=summarize(cases),
        )

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)

    @classmethod
    def from_json(cls, text: str) -> EvalReport:
        return cls.model_validate(json.loads(text))

    @classmethod
    def read(cls, path: Path) -> EvalReport:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def write(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path

    def result_for(self, case_id: str) -> CaseResult | None:
        return next((case for case in self.cases if case.case_id == case_id), None)

    def table(self) -> str:
        return render_table(self)


def summarize(cases: list[CaseResult]) -> dict[str, Any]:
    ran = [case for case in cases if case.status == "ok" and case.metrics is not None]
    summary: dict[str, Any] = {
        "cases": len(cases),
        "ran": len(ran),
        "skipped": sum(case.status == "skipped" for case in cases),
        "errors": sum(case.status == "error" for case in cases),
        "passed": sum(bool(case.metrics and case.metrics.passed) for case in ran),
    }
    for name in MEAN_METRICS:
        values = [
            value
            for case in ran
            if case.metrics is not None
            and isinstance(value := getattr(case.metrics, name), int | float)
        ]
        summary[f"mean_{name}"] = round(sum(values) / len(values), 4) if values else None
    return summary


_COLUMNS: tuple[tuple[str, str], ...] = (
    ("ground", "grounded_ratio"),
    ("vocab", "vocab_violations"),
    ("gram", "grammar_violations"),
    ("skill", "skill_coverage"),
    ("outc", "outcome_coverage"),
    ("time", "timing_ok"),
    ("e.loc", "edit_locality"),
    ("e.ok", "edit_success"),
    ("judge", "judge_mean"),
)


def _cell(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_table(report: EvalReport) -> str:
    headers = ["case", "status", *(label for label, _ in _COLUMNS), "pass"]
    rows: list[list[str]] = []
    for case in report.cases:
        row = [case.case_id, case.status]
        for _, name in _COLUMNS:
            row.append(_cell(getattr(case.metrics, name) if case.metrics else None))
        row.append(_cell(case.metrics.passed) if case.metrics else "-")
        rows.append(row)
    summary = report.summary
    mean_row = ["mean", f"{summary.get('ran', 0)}/{summary.get('cases', 0)} ran"]
    for _, name in _COLUMNS:
        mean_row.append(_cell(summary.get(f"mean_{name}")) if name != "timing_ok" else "-")
    mean_row.append(f"{summary.get('passed', 0)}/{summary.get('ran', 0)}")
    widths = [max(len(r[i]) for r in [headers, *rows, mean_row]) for i in range(len(headers))]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    rule = "  ".join("-" * width for width in widths)
    lines = [line(headers), rule, *(line(r) for r in rows), rule, line(mean_row)]
    for case in report.cases:
        if case.reason:
            lines.append(f"{case.case_id}: {case.status}: {case.reason}")
        if case.metrics:
            failed = [key for key, ok in case.metrics.expectations.items() if not ok]
            if failed:
                lines.append(f"{case.case_id}: failed expectations: {', '.join(failed)}")
        lines.extend(f"{case.case_id}: {note}" for note in case.notes)
    judge = report.judge or "-"
    lines.append(f"provider={report.provider}  judge={judge}  at {report.timestamp}")
    return "\n".join(lines)
