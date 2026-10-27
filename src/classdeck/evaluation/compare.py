"""Compare two results files: per-case metric deltas and the summary delta."""

from __future__ import annotations

import sys
from pathlib import Path

from classdeck.evaluation.metrics import CaseMetrics
from classdeck.evaluation.report import MEAN_METRICS, EvalReport

COMPARED = (
    "grounded_ratio",
    "ai_added_ratio",
    "vocab_violations",
    "grammar_violations",
    "skill_coverage",
    "outcome_coverage",
    "edit_locality",
    "edit_success",
    "judge_mean",
    "tokens_in",
    "tokens_out",
    "cost_usd",
)


def compare(a: Path, b: Path) -> str:
    """A text diff of two results files, ``b`` relative to ``a``."""
    first, second = EvalReport.read(Path(a)), EvalReport.read(Path(b))
    lines = [f"A: {Path(a).name}  ({first.provider}, {first.timestamp})"]
    lines.append(f"B: {Path(b).name}  ({second.provider}, {second.timestamp})")
    ids = [case.case_id for case in first.cases]
    ids += [case.case_id for case in second.cases if case.case_id not in ids]
    for case_id in ids:
        lines.append("")
        lines.append(case_id)
        old, new = first.result_for(case_id), second.result_for(case_id)
        if old is None or new is None:
            lines.append(f"  only in {'B' if old is None else 'A'}")
            continue
        if old.metrics is None or new.metrics is None:
            lines.append(f"  A: {old.status}  B: {new.status}")
            continue
        lines.extend(_metric_lines(old.metrics, new.metrics))
        if old.metrics.passed != new.metrics.passed:
            lines.append(f"  passed: {old.metrics.passed} -> {new.metrics.passed}")
    lines.append("")
    lines.append("summary")
    for name in MEAN_METRICS:
        key = f"mean_{name}"
        delta = _delta(first.summary.get(key), second.summary.get(key))
        if delta is not None:
            lines.append(f"  {key:<28}{delta}")
    for key in ("ran", "skipped", "errors", "passed"):
        delta = _delta(first.summary.get(key), second.summary.get(key))
        if delta is not None:
            lines.append(f"  {key:<28}{delta}")
    return "\n".join(lines)


def _metric_lines(old: CaseMetrics, new: CaseMetrics) -> list[str]:
    lines = []
    for name in COMPARED:
        delta = _delta(getattr(old, name), getattr(new, name))
        if delta is not None:
            lines.append(f"  {name:<24}{delta}")
    return lines or ["  (no comparable metrics)"]


def _delta(old: object, new: object) -> str | None:
    if old is None and new is None:
        return None
    if not isinstance(old, int | float) or not isinstance(new, int | float):
        return f"{_fmt(old)} -> {_fmt(new)}"
    change = new - old
    sign = "+" if change > 0 else ""
    return f"{_fmt(old)} -> {_fmt(new)}  ({sign}{_fmt(change)})"


def _fmt(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: compare.py <results-a.json> <results-b.json>", file=sys.stderr)
        return 2
    print(compare(Path(args[0]), Path(args[1])))
    return 0
