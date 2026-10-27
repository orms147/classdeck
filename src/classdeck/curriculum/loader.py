"""Load a curriculum file and answer lookups over it.

``load_curriculum`` reads one ``(version, grade)`` YAML file and validates it;
:class:`Curriculum` indexes the result for unit / outcome lookup, the word list, the
forbidden-grammar rules and the keyword search used by the outliner.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from classdeck.curriculum.models import CurriculumFile, GrammarRule, Outcome, Unit

_SUBJECT_DIR = "tieng-anh"
_GRADE_FILE = re.compile(r"^lop-(\d+)$")
_TOKEN = re.compile(r"\w+")


class CurriculumNotFound(FileNotFoundError):
    """No curriculum file exists for the requested version and grade."""


def curriculum_path(root: Path, version: str, grade: int) -> Path:
    return root / version / _SUBJECT_DIR / f"lop-{grade}.yaml"


def load_curriculum(root: Path, version: str, grade: int) -> Curriculum:
    path = curriculum_path(root, version, grade)
    if not path.is_file():
        raise CurriculumNotFound(f"no curriculum for version {version!r}, grade {grade}: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    file = CurriculumFile.model_validate(data)
    if file.version != version or file.grade != grade:
        raise ValueError(
            f"{path} declares version {file.version!r}, grade {file.grade}; "
            f"expected version {version!r}, grade {grade}"
        )
    return Curriculum(file)


def list_available(root: Path) -> list[tuple[str, int]]:
    """Every ``(version, grade)`` that has a file under ``root``, sorted."""
    found: list[tuple[str, int]] = []
    for path in root.glob(f"*/{_SUBJECT_DIR}/lop-*.yaml"):
        match = _GRADE_FILE.match(path.stem)
        if match:
            found.append((path.parent.parent.name, int(match.group(1))))
    return sorted(found)


class Curriculum:
    """Read-only lookups over a validated :class:`CurriculumFile`."""

    def __init__(self, file: CurriculumFile) -> None:
        self.file = file
        self._units = {unit.id: unit for unit in file.units}
        self._outcomes = {
            outcome.id: (unit, outcome) for unit in file.units for outcome in unit.outcomes
        }
        self._grade_words = frozenset(
            word.lower()
            for word in [*self.file.vocabulary.words, *self.file.vocabulary.classroom_language]
        )

    @property
    def period_minutes(self) -> int:
        return self.file.period_minutes

    def unit(self, unit_id: str) -> Unit:
        try:
            return self._units[unit_id]
        except KeyError:
            raise KeyError(
                f"unknown unit id {unit_id!r}; known units: {', '.join(self._units)}"
            ) from None

    def outcome(self, outcome_id: str) -> Outcome:
        return self._lookup_outcome(outcome_id)[1]

    def unit_of_outcome(self, outcome_id: str) -> Unit:
        return self._lookup_outcome(outcome_id)[0]

    def word_list(self, unit_id: str | None = None) -> frozenset[str]:
        """The grade's words (including classroom language), plus the unit's own words when
        ``unit_id`` is given."""
        if unit_id is None:
            return self._grade_words
        unit_words = {word.lower() for word in self.unit(unit_id).vocabulary}
        return self._grade_words | unit_words

    def forbidden_rules(self) -> list[GrammarRule]:
        return list(self.file.grammar_scope.forbidden)

    def search_outcomes(
        self, query: str, *, unit_id: str | None = None, limit: int = 5
    ) -> list[Outcome]:
        """Outcomes ranked by how many distinct query tokens appear in their text or keywords."""
        query_tokens = list(dict.fromkeys(_TOKEN.findall(query.lower())))
        units = [self.unit(unit_id)] if unit_id is not None else self.file.units
        scored: list[tuple[int, Outcome]] = []
        for unit in units:
            for outcome in unit.outcomes:
                tokens = set(_TOKEN.findall(" ".join([outcome.text, *outcome.keywords]).lower()))
                score = sum(1 for token in query_tokens if token in tokens)
                if score > 0:
                    scored.append((score, outcome))
        scored.sort(key=lambda pair: -pair[0])
        return [outcome for _, outcome in scored[: max(limit, 0)]]

    def _lookup_outcome(self, outcome_id: str) -> tuple[Unit, Outcome]:
        try:
            return self._outcomes[outcome_id]
        except KeyError:
            raise KeyError(f"unknown outcome id {outcome_id!r}") from None
