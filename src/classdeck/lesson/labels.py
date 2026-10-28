"""How a lesson is named for teachers: ``Grade 9 · Lesson 1`` and ``Unit 1 · Local community``.

Internal ids (``ta9.u1``, block ids) stay in URLs, data attributes and the teacher-mode footer of
the deck; everything a teacher reads uses these labels (``docs/local-app.md``).
"""

from __future__ import annotations

import re

from classdeck.lesson.schema import CurriculumRef, Lesson

SEP = " · "
_UNIT_ID_NUMBER = re.compile(r"\.u(\d+)$")
_OLD_TITLE = re.compile(r"^Unit\s+\d+\s+(?P<title>.+?)\s+[-\u2013\u2014]\s+Lesson\b")


SECONDARY_LESSON_TYPES: dict[int, str] = {
    1: "Getting Started",
    2: "A Closer Look 1",
    3: "A Closer Look 2",
    4: "Communication",
    5: "Skills 1",
    6: "Skills 2",
    7: "Looking Back & Project",
}
"""The seven textbook lessons of a Global Success 6-9 unit, in teaching order."""

FIRST_SECONDARY_GRADE = 6


def lesson_label_for(grade: int, lesson_no: int | None, period: int | None) -> str:
    """``Grade 3 · Lesson 1 · Period 2``; the period only for primary lessons that have one.

    A lower-secondary lesson names its textbook type: ``Grade 9 · Lesson 7 · Looking Back &
    Project``.
    """
    parts = [f"Grade {grade}"]
    if lesson_no is not None:
        parts.append(f"Lesson {lesson_no}")
        if grade >= FIRST_SECONDARY_GRADE and lesson_no in SECONDARY_LESSON_TYPES:
            parts.append(SECONDARY_LESSON_TYPES[lesson_no])
    if period is not None:
        parts.append(f"Period {period}")
    return SEP.join(parts)


def lesson_label(lesson: Lesson) -> str:
    ref = lesson.curriculum
    return lesson_label_for(lesson.grade, ref.lesson_no, ref.period)


def unit_number(ref: CurriculumRef) -> int | None:
    if ref.unit_no is not None:
        return ref.unit_no
    match = _UNIT_ID_NUMBER.search(ref.unit_id)
    return int(match.group(1)) if match else None


def unit_title(lesson: Lesson) -> str:
    """The unit's name; for lessons saved before it was stored, the part of the old title."""
    if lesson.curriculum.unit_title:
        return lesson.curriculum.unit_title
    match = _OLD_TITLE.match(lesson.title)
    return match["title"] if match else ""


def unit_line(lesson: Lesson) -> str:
    """``Unit 1 · Local community``, or ``Unit 1`` when the title is unknown."""
    number = unit_number(lesson.curriculum)
    title = unit_title(lesson)
    head = f"Unit {number}" if number is not None else "Unit"
    return f"{head}{SEP}{title}" if title else head


def display_title(lesson: Lesson) -> str:
    """The lesson's name in lists and headings: the grade and lesson label."""
    return lesson_label(lesson)
