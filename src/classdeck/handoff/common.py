"""What the prompt packs and their checkers share: the lesson as prompt text and the word list.

A chat model in the browser cannot read a lesson file, so the packs render the lesson as compact,
deterministic Markdown; the checkers then hold the model's reply to the same lesson. Both sides
use the allowed-word list built here, so a word the pack permitted is never reported later.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

import simplemma

from classdeck.curriculum import Curriculum
from classdeck.english.validators import pupil_text_of, tokenize
from classdeck.export.source_map import (
    PROVENANCE_LABEL,
    collect_warnings,
    format_check,
    format_citation,
)
from classdeck.lesson import (
    ActivityBlock,
    Block,
    Citation,
    DialogueBlock,
    ImageRefBlock,
    Lesson,
    ObjectiveBlock,
    Phase,
    PhonicsBlock,
    SentencePatternBlock,
    SourceDocument,
    SpanKind,
    SummaryBlock,
    TeacherNoteBlock,
    VocabularyBlock,
)

Sources = Mapping[str, SourceDocument]

TEACHER_ONLY = "Teacher-only:"
"""Marker that opens the part of a block only the teacher may see."""

PICTURE_PLACEHOLDER_RULE = (
    "Where a picture goes, put an empty placeholder tag "
    '<img data-block-id="…" data-item="…" alt="…"> with no src attribute: data-block-id is the '
    "block the picture belongs to, data-item the index of the vocabulary item it illustrates "
    "(0 for the first item; always 0 for an image_ref block) and alt the word or the alt text "
    "given. The checker embeds the real picture there as a data URI. Never invent an image URL "
    "and never describe the picture in words on the slide."
)

PHASE_LABEL: dict[Phase, str] = {
    Phase.WARM_UP: "Warm-up",
    Phase.PRESENTATION: "Presentation",
    Phase.PRACTICE: "Practice",
    Phase.PRODUCTION: "Production",
    Phase.WRAP_UP: "Wrap-up",
}


def one_line(text: str) -> str:
    """The text on a single line, so no value can open a new line or a new heading in the pack."""
    return " ".join(text.split())


def picture_citation(block: ImageRefBlock, sources: Sources | None) -> Citation | None:
    """The citation an image_ref picture comes from: the first cited image span.

    Without ``sources`` the first citation stands in; with them, a citation whose span is not
    an image is skipped, and the first citation is the fallback when none is an image.
    """
    if not block.sources:
        return None
    if sources is not None:
        for citation in block.sources:
            document = sources.get(citation.doc_id)
            span = document.find_span(citation.page, citation.span_id) if document else None
            if span is not None and span.kind is SpanKind.IMAGE:
                return citation
    return block.sources[0]


@lru_cache(maxsize=8192)
def lemma(word: str) -> str:
    """The English lemma of a lower-cased word, as the level checks compute it."""
    return simplemma.lemmatize(word, lang="en").lower()


# --- allowed words -----------------------------------------------------------------------------


def allowed_words(lesson: Lesson, curriculum: Curriculum | None = None) -> list[str]:
    """The words a deck may show, sorted.

    The curriculum's grade list plus the unit's words when a curriculum is given, always
    together with the lesson's vocabulary words and the words (and lemmas) of every block's
    pupil-facing text, so the lesson can be rendered with the list it was written in.
    """
    words: set[str] = set()
    if curriculum is not None:
        try:
            words |= curriculum.word_list(lesson.curriculum.unit_id)
        except KeyError:
            words |= curriculum.word_list()
    for block in lesson.blocks.values():
        if isinstance(block, VocabularyBlock):
            words.update(item.word.lower() for item in block.content.items)
        for text in pupil_text_of(block):
            for token in tokenize(text):
                words.add(token.surface)
                words.add(lemma(token.surface))
    return sorted(words)


def listed_words(lesson: Lesson, curriculum: Curriculum | None = None) -> list[str]:
    """The curriculum's own list for the lesson, sorted: the grade's words, the classroom
    language and the unit's words. Without a curriculum, only the lesson's vocabulary words.
    """
    words: set[str] = set()
    if curriculum is not None:
        try:
            words |= curriculum.word_list(lesson.curriculum.unit_id)
        except KeyError:
            words |= curriculum.word_list()
    for block in lesson.blocks.values():
        if isinstance(block, VocabularyBlock):
            words.update(item.word.lower() for item in block.content.items)
    return sorted(words)


# --- the lesson as prompt text -----------------------------------------------------------------


def render_lesson_for_prompt(lesson: Lesson, *, sources: Sources | None = None) -> str:
    """The lesson as compact Markdown for a chat model.

    One heading per section, then one ``###`` heading per block with its id, kind, provenance
    label and formatted citations, followed by the content in a few lines. Material the pupils
    must not see is grouped under :data:`TEACHER_ONLY`. ``sources`` only serve to confirm that a
    cited picture is an image span.
    """
    lines = [f"# {one_line(lesson.title)}", _lesson_line(lesson), ""]
    placed: set[str] = set()
    for index, section in enumerate(lesson.sections, start=1):
        label = PHASE_LABEL.get(section.phase, section.phase.value)
        lines.append(
            f"## Section {index} · {section.phase.value} ({label}) · {one_line(section.title)} · "
            f"{section.minutes} min"
        )
        lines.append("")
        for block_id in section.block_ids:
            placed.add(block_id)
            lines.extend(_render_block(lesson.blocks[block_id], sources))
            lines.append("")
    unplaced = [block for key, block in lesson.blocks.items() if key not in placed]
    if unplaced:
        lines.append("## Blocks not placed in a section")
        lines.append("")
        for block in unplaced:
            lines.extend(_render_block(block, sources))
            lines.append("")
    warnings = collect_warnings(lesson)
    if warnings:
        lines.append(f"## Warnings ({TEACHER_ONLY} shown only in teacher mode)")
        lines.append("")
        lines.extend(f"- {one_line(format_check(block_id, check))}" for block_id, check in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _lesson_line(lesson: Lesson) -> str:
    ref = lesson.curriculum
    parts = [
        f"Grade {lesson.grade}",
        f"unit {ref.unit_id}",
        f"lesson {ref.lesson_no}" if ref.lesson_no is not None else "",
        f"period {ref.period}" if ref.period is not None else "",
        f"{sum(section.minutes for section in lesson.sections)} min in "
        f"{len(lesson.sections)} sections",
        f"{len(lesson.blocks)} blocks",
    ]
    return " · ".join(part for part in parts if part)


def _origin(block: Block) -> str:
    if block.sources:
        return ", ".join(format_citation(citation) for citation in block.sources)
    if block.depends_on:
        return "Derived from " + ", ".join(block.depends_on)
    return "no citation"


def _render_block(block: Block, sources: Sources | None) -> list[str]:
    t = one_line
    label = PROVENANCE_LABEL[block.provenance]
    header = one_line(f"### {block.id} · {block.kind} · {label} · {_origin(block)}")
    body: list[str] = []
    teacher: list[str] = []
    match block:
        case ObjectiveBlock():
            content = block.content
            teacher.append(f"objective: {t(content.text)}")
            details = []
            if content.outcome_ids:
                details.append("outcomes: " + ", ".join(content.outcome_ids))
            if content.skills:
                details.append("skills: " + ", ".join(skill.value for skill in content.skills))
            if details:
                teacher.append(" · ".join(details))
        case VocabularyBlock():
            for index, item in enumerate(block.content.items):
                line = f"- {t(item.word)}"
                if item.ipa:
                    line += f" {_ipa(t(item.ipa))}"
                if item.meaning_vi:
                    line += f" — {t(item.meaning_vi)}"
                if item.image is not None:
                    line += f" (picture: {_picture_note(item.image, index, sources)})"
                elif item.generated_picture:
                    line += " (generated picture)"
                body.append(line)
        case SentencePatternBlock():
            content = block.content
            body.append(f"- pattern: {t(content.pattern)}")
            if content.function:
                body.append(f"- function: {t(content.function)}")
            if content.examples:
                body.append("- examples:")
                body.extend(f"  - {t(example)}" for example in content.examples)
            if content.notes:
                teacher.append(f"notes: {t(content.notes)}")
        case DialogueBlock():
            content = block.content
            if content.context:
                body.append(f"- context: {t(content.context)}")
            body.extend(
                f"- {t(line.speaker)}: {t(line.text)}" if line.speaker else f"- {t(line.text)}"
                for line in content.lines
            )
        case PhonicsBlock():
            content = block.content
            body.append(f"- sound: {t(content.sound)} · letters: {t(content.letters)}")
            if content.words:
                body.append("- words: " + ", ".join(t(word) for word in content.words))
            if content.chant_lines:
                body.append("- chant:")
                body.extend(f"  - {t(line)}" for line in content.chant_lines)
        case ActivityBlock():
            content = block.content
            body.append(f"- title: {t(content.title)}")
            body.append(f"- type: {content.type.value}")
            if content.instructions:
                body.append(f"- instructions: {t(content.instructions)}")
            if content.student_steps:
                body.append("- student_steps:")
                body.extend(f"  {n}. {t(step)}" for n, step in enumerate(content.student_steps, 1))
            teacher.append(
                f"skills: {', '.join(skill.value for skill in content.skills) or 'none'} · "
                f"interaction: {content.interaction.value.replace('_', ' ')} · "
                f"minutes: {content.minutes}"
            )
            if content.teacher_steps:
                teacher.append("teacher_steps:")
                teacher.extend(
                    f"  {n}. {t(step)}" for n, step in enumerate(content.teacher_steps, 1)
                )
            if content.materials:
                teacher.append("materials: " + ", ".join(t(item) for item in content.materials))
            if content.competency_tags:
                teacher.append("competences: " + ", ".join(content.competency_tags))
        case SummaryBlock():
            body.extend(f"- {t(bullet)}" for bullet in block.content.bullets)
        case ImageRefBlock():
            content = block.content
            citation = picture_citation(block, sources)
            picture = _picture_note(citation, 0, sources) if citation else "none cited"
            body.append(f"- picture: {picture} · alt: {t(content.alt)}")
            if content.caption:
                body.append(f"- caption: {t(content.caption)}")
        case TeacherNoteBlock():
            teacher.append(f"note: {t(block.content.text)}")
    if block.teacher_note:
        teacher.append(f"note: {t(block.teacher_note)}")
    lines = [header, *body]
    if teacher:
        lines.append(f"- {TEACHER_ONLY}")
        # Numbered steps are already indented under their label; everything else is a bullet.
        lines.extend(f"  {line}" if line.startswith("  ") else f"  - {line}" for line in teacher)
    return lines


def _ipa(ipa: str) -> str:
    text = ipa.strip()
    return text if text.startswith("/") else f"/{text}/"


def _picture_note(citation: Citation, item: int, sources: Sources | None) -> str:
    """``data-item 0, p. 6, ¶4``, or a warning when the cited span is known not to be an image."""
    where = format_citation(citation)
    if sources is not None:
        document = sources.get(citation.doc_id)
        span = document.find_span(citation.page, citation.span_id) if document else None
        if span is None or span.kind is not SpanKind.IMAGE:
            return f"cited at {where} but no image span found"
    return f"data-item {item}, {where}"
