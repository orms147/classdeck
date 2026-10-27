"""The deck pack: one prompt that asks a chat model for the HTML deck of a lesson.

``docs/output-formats.md``, "Prompt packs": a teacher with no API key pastes the pack into a free
chat model, saves the reply as ``.html`` and runs ``check-deck`` over it. The pack carries the
design brief of the HTML deck in condensed form, the lesson rendered by
:func:`~classdeck.handoff.common.render_lesson_for_prompt`, the allowed-word list verbatim and
the hard rules the checker enforces. For a given lesson and curriculum it is deterministic.
"""

from __future__ import annotations

from classdeck.curriculum import Curriculum
from classdeck.english.rules import PHASES
from classdeck.export.source_map import ensure_exportable
from classdeck.handoff.common import (
    PHASE_LABEL,
    PICTURE_PLACEHOLDER_RULE,
    TEACHER_ONLY,
    Sources,
    listed_words,
    render_lesson_for_prompt,
)
from classdeck.lesson import ActivityType, Lesson, Phase, Provenance

DEFAULT_PERIOD_MINUTES = 35
PUPIL_AGES = "8 to 10"

BACKGROUND = "#FFFDF7"
INK = "#1F2937"

PHASE_STYLE: dict[Phase, tuple[str, str]] = {
    Phase.WARM_UP: ("☀️", "#F9C74F"),
    Phase.PRESENTATION: ("📖", "#4D96FF"),
    Phase.PRACTICE: ("✏️", "#6BCB77"),
    Phase.PRODUCTION: ("🎤", "#FF6B6B"),
    Phase.WRAP_UP: ("⭐", "#9D4EDD"),
}
"""Icon and accent colour of each phase (theme ``kids``)."""

ACTIVITY_EMOJI: dict[ActivityType, str] = {
    ActivityType.LISTEN_AND_DO: "👂",
    ActivityType.LOOK_LISTEN_REPEAT: "👀🗣️",
    ActivityType.LISTEN_POINT_SAY: "👉🗣️",
    ActivityType.LETS_TALK: "💬",
    ActivityType.LETS_WRITE: "✍️",
    ActivityType.SONG: "🎵",
    ActivityType.CHANT: "🥁",
    ActivityType.GAME: "🎮",
    ActivityType.ROLE_PLAY: "🎭",
    ActivityType.PROJECT: "🧩",
    ActivityType.READ_AND_MATCH: "🧠",
    ActivityType.LOOK_COMPLETE_READ: "📝",
    ActivityType.TPR: "🤸",
    ActivityType.OTHER: "✨",
}

PROVENANCE_VALUE: dict[str, str] = {
    "Source": Provenance.EXTRACTED.value,
    "Derived": Provenance.DERIVED.value,
    "AI-added": Provenance.GENERATED.value,
}
"""The ``data-provenance`` value behind each provenance label used in the rendered lesson."""

SLIDE_TYPES: list[tuple[str, str]] = [
    ("cover", "unit and lesson title, grade, a phase strip with the minutes of each section"),
    (
        "section start",
        "divider: phase icon, section title, minutes, a tap-to-start countdown for the section",
    ),
    (
        "vocabulary",
        "one flashcard per item: picture dominant (the placeholder <img>), the word, the IPA; "
        "tap to reveal meaning_vi",
    ),
    (
        "dialogue",
        "chat bubbles alternating left/right by speaker with initial-letter avatars; "
        "tap to reveal line by line",
    ),
    (
        "sentence_pattern",
        "the pattern with the slot as a dashed box; tap cycles the examples into the slot; "
        "function as a small caption",
    ),
    ("phonics", "the sound very large, the words with the target letters highlighted, chant lines"),
    (
        "activity",
        "icon, title, instructions (large, at most 12 words), student_steps as numbered cards, "
        "a countdown for its minutes",
    ),
    ("summary", "grid of the lesson's words (with their placeholder pictures) and the pattern"),
    ("image_ref", "the picture (placeholder <img>) with its caption"),
    (
        "objective, teacher_note",
        'no slide of their own: a class="teacher" note (carrying the block\'s data-block-id) on '
        "the cover or on the previous slide",
    ),
    (
        "final slides, teacher mode only",
        "the warnings list (if the lesson has any) and a source map table: "
        "block id, kind, provenance, citation",
    ),
]


def build_deck_pack(
    lesson: Lesson,
    *,
    sources: Sources | None = None,
    curriculum: Curriculum | None = None,
    theme_notes: str | None = None,
) -> str:
    """The complete Markdown prompt for the deck of ``lesson``.

    ``theme_notes`` are the teacher's own remarks on look and feel, appended to the design brief.
    The lesson must be exportable: as for the other writers, a failed check raises
    :class:`~classdeck.export.source_map.ExportBlocked`; warnings are listed in the pack.
    """
    ensure_exportable(lesson)
    minutes = curriculum.period_minutes if curriculum is not None else DEFAULT_PERIOD_MINUTES
    parts = [
        f"# Deck pack — {lesson.title}",
        "",
        *_role(lesson, minutes),
        "",
        *_design_brief(theme_notes),
        "",
        "## 3. The lesson",
        "",
        render_lesson_for_prompt(lesson, sources=sources).rstrip(),
        "",
        *_allowed(lesson, curriculum),
        "",
        *_hard_rules(),
    ]
    return "\n".join(parts).rstrip() + "\n"


def _role(lesson: Lesson, minutes: int) -> list[str]:
    return [
        "## 1. Role and audience",
        "",
        "You design classroom slides for primary English in Viet Duc. Build the slide deck for "
        f'one {minutes}-minute period of grade {lesson.grade} English: "{lesson.title}". The '
        f"pupils are aged {PUPIL_AGES} and read very little English, so slides are big, bright, "
        "picture-led and carry very few words; the teacher projects the deck in class and reads "
        "the teacher material on the laptop. Every word on a slide must come from the lesson "
        "below, every picture from its placeholders.",
    ]


def _design_brief(theme_notes: str | None) -> list[str]:
    phase_lines = []
    for spec in PHASES:
        icon, colour = PHASE_STYLE[spec.phase]
        label = PHASE_LABEL[spec.phase]
        phase_lines.append(f"  - {icon} {label} ({spec.phase.value}) {colour} — {spec.description}")
    emoji = " · ".join(f"{icon} {kind.value}" for kind, icon in ACTIVITY_EMOJI.items())
    provenance = ", ".join(f"{label} → {value}" for label, value in PROVENANCE_VALUE.items())
    table = ["| block / position | slide |", "|---|---|"]
    table.extend(f"| {block} | {slide} |" for block, slide in SLIDE_TYPES)
    lines = [
        "## 2. Design brief",
        "",
        'Theme "kids": one self-contained HTML file for a projector. A 16:9 stage scaled to fit '
        "the window, working at 1024 by 768 and on a phone; it opens offline from a USB stick and "
        "looks the same everywhere.",
        "",
        "- One accent colour per phase, used for the divider slide and as a top band on every "
        "slide of that phase:",
        *phase_lines,
        f"- Background off-white {BACKGROUND}; text near-black {INK}; contrast at least 4.5:1.",
        "- Rounded cards (24 px radius), soft shadows, generous padding. Nothing smaller than "
        "32 px on a slide; words on flashcards 96 px, sentence patterns 56 px, dialogue lines "
        "44 px.",
        f"- An emoji icon per activity type: {emoji}.",
        "- Simple CSS transitions (fade or slide-in), no libraries; respect "
        "prefers-reduced-motion; large hit targets.",
        "- System font stack only; all CSS and JavaScript inline; no external resources of any "
        "kind (no web fonts, scripts, stylesheets or images from the web).",
        "- Navigation: arrow keys, space, click or tap on the right or left third, swipe; "
        "F toggles fullscreen; T toggles teacher mode; N toggles only the notes panel; progress "
        "dots at the bottom; print gives one slide per page with the notes under each.",
        "",
        "Slide types, one slide per block in lesson order, plus the cover and one divider per "
        "section:",
        "",
        *table,
        "",
        "Pupil mode and teacher mode:",
        "",
        "- Pupil mode is the default: clean slides with no citations, badges or notes.",
        '- Everything for the teacher lives in elements with class="teacher", hidden by default '
        "and shown in teacher mode (T key or ?teacher=1): teacher steps, materials, interaction "
        "and minutes in a notes panel; a footer `Source: p. 6, ¶3` with each block's citations; "
        'the badge "AI-added" on AI-added blocks; objectives and teacher notes; the final '
        f'warnings and source map slides. Everything marked "{TEACHER_ONLY}" in the lesson below '
        "belongs there.",
        '- Every element that renders a block carries data-block-id="<block id>", '
        'data-kind="<kind>" and data-provenance="<extracted|derived|generated>" '
        f'({provenance}). A vocabulary flashcard also carries data-item="<item index>".',
        '- Mark your own interface text with class="ui": the cover line "Grade 3", buttons '
        'such as "Start", countdown labels and tap hints. It is shown to pupils but is not '
        'checked against the allowed words; lesson content never goes in a class="ui" element.',
        f"- Pictures: {PICTURE_PLACEHOLDER_RULE}",
    ]
    if theme_notes and theme_notes.strip():
        lines.extend(["", f"Teacher's notes on look and feel: {theme_notes.strip()}"])
    return lines


def _allowed(lesson: Lesson, curriculum: Curriculum | None) -> list[str]:
    return [
        "## 4. Allowed words",
        "",
        "The grade word list, the classroom language and the words of this unit:",
        "",
        ", ".join(listed_words(lesson, curriculum)),
        "",
        "Every word of the pupil-facing text in part 3 (everything outside the "
        f'"{TEACHER_ONLY}" groups) is allowed as well.',
        "English only on slides; use no other words except names and numbers. The lesson title, "
        "the section titles, the activity titles, the sounds and the IPA may appear as written in "
        "part 3; a flashcard's meaning_vi appears only inside its tap-to-reveal.",
    ]


def _hard_rules() -> list[str]:
    return [
        "## 5. Hard rules",
        "",
        "1. Keep every block id: each block of part 3 appears exactly once as data-block-id on "
        "the element that renders it (a vocabulary block: once per flashcard, each flashcard with "
        "its own data-item index). Invent no other block ids. The only other place data-block-id "
        "appears is on <img> picture placeholders.",
        "2. One slide per block as in the slide-types table, in lesson order, after the cover and "
        "behind the divider of its section.",
        "3. English only on slides, and no words outside the allowed list except names and "
        "numbers. Interface text that is not lesson content (the cover's grade line, a Start "
        'button, countdown labels, tap hints) goes in elements with class="ui"; keep it to a few '
        "short words, and every other word on a slide comes from the allowed list. Keyboard "
        'help goes in class="teacher" elements.',
        '4. Teacher content only in elements with class="teacher": teacher steps, materials, '
        "interaction, minutes, objectives, teacher notes, citations, badges and warnings. Nothing "
        f'marked "{TEACHER_ONLY}" may appear anywhere else.',
        "5. No external resources: no http:// or https:// in src, href, @import or url(); no web "
        "fonts; all CSS and JavaScript inline. Pictures only through the placeholders described.",
        "6. Output ONE complete HTML file that starts with <!doctype html> and nothing else: no "
        "explanation, no Markdown fence, no text before or after the HTML.",
    ]
