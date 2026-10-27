"""Prompt packs: hand the generative step to a chat model in the browser, then check its reply.

See ``docs/output-formats.md``, "Prompt packs". The deck pack asks for the HTML deck of a lesson;
``check_deck`` holds the reply to the lesson and embeds the pictures. The draft pack asks for the
``DraftSection`` JSON of every section of an outline; ``import_draft`` validates the reply and
assembles the lesson.
"""

from __future__ import annotations

from classdeck.handoff.check_deck import NO_BLOCK, DeckCheckResult, check_deck
from classdeck.handoff.common import (
    PHASE_LABEL,
    PICTURE_PLACEHOLDER_RULE,
    TEACHER_ONLY,
    allowed_words,
    listed_words,
    one_line,
    render_lesson_for_prompt,
)
from classdeck.handoff.deck_pack import (
    ACTIVITY_EMOJI,
    PHASE_STYLE,
    SLIDE_TYPES,
    build_deck_pack,
)
from classdeck.handoff.draft_pack import WRAP_UP_REPLY, build_draft_pack
from classdeck.handoff.import_draft import ImportResult, SectionReport, import_draft

__all__ = [
    "ACTIVITY_EMOJI",
    "NO_BLOCK",
    "PHASE_LABEL",
    "PHASE_STYLE",
    "PICTURE_PLACEHOLDER_RULE",
    "SLIDE_TYPES",
    "TEACHER_ONLY",
    "WRAP_UP_REPLY",
    "DeckCheckResult",
    "ImportResult",
    "SectionReport",
    "allowed_words",
    "build_deck_pack",
    "build_draft_pack",
    "check_deck",
    "import_draft",
    "listed_words",
    "one_line",
    "render_lesson_for_prompt",
]
