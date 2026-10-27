"""Curriculum config: YAML schema and loader. See ``docs/curriculum-config.md``."""

from __future__ import annotations

from classdeck.curriculum.loader import (
    Curriculum,
    CurriculumNotFound,
    curriculum_path,
    list_available,
    load_curriculum,
)
from classdeck.curriculum.models import (
    CurriculumFile,
    GrammarRule,
    GrammarScope,
    Outcome,
    Pattern,
    PhonicsItem,
    Skill,
    TextbookRef,
    TextbookUnitRef,
    Unit,
    VocabularyScope,
)

__all__ = [
    "Curriculum",
    "CurriculumFile",
    "CurriculumNotFound",
    "GrammarRule",
    "GrammarScope",
    "Outcome",
    "Pattern",
    "PhonicsItem",
    "Skill",
    "TextbookRef",
    "TextbookUnitRef",
    "Unit",
    "VocabularyScope",
    "curriculum_path",
    "list_available",
    "load_curriculum",
]
