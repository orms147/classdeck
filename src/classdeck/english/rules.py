"""Primary-English lesson rules: phases, lesson shapes, block kinds and prompt fragments.

The rest of the pipeline reads these rules instead of hard-coding them; the rationale for each
is in ``docs/english-rules.md``.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources

import yaml
from pydantic import Field

from classdeck.curriculum.loader import Curriculum
from classdeck.lesson.schema import (
    ActivityType,
    BlockKind,
    Interaction,
    Phase,
    Skill,
    Strict,
)

PERIOD_MINUTES_TOLERANCE = 2
GROUNDING_THRESHOLDS: tuple[float, float] = (0.20, 0.60)
"""``(LOW, HIGH)`` lexical-overlap thresholds for citation verification."""

CONTENT_LANGUAGE = "en"

DRAFTABLE_BLOCK_KINDS: frozenset[BlockKind] = frozenset(BlockKind)
NON_CONTENT_KINDS: frozenset[BlockKind] = frozenset(
    {BlockKind.OBJECTIVE, BlockKind.TEACHER_NOTE, BlockKind.SUMMARY}
)


# --- period structure --------------------------------------------------------------------------


class PhaseSpec(Strict):
    phase: Phase
    default_minutes: int = Field(ge=0)
    description: str


PHASES: list[PhaseSpec] = [
    PhaseSpec(
        phase=Phase.WARM_UP,
        default_minutes=5,
        description="Review game, song or chant from the previous lesson; lead-in to the topic.",
    ),
    PhaseSpec(
        phase=Phase.PRESENTATION,
        default_minutes=10,
        description="Look, listen and repeat: dialogue, new vocabulary, target pattern.",
    ),
    PhaseSpec(
        phase=Phase.PRACTICE,
        default_minutes=12,
        description=(
            "Controlled practice: Listen, point and say; Listen and tick; Look, complete and read."
        ),
    ),
    PhaseSpec(
        phase=Phase.PRODUCTION,
        default_minutes=6,
        description="Freer use of the pattern: Let's talk, role play, game, project.",
    ),
    PhaseSpec(
        phase=Phase.WRAP_UP,
        default_minutes=2,
        description="Recap, homework, praise.",
    ),
]

_AT = ActivityType

_LESSON_1_2_PERIOD_1 = [_AT.LOOK_LISTEN_REPEAT, _AT.LISTEN_POINT_SAY, _AT.LETS_TALK]
_LESSON_1_2_PERIOD_2 = [_AT.LISTEN_AND_DO, _AT.LOOK_COMPLETE_READ, _AT.SONG]

LESSON_SHAPES: dict[tuple[int, int], list[ActivityType]] = {
    (1, 1): list(_LESSON_1_2_PERIOD_1),
    (1, 2): list(_LESSON_1_2_PERIOD_2),
    (2, 1): list(_LESSON_1_2_PERIOD_1),
    (2, 2): list(_LESSON_1_2_PERIOD_2),
    (3, 1): [_AT.LOOK_LISTEN_REPEAT, _AT.LISTEN_AND_DO, _AT.CHANT],
    (3, 2): [_AT.READ_AND_MATCH, _AT.LETS_WRITE, _AT.PROJECT],
}
"""Activities of a Global Success period, keyed by ``(lesson_no, period)``.

In lesson 3, period 1 starts with the phonics *Listen and repeat* (``look_listen_repeat``).
"""

ALT_PRODUCTION: frozenset[ActivityType] = frozenset({_AT.SONG, _AT.GAME})
"""Activity 6 of lessons 1-2 is *Let's sing* or *Let's play*; ``song`` is the default shape."""

_PHASE_OF: dict[ActivityType, Phase] = {
    _AT.TPR: Phase.WARM_UP,
    _AT.LOOK_LISTEN_REPEAT: Phase.PRESENTATION,
    _AT.LISTEN_POINT_SAY: Phase.PRACTICE,
    _AT.LISTEN_AND_DO: Phase.PRACTICE,
    _AT.LOOK_COMPLETE_READ: Phase.PRACTICE,
    _AT.READ_AND_MATCH: Phase.PRACTICE,
    _AT.LETS_TALK: Phase.PRODUCTION,
    _AT.LETS_WRITE: Phase.PRODUCTION,
    _AT.SONG: Phase.PRODUCTION,
    _AT.CHANT: Phase.PRODUCTION,
    _AT.GAME: Phase.PRODUCTION,
    _AT.ROLE_PLAY: Phase.PRODUCTION,
    _AT.PROJECT: Phase.PRODUCTION,
    _AT.OTHER: Phase.PRACTICE,
}


def phase_for(activity_type: ActivityType) -> Phase:
    """The phase an activity of this type normally belongs to."""
    return _PHASE_OF[activity_type]


# --- prompt fragments --------------------------------------------------------------------------

_GRADE_SCOPE: dict[int, str] = {
    3: (
        "present simple with be and have, imperatives, this / that, What's ...? questions, "
        "numbers 1-20"
    ),
    4: (
        "the grade 3 scope plus can, present continuous, like + -ing, days of the week, "
        "months, Where / When questions"
    ),
    5: (
        "the grade 4 scope plus past simple (regular and common irregular verbs), going to, "
        "will for offers, because, comparatives"
    ),
}


def prompt_rules(grade: int, curriculum: Curriculum | None = None) -> str:
    """The rule fragment injected into the outliner, drafter and editor prompts.

    The language-scope line comes from the curriculum's ``grammar_scope.allowed`` when a
    curriculum with such a list is given, else from the built-in description of the grade.
    """
    if grade not in _GRADE_SCOPE:
        raise ValueError(f"grade must be 3, 4 or 5, got {grade}")
    allowed = curriculum.file.grammar_scope.allowed if curriculum is not None else []
    scope = "; ".join(allowed) if allowed else _GRADE_SCOPE[grade]
    rules = [
        "Vocabulary and sentence patterns come from the cited unit. New words beyond the source "
        "are allowed only with `sources: []` and only if they are in the grade word list.",
        "Every activity names its type, at least one skill, the interaction pattern, and "
        "separate teacher_steps and student_steps written as imperatives.",
        f"Language scope for grade {grade}: {scope}. Stay inside this scope.",
        "No perfect tenses, no passive voice and no reported speech at any grade.",
        "Instructions for pupils are short (at most 12 words), one action each.",
        "Picture-driven: when a vocabulary item has a picture on the page, reference the image "
        "span; do not describe the picture in words on the slide.",
        "Vietnamese is allowed only in meaning_vi and in teacher notes.",
        "Keep the textbook's wording: copy the unit's patterns and dialogue lines exactly, "
        "contractions included (write I'm, never I am), and do not introduce a pattern or phrase "
        "that the unit does not teach.",
        "The new pattern is taught from the presentation onward: a warm-up recycles earlier "
        "language or is a TPR in classroom language.",
        "Practice and production activities need concrete steps: teacher_steps say what to "
        "point at or show, who speaks first, how many rounds and how to check; student_steps are "
        "3 to 5 concrete imperatives. A listen_and_do or look_complete_read activity ends its "
        "teacher_steps with an 'Answer key: ...' step.",
    ]
    return "\n".join(f"- {rule}" for rule in rules)


# --- activity templates ------------------------------------------------------------------------


class ActivityTemplate(Strict):
    """Starting point for an activity of one type; the drafter fills the placeholders."""

    default_skills: list[Skill]
    default_interaction: Interaction
    default_minutes: int = Field(ge=1)
    title_template: str
    instructions_template: str
    teacher_steps: list[str]
    student_steps: list[str]


@lru_cache(maxsize=1)
def _read_templates() -> dict[ActivityType, ActivityTemplate]:
    resource = resources.files("classdeck.english").joinpath("templates", "activities.yaml")
    raw = yaml.safe_load(resource.read_text(encoding="utf-8"))
    entries = raw["templates"]
    templates = {
        ActivityType(key): ActivityTemplate.model_validate(v) for key, v in entries.items()
    }
    missing = [kind.value for kind in ActivityType if kind not in templates]
    if missing:
        raise ValueError(f"activities.yaml has no template for: {', '.join(missing)}")
    return {kind: templates[kind] for kind in ActivityType}


def load_activity_templates() -> dict[ActivityType, ActivityTemplate]:
    """One template per :class:`ActivityType`, read from ``templates/activities.yaml``."""
    return dict(_read_templates())
