"""Evaluation cases: the YAML format of ``eval/cases/*.yaml`` (``docs/evaluation.md``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseCurriculum(_Strict):
    version: str
    unit_id: str
    lesson_no: int = Field(ge=1)
    period: int = Field(ge=1)


class CaseExpect(_Strict):
    """Pass/fail thresholds for one case; a key left out is not checked."""

    min_grounded_ratio: float | None = Field(default=None, ge=0, le=1)
    max_vocab_violations: int | None = Field(default=None, ge=0)
    max_grammar_violations: int | None = Field(default=None, ge=0)
    must_cover_outcomes: list[str] = []
    must_cover_skills: list[str] = []
    timing_ok: bool | None = None


class EditCase(_Strict):
    """One scripted edit: the first block of ``target_kind`` and the teacher's instruction."""

    target_kind: str
    instruction: str = Field(min_length=1)
    expect: dict[str, Any] = {}


class EvalCase(_Strict):
    id: str = Field(min_length=1)
    grade: int = Field(ge=3, le=5)
    curriculum: CaseCurriculum
    source: str = Field(min_length=1)
    """Path of the source file, relative to the ``eval/`` directory."""
    pages: list[int] | None = None
    expect: CaseExpect = CaseExpect()
    edits: list[EditCase] = []
    skipped_reason: str | None = None
    """Set by :func:`load_cases` when the case cannot run, for example a missing fixture."""


def source_path(case: EvalCase, eval_dir: Path) -> Path:
    return Path(eval_dir) / case.source


def missing_fixture_reason(case: EvalCase, eval_dir: Path) -> str | None:
    path = source_path(case, eval_dir)
    if path.is_file():
        return None
    return f"missing fixture: {case.source}"


def load_case(path: Path) -> EvalCase:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return EvalCase.model_validate(data)


def load_cases(path: Path, *, eval_dir: Path | None = None) -> list[EvalCase]:
    """Load one case file, or every ``*.yaml`` in a directory (sorted by file name).

    ``eval_dir`` is where ``source`` paths resolve; by default the parent of the ``cases``
    directory. A case whose source file does not exist is kept, with ``skipped_reason`` set.
    """
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.yaml"))
        base = path.parent
    else:
        files = [path]
        base = path.parent.parent
    base = Path(eval_dir) if eval_dir is not None else base
    cases = []
    for file in files:
        case = load_case(file)
        case.skipped_reason = missing_fixture_reason(case, base)
        cases.append(case)
    return cases
