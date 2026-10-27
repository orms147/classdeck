"""Pydantic models for the curriculum YAML files.

One file describes one ``(version, grade)``. The format and ID policy are documented in
``docs/curriculum-config.md``; this module is the machine-checked version of that document.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Skill = Literal["listening", "speaking", "reading", "writing"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GrammarRule(_Strict):
    """A forbidden construction, expressed as a case-insensitive regular expression."""

    id: str
    pattern: str
    note: str | None = None

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        try:
            re.compile(value, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regular expression {value!r}: {exc}") from exc
        return value

    @cached_property
    def regex(self) -> re.Pattern[str]:
        return re.compile(self.pattern, re.IGNORECASE)


class GrammarScope(_Strict):
    allowed: list[str] = []
    forbidden: list[GrammarRule] = []


class VocabularyScope(_Strict):
    level: str
    words: list[str]
    # Instruction language pupils hear every lesson (listen, point, partner, …): receptive,
    # not taught as new words, but allowed wherever the word list is enforced.
    classroom_language: list[str] = []


class TextbookRef(_Strict):
    series: str
    volume: int | None = None


class TextbookUnitRef(_Strict):
    series: str
    unit: int


class Pattern(_Strict):
    pattern: str
    function: str | None = None


class PhonicsItem(_Strict):
    sound: str
    letters: str
    words: list[str]


class Outcome(_Strict):
    id: str
    text: str
    skills: list[Skill] = []
    keywords: list[str] = []


class Unit(_Strict):
    id: str
    title: str
    textbook_units: list[TextbookUnitRef] = []
    lessons: int = Field(default=3, ge=1)
    outcomes: list[Outcome]
    vocabulary: list[str] = []
    patterns: list[Pattern] = []
    phonics: list[PhonicsItem] = []


class CurriculumFile(_Strict):
    version: str
    subject: Literal["tieng-anh"]
    grade: int = Field(ge=3, le=5)
    language: str = "en"
    status: Literal["seed", "verified"] = "seed"
    period_minutes: int = Field(default=35, gt=0)
    vocabulary: VocabularyScope
    grammar_scope: GrammarScope = Field(default_factory=GrammarScope)
    textbooks: list[TextbookRef] = []
    units: list[Unit]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        unit_ids: set[str] = set()
        outcome_ids: set[str] = set()
        for unit in self.units:
            if unit.id in unit_ids:
                raise ValueError(f"duplicate unit id: {unit.id}")
            unit_ids.add(unit.id)
            for outcome in unit.outcomes:
                if outcome.id in outcome_ids:
                    raise ValueError(f"duplicate outcome id: {outcome.id}")
                outcome_ids.add(outcome.id)
        return self
