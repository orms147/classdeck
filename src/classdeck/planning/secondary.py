"""Drafting one lesson of a lower-secondary unit (Global Success 6-9).

``docs/secondary-units.md``, "Drafting a secondary lesson". The textbook page is cut by code:
section banners (GETTING STARTED, A CLOSER LOOK 1 ...) split a unit into its seven lessons, each
``instruction`` span starts an exercise, and every exercise becomes a lesson block whose wording
is copied exactly and which cites the spans it came from. Exactly one model call per lesson then
fills what the page does not say: answer keys, glosses and IPA for the new words, a warm-up, the
teacher's steps and timing, a wrap-up and homework. The reply is validated; one re-prompt, then a
deterministic fallback for whatever is still wrong (an exercise without answers becomes a plain
question list, the warm-up and wrap-up come from templates).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from classdeck.curriculum.loader import Curriculum
from classdeck.curriculum.models import Outcome, Unit
from classdeck.lesson.ids import new_id
from classdeck.lesson.labels import SECONDARY_LESSON_TYPES, lesson_label_for
from classdeck.lesson.schema import (
    MAX_GALLERY_IMAGES,
    ActivityBlock,
    ActivityContent,
    ActivityType,
    Block,
    BlockAdapter,
    Citation,
    CurriculumRef,
    GalleryBlock,
    GalleryContent,
    GalleryImage,
    Interaction,
    Lesson,
    ObjectiveBlock,
    ObjectiveContent,
    Page,
    Phase,
    Provenance,
    ReadingBlock,
    ReadingContent,
    ReadingParagraph,
    Section,
    SourceDocument,
    SourceRef,
    Span,
    SpanKind,
    SummaryBlock,
    SummaryContent,
    TeacherNoteBlock,
    TeacherNoteContent,
    VocabItem,
    VocabularyBlock,
    VocabularyContent,
)
from classdeck.llm.base import LLMError, LLMProvider, LLMResponse, Message
from classdeck.llm.pricing import estimate_cost_usd
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, extract_json
from classdeck.planning.blueprints import Blueprint, blueprint_for, blueprint_prompt
from classdeck.planning.models import SpanRef
from classdeck.planning.outliner import scale_minutes
from classdeck.planning.spelling import britishise, variants

logger = logging.getLogger(__name__)

LESSON_NAMES = SECONDARY_LESSON_TYPES


PHASE_MINUTES: dict[Phase, int] = {
    Phase.WARM_UP: 5,
    Phase.PRESENTATION: 11,
    Phase.PRACTICE: 16,
    Phase.PRODUCTION: 9,
    Phase.WRAP_UP: 4,
}
"""The 45-minute skeleton; a phase without an exercise gives its minutes to the others."""

PHASE_TITLES: dict[Phase, str] = {
    Phase.WARM_UP: "Warm-up",
    Phase.PRESENTATION: "Listen, read and learn",
    Phase.PRACTICE: "Practise",
    Phase.PRODUCTION: "Your turn",
    Phase.WRAP_UP: "Wrap-up",
}

VOCABULARY_WEIGHT = 3
"""Minutes (as a weight) the new-words cards take inside the presentation phase."""
MAX_VOCABULARY = 8
MAX_ITEMS_PER_EXERCISE = 12
GRID_BLANK_ROWS = 4
"""Empty rows of a survey table whose body the page leaves blank."""

type Entry = tuple[SpanRef, Span]


# --- sections ----------------------------------------------------------------------------------


def section_lesson(text: str) -> int | None:
    """The lesson number a section banner opens (``GETTING STARTED`` is 1), else ``None``."""
    name = " ".join(re.sub(r"[^a-z0-9& ]+", " ", text.lower()).replace("&", " and ").split())
    if name == "getting started":
        return 1
    if name in ("a closer look 1", "closer look 1"):
        return 2
    if name in ("a closer look 2", "closer look 2"):
        return 3
    if name == "communication":
        return 4
    if name == "skills 1":
        return 5
    if name == "skills 2":
        return 6
    if name.startswith("looking back") or name == "project":
        return 7
    return None


@dataclass
class LessonText:
    """The spans of one textbook lesson, between its banner and the next lesson's banner."""

    number: int
    banner: SpanRef
    entries: list[Entry] = field(default_factory=list)
    project_from: int | None = None
    """Index in ``entries`` where the PROJECT banner stood (lesson 7 only): what follows is the
    project, what precedes it the review."""


def split_lessons(documents: Sequence[SourceDocument]) -> dict[int, LessonText]:
    """Cut the unit's pages into its lessons by their section banners.

    A lesson may start on any page and continue over several; the banners of ``LOOKING BACK`` and
    ``PROJECT`` both belong to lesson 7. Spans before the first banner (the unit opener) belong
    to no lesson. A lesson whose banner is missing from the pages is absent from the result.
    """
    lessons: dict[int, LessonText] = {}
    current: LessonText | None = None
    for document in documents:
        for page in document.pages:
            for span in page.spans:
                ref = SpanRef(doc_id=document.doc_id, page=page.number, span_id=span.span_id)
                number = section_lesson(span.text) if span.kind is SpanKind.HEADING else None
                if number is not None:
                    if number in lessons:
                        current = lessons[number] if current is lessons[number] else None
                    else:
                        current = lessons[number] = LessonText(number, ref)
                    if current is not None and _is_project_banner(span.text):
                        current.project_from = len(current.entries)
                    continue
                if current is not None and not _overlaid(span, page):
                    current.entries.append((ref, span))
    return lessons


def _is_project_banner(text: str) -> bool:
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text.lower()).split()) == "project"


PART_HEADINGS = frozenset(
    {
        "vocabulary",
        "pronunciation",
        "grammar",
        "reading",
        "speaking",
        "listening",
        "writing",
        "everyday english",
        "project",
    }
)
"""Headings that name a part of a lesson, not its topic."""

OVERLAID_TEXT_SPANS = 3
"""A picture with at least this many text spans printed on it is a background, not a picture."""


def _overlaid(span: Span, page: Page) -> bool:
    """Whether ``span`` is a picture with printed text on top of it.

    A photo behind a conversation is cropped with the conversation on it; shown as a picture it
    would repeat the text, so it is left out.
    """
    if span.kind is not SpanKind.IMAGE or span.bbox is None:
        return False
    x0, y0, x1, y1 = span.bbox
    covered = 0
    for other in page.spans:
        if other is span or other.kind is SpanKind.IMAGE or other.bbox is None:
            continue
        if not other.text.strip():
            continue
        ox0, oy0, ox1, oy1 = other.bbox
        area = (ox1 - ox0) * (oy1 - oy0)
        width, height = min(x1, ox1) - max(x0, ox0), min(y1, oy1) - max(y0, oy0)
        if area > 0 and width > 0 and height > 0 and width * height >= 0.5 * area:
            covered += 1
    return covered >= OVERLAID_TEXT_SPANS


# --- exercises ---------------------------------------------------------------------------------

_NUMBERED = re.compile(r"^\s*(\d{1,2})[.)]\s*(.*)$", re.DOTALL)
_OPTION = re.compile(r"^\s*([A-Da-d])[.)]\s+(.*)$", re.DOTALL)
_INLINE_OPTION = re.compile(r"\s+(?=[A-D][.)]\s)")
_LETTERED = re.compile(r"^\s*([a-hA-H])[.)]\s*(.*)$", re.DOTALL)
_RSQUO, _LSQUO = chr(0x2019), chr(0x2018)
_LENGTH_MARK = chr(0x02D0)
_NAME_CHARS = "[\\w'" + _RSQUO + ".-]"
_SPEAKER = re.compile(
    rf"^([A-Z]{_NAME_CHARS}*(?: [A-Z]{_NAME_CHARS}*){{0,2}}):\s+(\S.*)$", re.DOTALL
)
_BLANK = re.compile(r"_{2,}")
_RULE_HEADING = re.compile(r"^\W*remember\b", re.IGNORECASE)
_TRUE_FALSE = re.compile(r"\btrue\b.*\bfalse\b", re.IGNORECASE)
_TRUE_FALSE_LETTERS = re.compile(r"\bT\b.*\bF\b|\bT/F\b")
_FREE = re.compile(
    r"\b(quiz|game|your|you|discuss|share|talk|ask and answer|pairs?|groups?|present|project|"
    r"tell|role[- ]?play|survey|interview)\b",
    re.IGNORECASE,
)
_PRODUCE = re.compile(
    r"\b(speak|talk|write|present|role[- ]?play|project|game|discuss|ask|tell|describe|"
    r"interview|survey)\b",
    re.IGNORECASE,
)
_MIN_READING_CHARS = 120
_SENTENCE = re.compile(r"[.!?]\s*$")
_SOUND = re.compile(r"(?<![\w/])/([^\s/]{1,6})/(?![\w/])")
_PROMPT_VERB = re.compile(
    r"^(work|ask|tell|talk|discuss|write|think|say|add|share|look|find|make|choose|play|"
    r"describe|interview|act|practise|practice|work)\b",
    re.IGNORECASE,
)
_MAX_PROMPT_WORDS = 25
_WORD_CELL = re.compile(rf"[A-Za-z][A-Za-z'{_RSQUO}-]*(?: [A-Za-z][A-Za-z'{_RSQUO}-]*)?")
_MIN_SOUND_WORDS = 4
_LEADING_BLANKS = re.compile(r"^[\s_]*(?:\d{1,2}[.)])?[\s_]*")
_TRAILING_BLANKS = re.compile(r"[\s_]+$")
_PICTURE_WORDS = re.compile(r"\b(pictures?|photos?|images?)\b", re.IGNORECASE)


def sounds_in(text: str) -> list[str]:
    """The IPA sounds written between slashes in ``text`` (``/e/``), once each, in order."""
    found: list[str] = []
    for match in _SOUND.finditer(text):
        sound = match.group(1).replace(":", _LENGTH_MARK)
        if sound not in found:
            found.append(sound)
    return found


@dataclass
class ExerciseChunk:
    """The spans of one textbook exercise (or *Remember!* box), in reading order."""

    number: str | None
    instruction: str
    ref: SpanRef | None
    heading: str = ""
    rule: bool = False
    title: str = ""
    title_ref: SpanRef | None = None
    body: list[Entry] = field(default_factory=list)
    follow_ups: list[Entry] = field(default_factory=list)
    sounds: list[str] = field(default_factory=list)
    """The sounds of a pronunciation exercise: from its instruction, else from the heading above."""
    heading_ref: SpanRef | None = None
    examples: list[Entry] = field(default_factory=list)
    """The lines under an ``Example:`` heading inside the exercise or rule box."""
    example_ref: SpanRef | None = None
    track: int | None = None
    """The audio track the page marks next to the instruction."""
    project: bool = False
    """The exercise belongs to the project (after the PROJECT banner of lesson 7)."""
    prelude: list[Entry] = field(default_factory=list)
    """Spans of the project before its first numbered exercise (pictures, ``Work in groups.``)."""
    self_check: bool = False
    """The *Now I can ...* list."""


def _instruction_parts(text: str) -> tuple[str | None, str]:
    match = _NUMBERED.match(text)
    if match is None:
        return None, " ".join(text.split())
    return match.group(1), " ".join(match.group(2).split())


_EXAMPLE_HEADING = re.compile(r"^examples?\s*:?$", re.IGNORECASE)


def chunk_lesson(lesson: LessonText) -> list[ExerciseChunk]:
    """Cut a lesson into exercises: each numbered ``instruction`` span starts one.

    A *Remember!* heading starts a rule chunk. Another heading ends the open exercise and
    becomes the heading of the exercises after it (``Vocabulary``, ``Pronunciation``), until the
    next heading, except a heading
    that ends with ``?`` after the items (a follow-up question), a heading right after the
    instruction (the title of a reading text), ``Remember!`` and ``Example:`` (the lines after it
    belong to the open exercise as its examples). An ``instruction`` span without
    a number continues the instruction line above it. Spans before the first exercise are dropped,
    except those of the project (pictures, ``Work in groups.``), which are kept in the ``prelude``
    of its first chunk.
    """
    chunks: list[ExerciseChunk] = []
    current: ExerciseChunk | None = None
    pending, pending_ref = "", None
    heading_sounds: list[str] = []
    in_examples = False
    project = False
    prelude: list[Entry] = []
    for index, (ref, span) in enumerate(lesson.entries):
        if lesson.project_from is not None and index == lesson.project_from:
            current, pending, pending_ref, project, in_examples = None, "", None, True, False
        text = " ".join(span.text.split())
        if _NOW_I_CAN.match(text) and span.kind in (
            SpanKind.TABLE,
            SpanKind.HEADING,
            SpanKind.INSTRUCTION,
        ):
            current = ExerciseChunk(
                None,
                "Now I can ...",
                ref,
                heading=pending,
                heading_ref=pending_ref,
                project=project,
                self_check=True,
            )
            if span.kind is SpanKind.TABLE:
                current.body.append((ref, span))
            chunks.append(current)
            in_examples = False
            continue
        if (
            current is not None
            and span.kind in (SpanKind.HEADING, SpanKind.PARAGRAPH, SpanKind.LIST_ITEM)
            and _EXAMPLE_HEADING.match(text)
        ):  # the label of the examples, whatever kind the page reading gave it
            in_examples, current.example_ref = True, ref
            continue
        if span.kind is SpanKind.HEADING:
            in_examples = False
            if len(sounds_in(text)) >= 2:
                heading_sounds = sounds_in(text)
            if _RULE_HEADING.match(text):
                current = ExerciseChunk(
                    None, text, ref, rule=True, heading=pending, heading_ref=pending_ref
                )
                current.project = project
                chunks.append(current)
            elif current is not None and not current.rule and current.body and text.endswith("?"):
                current.follow_ups.append((ref, span))
            elif (
                current is not None and not current.rule and not current.body and not current.title
            ):
                current.title, current.title_ref = text, ref
            else:
                current, pending, pending_ref = None, text, ref
            continue
        if span.kind is SpanKind.INSTRUCTION:
            in_examples = False
            number, rest = _instruction_parts(text)
            if number is not None:
                sounds = sounds_in(rest)
                current = ExerciseChunk(
                    number,
                    rest,
                    ref,
                    heading=pending,
                    heading_ref=pending_ref,
                    sounds=sounds if len(sounds) >= 2 else heading_sounds,
                    track=span.track,
                    project=project,
                )
                if project and prelude and not any(c.project for c in chunks):
                    current.prelude, prelude = prelude, []
                chunks.append(current)
                continue
            if current is not None and not current.body and not current.rule and current.ref:
                current.instruction = f"{current.instruction} {rest}".strip()
                current.track = current.track or span.track
                continue
        if current is None and project and not any(c.project for c in chunks):
            if text or span.kind is SpanKind.IMAGE:
                prelude.append((ref, span))
            continue
        if current is not None and in_examples and (text or span.kind is SpanKind.IMAGE):
            current.examples.append((ref, span))
            continue
        if (
            current is not None
            and not current.rule
            and not current.self_check
            and _is_prompt_line(text, span, current)
        ):
            current.follow_ups.append((ref, span))
            continue
        if current is not None and (text or span.kind in (SpanKind.TABLE, SpanKind.IMAGE)):
            current.body.append((ref, span))
    return chunks


def _is_prompt_line(text: str, span: Span, chunk: ExerciseChunk) -> bool:
    """A prompt line printed under an exercise's items (``What other community helpers can you
    add?``): an unnumbered paragraph that is a question or starts with a task verb. It is an
    exercise of its own; leaving it in the body would drop it."""
    if span.kind is not SpanKind.PARAGRAPH or _NUMBERED.match(text) or _SPEAKER.match(text):
        return False
    if len(text.split()) > _MAX_PROMPT_WORDS or not (
        text.endswith("?") or _PROMPT_VERB.match(text)
    ):
        return False
    return any(
        s.kind is SpanKind.TABLE or _NUMBERED.match(" ".join(s.text.split()))
        for _r, s in chunk.body
    )


@dataclass
class Exercise:
    """One exercise as the lesson will carry it, before answers and teacher steps are added."""

    id: str
    number: str | None
    kind: str
    """A :class:`BlockKind` value of an exercise kind, ``dialogue`` or ``activity``."""
    instruction: str
    content: dict[str, Any]
    refs: list[SpanRef]
    phase: Phase
    before: list[Entry] = field(default_factory=list)
    after: list[Entry] = field(default_factory=list)
    heading: str = ""
    track: int | None = None
    """The audio track the page marks for this exercise."""
    project: bool = False
    """The exercise is a step of the project (lesson 7, after the PROJECT banner)."""
    pair: str | None = None
    """For a reading: the id of the matching exercise whose words it highlights."""
    first_rule: bool = False
    """For a rule box: the first of the lesson (it opens the presentation)."""
    bold: list[list[str]] = field(default_factory=list)
    """For a sound sort: the words the page prints in bold, per sentence (a hint for the model)."""

    @property
    def needs_answers(self) -> bool:
        if self.kind == "grid":
            return _grid_blanks(self.content["rows"]) > 0
        return self.kind in _ANSWERED

    @property
    def listening(self) -> bool:
        """An exercise the pupils do while listening: its answers are in the audio."""
        listen = bool(_LISTENING.match(self.instruction))
        return listen and self.kind not in ("dialogue", "sound_sort")

    def prompt_lines(self) -> list[str]:
        """The exercise as the model sees it: kind, instruction and the items, no answers."""
        label = f"textbook exercise {self.number}" if self.number else "no textbook number"
        lines = [f"{self.id} | {label} | {self.kind}", f"Instruction: {self.instruction}"]
        content = self.content
        match self.kind:
            case "gap_fill":
                if content.get("word_box"):
                    lines.append("Word box: " + " / ".join(content["word_box"]))
                lines += [f"{n}. {item['text']}" for n, item in enumerate(content["items"], 1)]
            case "match":
                pictures = content.get("left_images") or []
                lines += [
                    f"{n}. " + (f"[picture: {text}]" if pictures and pictures[n - 1] else text)
                    for n, text in enumerate(content["left"], 1)
                ]
                lines += [f"{chr(97 + n)}. {text}" for n, text in enumerate(content["right"])]
            case "choice":
                for n, item in enumerate(content["items"], 1):
                    lines.append(f"{n}. {item['stem']}")
                    lines += [f"   {chr(65 + i)}. {o}" for i, o in enumerate(item["options"])]
            case "true_false":
                lines += [f"{n}. {i['statement']}" for n, i in enumerate(content["items"], 1)]
            case "question_set":
                lines += [f"{n}. {q['question']}" for n, q in enumerate(content["questions"], 1)]
            case "sound_sort":
                lines.append("Sounds: " + "  ".join(f"/{sound}/" for sound in content["sounds"]))
                if content["words"]:
                    count = len(content["words"])
                    lines.append(
                        f"Words ({count}; word_sounds must have exactly {count} entries): "
                        + "  ".join(f"{n}. {w['word']}" for n, w in enumerate(content["words"], 1))
                    )
                for n, sentence in enumerate(content["sentences"], 1):
                    hint = self.bold[n - 1] if n <= len(self.bold) else []
                    note = f"  (printed in bold: {', '.join(hint)})" if hint else ""
                    lines.append(f"Sentence {n}: {sentence['text']}{note}")
                if content["sentences"]:
                    lines.append(
                        f"targets must have exactly {len(content['sentences'])} entries, one list "
                        "per sentence; a bold phrase is split into its words, each with its sound."
                    )
            case "reading":
                lines += [p["text"] for p in content["paragraphs"]]
            case "rule_box":
                lines += [*content["points"], *content["examples"]]
            case "dialogue":
                lines.append("(the conversation is in the text below)")
            case "grid":
                lines.append("Columns: " + " | ".join(content["header"]))
                lines += [" | ".join(row) for row in content["rows"]]
                if (blanks := _grid_blanks(content["rows"])) > 0:
                    lines.append(
                        f"Blanks to fill: {blanks} (answers = one per blank, in reading order)"
                    )
            case "gallery":
                lines += [f"Question: {q}" for q in content["questions"]]
                lines.append(f"({len(content['images'])} pictures)")
            case "self_check":
                lines += [f"- {item}" for item in content["items"]]
        if self.listening:
            lines.append("(listening task: the audio script is not on the page)")
        return lines


_ANSWERED = frozenset({"gap_fill", "match", "choice", "true_false", "sound_sort"})
_LISTENING = re.compile(r"^\s*listen\b(?!\s+and\s+(read|repeat|practise|practice|number))", re.I)
_CONTROLLED = re.compile(
    r"^\s*(rewrite|fill|complete|match|choose|circle|put|order|underline|tick|decide|"
    r"write a (word|phrase)|find the|read the .*match|read .*decide)\b",
    re.IGNORECASE,
)
_PAREN_CHOICE = re.compile(r"\(\s*([^()/]+?(?:\s*/\s*[^()/]+?)+)\s*\)")
_NOW_I_CAN = re.compile(r"^\W*now i can\b", re.IGNORECASE)
_DECOR = re.compile(r"\b(lined|writing area|blank (space|lines?)|border|logo)\b", re.IGNORECASE)
_LABEL = re.compile(r"^\W*([A-Za-z]|\d{1,2})\W*(_+)?\W*$")


def _is_text(span: Span) -> bool:
    return span.kind not in (SpanKind.IMAGE, SpanKind.CAPTION) and bool(span.text.strip())


def _split_rows(span: Span) -> list[list[str]]:
    return [
        [" ".join(cell.split()) for cell in line.split("|")]
        for line in span.text.splitlines()
        if line.strip()
    ]


def _word_box(entries: Sequence[Entry]) -> list[str]:
    """Short unnumbered lines before the first numbered item: the words of a word box."""
    words: list[str] = []
    for _ref, span in entries:
        text = " ".join(span.text.split())
        if _NUMBERED.match(text):
            break
        if span.kind is SpanKind.TABLE:
            words += [cell for row in _split_rows(span) for cell in row if cell]
        elif text and len(text.split()) <= 4 and not _SENTENCE.search(text):
            words += [
                part.strip() for part in re.split(r"\s{2,}|\s/\s|\s\|\s|,", text) if part.strip()
            ]
        else:
            return []
    return words if len(words) >= 3 else []


def _numbered_items(entries: Sequence[Entry]) -> list[tuple[int, str, SpanRef]]:
    items: list[tuple[int, str, SpanRef]] = []
    for ref, span in entries:
        if span.kind is SpanKind.TABLE:
            continue
        match = _NUMBERED.match(" ".join(span.text.split()))
        if match and match.group(2).strip():
            items.append((int(match.group(1)), match.group(2).strip(), ref))
    return items


def _is_option_table(span: Span) -> bool:
    """A table whose every cell is a lettered option (``A. while | B. when``)."""
    cells = [cell for row in _split_rows(span) for cell in row if cell]
    return len(cells) >= 2 and all(_OPTION.match(cell) for cell in cells)


def _choice_items(entries: Sequence[Entry]) -> list[dict[str, Any]] | None:
    """Questions with lettered options (one line each, or ``A. x B. y`` inline), else ``None``."""
    items: list[dict[str, Any]] = []
    for _ref, span in entries:
        if span.kind is SpanKind.TABLE:
            if items and _is_option_table(span):  # options set in two columns: A | B / C | D
                cells = [cell for row in _split_rows(span) for cell in row if cell]
                items[-1]["options"] += [_OPTION.match(cell).group(2).strip() for cell in cells]
            continue
        text = " ".join(span.text.split())
        numbered = _NUMBERED.match(text)
        option = _OPTION.match(text)
        if numbered:
            pieces = _INLINE_OPTION.split(numbered.group(2).strip())
            items.append({"stem": pieces[0].strip(), "options": []})
            for piece in pieces[1:]:
                found = _OPTION.match(piece)
                if found:
                    items[-1]["options"].append(found.group(2).strip())
        elif option and items:
            items[-1]["options"].append(option.group(2).strip())
    if not items or any(len(item["options"]) < 2 for item in items):
        return None
    return items


def _speaker_lines(entries: Sequence[Entry]) -> list[tuple[str, str, SpanRef]]:
    lines: list[tuple[str, str, SpanRef]] = []
    for ref, span in entries:
        if span.kind not in (SpanKind.PARAGRAPH, SpanKind.LIST_ITEM):
            continue
        match = _SPEAKER.match(" ".join(span.text.split()))
        if match:
            lines.append((match.group(1), match.group(2), ref))
    return lines


_GROUPING = re.compile(r"^(work|talk) (in|with|on)\b|^in (pairs|groups)", re.IGNORECASE)
_AI_LABEL = re.compile(r"[\s(\[-]*\bAI[- ]added\b[\s)\]-]*", re.IGNORECASE)


_TITLE_TAIL = frozenset(
    {
        "a",
        "an",
        "the",
        "on",
        "in",
        "of",
        "to",
        "how",
        "about",
        "your",
        "their",
        "and",
        "for",
        "with",
    }
)


def _clean_title(title: str) -> str:
    """A title without the ``AI-added`` label a model may write into it (the deck adds its own)."""
    return _AI_LABEL.sub(" ", title).strip(" -:")


def _short_title(instruction: str) -> str:
    """A title for an activity: its first sentence, skipping a grouping line (*Work in groups.*),
    cut at six words when it is long."""
    parts = re.split(r"(?<=[.!?])\s", instruction)
    sentences = [part.strip().rstrip(".") for part in parts if part.strip()]
    chosen = next(
        (s for s in sentences if not _GROUPING.match(s)), sentences[0] if sentences else ""
    )
    if 0 < len(chosen) <= 60:
        return chosen
    words = chosen.split()[:6]
    while len(words) > 2 and words[-1].lower().strip(",;:") in _TITLE_TAIL:
        words.pop()
    return " ".join(words).rstrip(",;:") if words else "Activity"


def _activity_skills(instruction: str) -> list[str]:
    lowered = instruction.lower()
    skills = [
        skill
        for skill, words in (
            ("listening", ("listen",)),
            ("speaking", ("talk", "say", "discuss", "ask", "tell", "present", "speak")),
            ("reading", ("read",)),
            ("writing", ("write", "complete", "fill")),
        )
        if any(word in lowered for word in words)
    ]
    return skills or ["speaking"]


def _activity_interaction(instruction: str) -> str:
    lowered = instruction.lower()
    if "group" in lowered:
        return Interaction.GROUPS.value
    if "pair" in lowered or "partner" in lowered:
        return Interaction.PAIRS.value
    return Interaction.WHOLE_CLASS.value


def _phase_of(kind: str, instruction: str, content: Mapping[str, Any]) -> Phase:
    """The phase an exercise belongs to by what it is; a lesson type may override it."""
    if kind in ("dialogue", "reading", "rule_box"):
        return Phase.PRESENTATION
    if kind in _ANSWERED:
        return Phase.PRACTICE
    if kind == "self_check":
        return Phase.PRODUCTION
    if kind in ("question_set", "gallery", "grid"):
        if kind == "grid" and _grid_blanks(content.get("rows", [])):
            return Phase.PRACTICE
        if _CONTROLLED.match(instruction):
            return Phase.PRACTICE
        if kind == "question_set":
            questions = " ".join(q["question"] for q in content.get("questions", []))
        else:
            questions = " ".join(content.get("questions", []))
        if _FREE.search(instruction) or re.search(r"\byou(r)?\b", questions, re.IGNORECASE):
            return Phase.PRODUCTION
        return Phase.PRACTICE
    return Phase.PRODUCTION if _PRODUCE.search(instruction) else Phase.PRACTICE


def _word_cells(span: Span) -> list[str] | None:
    """The words of a line like ``___ pack | ___ park`` (a numbered blank before each word), or
    ``None`` when the span is not a list of one or two-word cells."""
    cells: list[str] = []
    rows = _split_rows(span) if span.kind is SpanKind.TABLE else [span.text.split("|")]
    for row in rows:
        for raw in row:
            cell = _TRAILING_BLANKS.sub("", _LEADING_BLANKS.sub("", " ".join(raw.split())))
            if not cell:
                continue
            if not _WORD_CELL.fullmatch(cell):
                return None
            cells.append(cell)
    return cells


def _sound_sort(
    chunk: ExerciseChunk, texts: Sequence[Entry], items: Sequence[tuple[int, str, SpanRef]]
) -> tuple[dict[str, Any], list[SpanRef], list[list[str]]] | None:
    """A pronunciation exercise sorted by sound: word lists under a heading such as ``Vowel
    revision`` followed by the sounds between slashes, and numbered sentences whose instruction
    names the sounds (``Underline the bold words with ...``). ``None`` when the chunk is not one."""
    if len(chunk.sounds) < 2:
        return None
    words: list[str] = []
    used: list[SpanRef] = []
    sentences: list[tuple[str, SpanRef]] = []
    bold: list[list[str]] = []
    own = len(sounds_in(chunk.instruction)) >= 2
    for ref, span in texts:
        if span.kind not in (SpanKind.LIST_ITEM, SpanKind.PARAGRAPH, SpanKind.TABLE):
            continue
        cells = _word_cells(span)
        if cells is not None:
            words += cells
            used.append(ref)
            continue
        match = _NUMBERED.match(" ".join(span.text.split()))
        if own and match and match.group(2).strip():
            sentences.append((match.group(2).strip(), ref))
            bold.append(list(span.highlights))
            used.append(ref)
        else:
            return None
    if words and (
        len(words) < _MIN_SOUND_WORDS
        or not re.search(r"\b(listen|repeat|say|pronounc\w*)\b", chunk.instruction, re.IGNORECASE)
    ):
        return None
    if not words and not sentences:
        return None
    return (
        {
            "instruction": chunk.instruction,
            "sounds": list(chunk.sounds),
            "words": [{"word": word, "sound": ""} for word in words],
            "sentences": [{"text": text, "targets": []} for text, _ref in sentences],
        },
        used,
        bold,
    )


def _picture_match(
    chunk: ExerciseChunk, texts: Sequence[Entry], images: Sequence[Entry]
) -> tuple[dict[str, Any], list[SpanRef]] | None:
    """A word-to-picture exercise: an instruction, a word box, then numbered pictures.

    The pictures are the left column (each with its description as text), the words of the box
    the right column. ``None`` unless there are at least two pictures, numbered (``1. ____`` under
    each) or named by the instruction, and at least as many words as pictures.
    """
    if len(images) < 2:
        return None
    numbered = sum(
        1
        for _r, span in chunk.body
        if span.kind is SpanKind.CAPTION and _NUMBERED.match(span.text.strip())
    )
    if numbered < 2 and not _PICTURE_WORDS.search(chunk.instruction):
        return None
    box = _word_box(texts)
    if len(box) < len(images):
        return None
    left = [" ".join(span.text.split()) or f"Picture {n}" for n, (_r, span) in enumerate(images, 1)]
    content = {
        "instruction": chunk.instruction,
        "left": left,
        "right": box,
        "left_images": [_citation(ref, span).model_dump() for ref, span in images],
        "answer": {i: i for i in range(len(left))},
    }
    return content, [*(ref for ref, _s in texts), *(ref for ref, _s in images)]


_BULLET = re.compile("^[" + chr(0x2013) + chr(0x2014) + chr(0x2022) + "-]" + chr(92) + "s*")


def _plain(text: str) -> str:
    """The text on one line, without a leading bullet or dash."""
    return _BULLET.sub("", " ".join(text.split()))


def _split_instruction(instruction: str) -> tuple[str, str]:
    """``Read the brochure. Match each word.`` -> (``Read the brochure.``, ``Match each word.``).

    The wording is only cut at a sentence end; when it has no such cut, both parts are the whole
    instruction.
    """
    sentences = re.split(r"(?<=[.!?])\s+", instruction)
    cut = next((i for i, s in enumerate(sentences) if re.search(r"\bmatch", s, re.I)), 0)
    if cut == 0:
        return instruction, instruction
    return " ".join(sentences[:cut]), " ".join(sentences[cut:])


def _gallery_images(chunk_body: Sequence[Entry], images: Sequence[Entry]) -> list[dict[str, Any]]:
    """The pictures of a gallery with their labels (``a``, ``1``) or captions from the page."""
    index_of = {ref.span_id: n for n, (ref, _s) in enumerate(chunk_body)}
    claimed: set[int] = set()
    found: list[dict[str, Any]] = []
    for ref, span in images:
        at = index_of.get(ref.span_id, -1)
        label, caption = "", ""
        for neighbour in (at - 1, at + 1):
            if not 0 <= neighbour < len(chunk_body) or neighbour in claimed:
                continue
            _r, other = chunk_body[neighbour]
            if other.kind is not SpanKind.CAPTION:
                continue
            claimed.add(neighbour)
            text = " ".join(other.text.split())
            labelled = _LABEL.match(text)
            if labelled:
                label = labelled.group(1)
            else:
                caption = text
            break
        found.append(
            {
                "image": _citation(ref, span).model_dump(),
                "label": label,
                "caption": caption,
                "alt": " ".join(span.text.split()),
            }
        )
    return found[:MAX_GALLERY_IMAGES]


def _table_grid(rows: Sequence[Sequence[str]]) -> dict[str, Any]:
    """A table as a grid: the first row is the header, short rows are padded with empty cells."""
    width = max(len(row) for row in rows)
    padded = [[*row, *([""] * (width - len(row)))] for row in rows]
    header, body = padded[0], padded[1:]
    return {
        "header": header,
        "rows": body,
        "blank_rows": 0 if body else GRID_BLANK_ROWS,
    }


def _grid_blanks(rows: Sequence[Sequence[str]]) -> int:
    return sum(len(_BLANK.findall(cell)) for row in rows for cell in row)


def _table_choice(grid: Sequence[Sequence[str]]) -> list[dict[str, Any]] | None:
    """``In which place ...? | Hill | River`` over numbered statements: a choice per statement."""
    if len(grid) < 3:
        return None
    header, body = grid[0], grid[1:]
    options = [cell for cell in header[1:] if cell]
    if len(options) < 2 or len(set(options)) != len(options):
        return None
    if any(len(option.split()) > MAX_KEY_WORD_TOKENS + 1 for option in options):
        return None
    items: list[dict[str, Any]] = []
    for row in body:
        numbered = _NUMBERED.match(row[0]) if row else None
        if numbered is None or any(cell for cell in row[1:]):
            return None
        items.append(
            {"stem": numbered.group(2).strip(), "options": list(options), "answer": options[0]}
        )
    return items


def _paren_choices(gaps: Sequence[tuple[str, SpanRef]]) -> list[dict[str, Any]] | None:
    """``The villagers (preserve / shorten) their skills.``: a choice with the stem as a blank."""
    items: list[dict[str, Any]] = []
    for text, _ref in gaps:
        found = _PAREN_CHOICE.search(text)
        if found is None:
            return None
        options = [option.strip() for option in found.group(1).split("/") if option.strip()]
        if len(options) < 2 or len(set(options)) != len(options):
            return None
        stem = _PAREN_CHOICE.sub("___", text, count=1)
        items.append({"stem": stem, "options": options, "answer": options[0]})
    return items if len(items) >= 2 else None


def _question_like(text: str) -> bool:
    plain = _NUMBERED.sub(lambda m: m.group(2), _plain(text))
    return plain.endswith("?")


def build_exercise(chunk: ExerciseChunk, index: int) -> tuple[Exercise, list[Exercise]] | None:
    """The exercise a chunk holds, plus the exercises that come after it (follow-up questions, the
    match of a reading); ``None`` for a chunk with nothing to teach.

    The kind follows from the spans: a dialogue from ``Speaker: text`` lines, a rule box from a
    *Remember!* heading, the self-check from *Now I can*, true/false from a T/F instruction, a
    match from a two-column table (with the text as a reading when the instruction says to read it
    first), a choice from lettered options, from ``(a / b)`` items or from a table of options, a
    gap fill from numbered sentences with blanks, a reading from a long paragraph, a gallery from
    pictures with their questions, a grid from any other table, a question set from other numbered
    lines; anything else is an activity that keeps the instruction as written.
    """
    exercise_id = f"e{index}"
    texts = [(ref, span) for ref, span in chunk.body if _is_text(span)]
    images = [
        (ref, span)
        for ref, span in chunk.body
        if span.kind is SpanKind.IMAGE and not _DECOR.search(span.text)
    ]
    first_text = next((i for i, (_r, s) in enumerate(chunk.body) if _is_text(s)), len(chunk.body))
    first_image = next(
        (i for i, (_r, s) in enumerate(chunk.body) if s.kind is SpanKind.IMAGE), None
    )
    before = images if first_image is not None and first_image < first_text else []
    after = [] if before else images
    refs = [chunk.ref] if chunk.ref else []
    if chunk.title_ref:
        refs.append(chunk.title_ref)
    instruction = chunk.instruction
    items = _numbered_items(texts)

    def make(
        kind: str,
        content: dict[str, Any],
        used: Sequence[SpanRef],
        *,
        eid: str = exercise_id,
        text: str | None = None,
    ) -> Exercise:
        said = instruction if text is None else text
        return Exercise(
            id=eid,
            number=chunk.number,
            kind=kind,
            instruction=said,
            content=content,
            refs=[*refs, *used],
            phase=_phase_of(kind, said, content),
            before=before,
            after=after,
            heading=chunk.heading,
            track=chunk.track,
            project=chunk.project,
        )

    def with_follow_ups(
        main: Exercise, extra: Sequence[Exercise] = ()
    ) -> tuple[Exercise, list[Exercise]]:
        follow: list[Exercise] = [*extra]
        for n, (ref, span) in enumerate(chunk.follow_ups, start=1):
            question = " ".join(span.text.split())
            content = {"prompt": "", "questions": [{"question": question}]}
            follow.append(
                Exercise(
                    id=f"{exercise_id}{chr(96 + n)}",
                    number=None,
                    kind="question_set",
                    instruction="",
                    content={"instruction": "", **content},
                    refs=[ref],
                    phase=_phase_of("question_set", question, content),
                    heading=chunk.heading,
                    project=chunk.project,
                )
            )
        if follow and main.after and chunk.follow_ups:
            # A lone picture beside a discussion question is the question's picture: one slide.
            last, pictures = follow[-1], main.after
            main.after = []
            last.kind = "gallery"
            last.content = {
                "instruction": "",
                "title": "",
                "images": _gallery_images(chunk.body, pictures),
                "questions": [last.content["questions"][0]["question"]],
            }
            last.refs = [*last.refs, *(ref for ref, _s in pictures)]
        return main, follow

    if chunk.self_check:
        lines = [(ref, _plain(span.text)) for ref, span in texts if span.kind is not SpanKind.TABLE]
        for ref, span in texts:  # items that were read as the rows of the table
            if span.kind is SpanKind.TABLE:
                lines += [(ref, _plain(row[0])) for row in _split_rows(span)[1:] if row and row[0]]
        lines = [(ref, text) for ref, text in lines if text]
        if not lines:
            return None
        content = {
            "instruction": "",
            "title": "Now I can ...",
            "items": [text for _r, text in lines],
        }
        exercise = make("self_check", content, [ref for ref, _t in lines])
        exercise.number, exercise.before, exercise.after = None, [], []
        return exercise, []

    if chunk.rule:
        used = [ref for ref, _span in texts]
        if chunk.heading_ref:
            used.append(chunk.heading_ref)
        examples_at = [(ref, span) for ref, span in chunk.examples if _is_text(span)]
        if examples_at:
            points = [_plain(span.text) for _r, span in texts]
            examples = [_plain(span.text) for _r, span in examples_at]
            used += [ref for ref, _span in examples_at]
            if chunk.example_ref:
                used.append(chunk.example_ref)
        else:
            points = [_plain(s.text) for _r, s in texts if s.kind is not SpanKind.LIST_ITEM]
            examples = [_plain(s.text) for _r, s in texts if s.kind is SpanKind.LIST_ITEM]
        title = instruction or "Remember!"
        if chunk.heading and chunk.heading.lower() not in title.lower():
            title = f"{title} {chunk.heading}"
        content = {"title": title, "points": points, "examples": examples}
        exercise = make("rule_box", content, used)
        exercise.number, exercise.instruction = None, ""
        exercise.before, exercise.after = [], []
        return exercise, []

    sorted_sounds = _sound_sort(chunk, texts, items)
    if sorted_sounds is not None:
        content, used, bold = sorted_sounds
        sound_sort = make("sound_sort", content, used)
        sound_sort.bold = bold
        return with_follow_ups(sound_sort)

    pictures = _picture_match(chunk, texts, images)
    if pictures is not None:
        content, used = pictures
        before, after = [], []
        return with_follow_ups(make("match", content, used))

    speakers = _speaker_lines(texts)
    if len(speakers) >= 2 and len({name for name, _t, _r in speakers}) >= 2:
        lines = [{"speaker": name, "text": text} for name, text, _r in speakers]
        spoken = " ".join(text for _n, text, _r in speakers).lower()
        highlights: list[str] = []
        for _ref, span in texts:
            for phrase in span.highlights:
                if phrase.lower() in spoken and phrase not in highlights:
                    highlights.append(phrase)
        content = {"lines": lines, "highlights": highlights}
        return with_follow_ups(make("dialogue", content, [r for _n, _t, r in speakers]))

    rows = [(ref, _split_rows(span)) for ref, span in texts if span.kind is SpanKind.TABLE]
    table_rows = [row for _ref, grid in rows for row in grid]
    is_true_false = bool(_TRUE_FALSE.search(instruction) or _TRUE_FALSE_LETTERS.search(instruction))
    if is_true_false:
        statements = [(text, ref) for _n, text, ref in items]
        if not statements and table_rows:
            table_ref = rows[0][0]
            for row in table_rows:
                numbered = _NUMBERED.match(row[0])
                if numbered and numbered.group(2).strip():
                    statements.append((numbered.group(2).strip(), table_ref))
        if statements:
            content = {
                "instruction": instruction,
                "items": [{"statement": text, "answer": True} for text, _r in statements],
            }
            return with_follow_ups(make("true_false", content, [r for _t, r in statements]))

    paragraphs = [
        (" ".join(s.text.split()), r, s)
        for r, s in texts
        if s.kind is SpanKind.PARAGRAPH and not _NUMBERED.match(s.text)
    ]
    long_text = sum(len(t) for t, _r, _s in paragraphs) >= _MIN_READING_CHARS
    two_columns = len(table_rows) >= 2 and all(len(row) == 2 for row in table_rows)
    lettered = two_columns and all(_LETTERED.match(row[1]) for row in table_rows)
    option_tables = bool(rows) and all(
        _is_option_table(span) for _r, span in texts if span.kind is SpanKind.TABLE
    )
    is_match = (
        two_columns
        and not option_tables
        and (lettered or re.search(r"\bmatch", instruction, re.IGNORECASE))
    )

    def match_content(text: str) -> dict[str, Any]:
        def unnumber(cell: str) -> str:
            found = _NUMBERED.match(cell)
            return found.group(2).strip() if found else cell

        return {
            "instruction": text,
            "left": [unnumber(row[0]) for row in table_rows],
            "right": [
                (_LETTERED.match(row[1]).group(2).strip() if _LETTERED.match(row[1]) else row[1])
                for row in table_rows
            ],
            "answer": {i: i for i in range(len(table_rows))},
        }

    if is_match and long_text and re.search(r"\bread\b", instruction, re.IGNORECASE):
        # A text and the matching of its highlighted words: the text is shown first, as a reading.
        read_text, match_text = _split_instruction(instruction)
        reading = _reading_exercise(chunk, make, paragraphs, read_text, f"{exercise_id}m")
        matched = make(
            "match",
            match_content(match_text),
            [ref for ref, _g in rows],
            eid=f"{exercise_id}m",
            text=match_text,
        )
        matched.before, matched.after = [], []
        return with_follow_ups(reading, [matched])

    if is_match:
        return with_follow_ups(make("match", match_content(instruction), [r for r, _g in rows]))

    picks = re.search(r"\b(decide|which|choose|select)\b", instruction, re.IGNORECASE)
    choice_table = (
        _table_choice(table_rows)
        if table_rows and not items and picks and "table" not in instruction.lower()
        else None
    )
    if choice_table:
        content = {"instruction": instruction, "items": choice_table}
        return with_follow_ups(make("choice", content, [ref for ref, _g in rows]))

    choices = _choice_items(texts)
    if choices:
        content = {
            "instruction": instruction,
            "items": [{**item, "answer": item["options"][0]} for item in choices],
        }
        used = [
            ref for ref, span in texts if span.kind is not SpanKind.TABLE or _is_option_table(span)
        ]
        return with_follow_ups(make("choice", content, used))

    gaps = [(text, ref) for _n, text, ref in items if _BLANK.search(text)]
    if gaps:
        box = _word_box(texts)
        content = {
            "instruction": instruction,
            "items": [{"text": text, "answer": "?"} for text, _r in gaps],
            "word_box": box,
        }
        used = [ref for ref, span in texts if span.kind is not SpanKind.TABLE]
        return with_follow_ups(make("gap_fill", content, used))

    circled = _paren_choices([(text, ref) for _n, text, ref in items])
    if circled:
        content = {"instruction": instruction, "items": circled}
        return with_follow_ups(make("choice", content, [r for _n, _t, r in items]))

    bulleted = any(span.kind is SpanKind.LIST_ITEM for _r, span in texts)
    if len(items) < 2 and long_text and not bulleted:
        return with_follow_ups(_reading_exercise(chunk, make, paragraphs, instruction, None))

    if images and not table_rows and all(_question_like(span.text) for _r, span in texts):
        questions = [(ref, _plain(span.text)) for ref, span in texts]
        content = {
            "instruction": instruction,
            "title": "",
            "images": _gallery_images(chunk.body, images),
            "questions": [
                _NUMBERED.sub(lambda m: m.group(2), text).strip() for _r, text in questions
            ],
        }
        used = [ref for ref, _t in questions] + [ref for ref, _s in images]
        gallery = make("gallery", content, used)
        gallery.before, gallery.after = [], []
        return with_follow_ups(gallery)

    if table_rows and not items:
        grid = _table_grid(table_rows)
        content = {"instruction": instruction, **grid, "answers": []}
        return with_follow_ups(make("grid", content, [ref for ref, _g in rows]))

    if items:
        lines = [
            (_NUMBERED.sub(lambda m: m.group(2), _plain(span.text)).strip(), ref)
            for ref, span in texts
            if span.kind is SpanKind.LIST_ITEM
        ]
        asked = [(text, ref) for _n, text, ref in items]
        asked = lines if len(lines) > len(asked) else asked  # a key printed without numbers
        content = {
            "instruction": instruction,
            "prompt": "",
            "questions": [{"question": text} for text, _r in asked],
        }
        return with_follow_ups(make("question_set", content, [r for _t, r in asked]))

    questions = [
        (" ".join(s.text.split()), r) for r, s in texts if " ".join(s.text.split()).endswith("?")
    ]
    if questions:
        asked = {ref.span_id for _t, ref in questions}
        notes = [(text, ref) for text, ref, _s in paragraphs if ref.span_id not in asked]
        content = {
            "instruction": instruction,
            "prompt": " ".join(text for text, _r in notes),
            "questions": [{"question": _plain(text)} for text, _r in questions],
        }
        used = [r for _t, r in questions] + [r for _t, r in notes]
        return with_follow_ups(make("question_set", content, used))

    content = {
        "type": ActivityType.OTHER.value,
        "title": _short_title(instruction),
        "skills": _activity_skills(instruction),
        "interaction": _activity_interaction(instruction),
        "instructions": instruction,
        # the instruction is the slide's line; the page's own lines under it (a starter) are steps
        "student_steps": [_plain(s.text) for _r, s in texts if s.text.strip()] or [instruction],
    }
    exercise = make("activity", content, [r for r, _s in texts])
    if chunk.examples:
        return with_follow_ups(*_with_example(exercise, chunk, f"{exercise_id}x"))
    return with_follow_ups(exercise)


def _with_example(
    exercise: Exercise, chunk: ExerciseChunk, example_id: str
) -> tuple[Exercise, list[Exercise]]:
    """An activity and, after it, the textbook's worked example as a reading (``Example:``)."""
    spans = [(ref, span) for ref, span in chunk.examples if _is_text(span)]
    if not spans:
        return exercise, []
    paragraphs = [{"text": _plain(span.text)} for _r, span in spans]
    refs = [ref for ref, _s in spans]
    if chunk.example_ref:
        refs.insert(0, chunk.example_ref)
    example = Exercise(
        id=example_id,
        number=None,
        kind="reading",
        instruction="",
        content={"instruction": "", "title": "Example", "paragraphs": paragraphs},
        refs=refs,
        phase=Phase.PRESENTATION,
        heading=chunk.heading,
        project=chunk.project,
    )
    return exercise, [example]


def _reading_exercise(
    chunk: ExerciseChunk,
    make: Any,
    paragraphs: Sequence[tuple[str, SpanRef, Span]],
    instruction: str,
    pair: str | None,
) -> Exercise:
    """A reading of the chunk's paragraphs; a picture before a paragraph goes with it."""
    image_of: dict[str, dict[str, Any]] = {}
    waiting: tuple[SpanRef, Span] | None = None
    for ref, span in chunk.body:
        if span.kind is SpanKind.IMAGE and not _DECOR.search(span.text):
            waiting = (ref, span)
        elif waiting is not None and any(ref.span_id == r.span_id for _t, r, _s in paragraphs):
            image_of[ref.span_id] = _citation(*waiting).model_dump()
            waiting = None
    content: dict[str, Any] = {
        "instruction": instruction,
        "title": chunk.title,
        "paragraphs": [
            {"text": text, **({"image": image_of[ref.span_id]} if ref.span_id in image_of else {})}
            for text, ref, _s in paragraphs
        ],
    }
    first = next(iter(image_of.values()), None)
    if first is not None:
        content["image"] = first
    reading = make("reading", content, [ref for _t, ref, _s in paragraphs], text=instruction)
    reading.pair = pair
    if first is not None:
        reading.before, reading.after = [], []
    return reading


# --- the plan ----------------------------------------------------------------------------------


@dataclass
class SecondaryPlan:
    """Everything code knows about one lesson before any model call."""

    unit: Unit
    lesson_no: int
    lesson_name: str
    title: str
    topic: str
    exercises: list[Exercise]
    spans: list[Entry]
    """The lesson's text spans, in reading order (the ones a vocabulary item may cite)."""
    outcomes: list[Outcome]
    total_minutes: int
    docs: dict[str, SourceDocument]
    key_words: list[str] = field(default_factory=list)
    """The words the lesson's vocabulary exercises list: the vocabulary block must hold them."""
    blueprint: Blueprint = field(default_factory=lambda: blueprint_for(1))
    """The plan of this lesson type (``planning/blueprints.py``)."""

    @property
    def needs_production(self) -> bool:
        """Whether no exercise of the page is a free task: the model then writes a short one."""
        return not self.phase_exercises(Phase.PRODUCTION)

    def phases(self, *, vocabulary: bool) -> list[Phase]:
        """The phases that hold something: the warm-up, the production (the page's free tasks, or
        a short speaking task written for the lesson), the wrap-up, the exercises' phases and,
        with new words, the presentation."""
        wanted = {
            Phase.WARM_UP,
            Phase.PRODUCTION,
            Phase.WRAP_UP,
            *(e.phase for e in self.exercises),
        }
        if vocabulary:
            wanted.add(Phase.PRESENTATION)
        return [phase for phase in PHASE_MINUTES if phase in wanted]

    def minutes(self, *, vocabulary: bool | None = None) -> dict[Phase, int]:
        """The 45-minute skeleton over :meth:`phases` (the new words only where the lesson type
        has them)."""
        with_words = self.blueprint.new_words if vocabulary is None else vocabulary
        return phase_minutes(
            self.phases(vocabulary=with_words), self.total_minutes, self.blueprint.weights
        )

    def phase_exercises(self, phase: Phase) -> list[Exercise]:
        return [exercise for exercise in self.exercises if exercise.phase is phase]


def phase_minutes(
    phases: Sequence[Phase], total: int, weights: Mapping[Phase, int] | None = None
) -> dict[Phase, int]:
    """The skeleton minutes of ``phases`` scaled to sum to ``total`` (the period length)."""
    base = weights or PHASE_MINUTES
    ordered = [phase for phase in PHASE_MINUTES if phase in phases]
    values = scale_minutes([base.get(phase, PHASE_MINUTES[phase]) for phase in ordered], total)
    return dict(zip(ordered, values, strict=True))


def unit_label(unit: Unit) -> str:
    if unit.textbook_units:
        return f"Unit {unit.textbook_units[0].unit} {unit.title}"
    return unit.title


def plan_lesson(
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    *,
    unit_id: str,
    lesson_no: int,
) -> SecondaryPlan:
    """The deterministic part: segmentation, exercises, phases and the 45-minute skeleton.

    Raises ``KeyError`` for an unknown unit and ``ValueError`` for a lesson number outside 1-7
    or a lesson whose banner is not in the ingested pages.
    """
    unit = curriculum.unit(unit_id)
    if lesson_no not in LESSON_NAMES:
        raise ValueError(f"lesson must be 1-7 for a secondary unit, got {lesson_no}")
    lessons = split_lessons(sources)
    found = lessons.get(lesson_no)
    if found is None:
        banner = LESSON_NAMES[lesson_no].upper()
        have = ", ".join(str(n) for n in sorted(lessons)) or "none"
        raise ValueError(
            f"the ingested pages have no {banner} banner (lessons found: {have}); "
            "ingest the pages of that lesson"
        )
    found.entries = [(ref, _british(span)) for ref, span in found.entries]
    blueprint = blueprint_for(lesson_no)
    exercises = _build_exercises(chunk_lesson(found), blueprint)
    spans = [(ref, span) for ref, span in found.entries if _is_text(span)]
    topic = next(
        (
            " ".join(span.text.split())
            for _ref, span in found.entries
            if span.kind is SpanKind.HEADING and span.text.strip().lower() not in PART_HEADINGS
        ),
        "",
    )
    name = LESSON_NAMES[lesson_no]
    outcomes = [o for o in unit.outcomes if lesson_no in o.lessons]
    return SecondaryPlan(
        unit=unit,
        lesson_no=lesson_no,
        lesson_name=name,
        title=lesson_label_for(curriculum.file.grade, lesson_no, None),
        topic=topic,
        exercises=exercises,
        spans=spans,
        outcomes=outcomes,
        total_minutes=curriculum.period_minutes,
        docs={doc.doc_id: doc for doc in sources},
        key_words=_key_words(exercises) if blueprint.new_words else [],
        blueprint=blueprint,
    )


def _british(span: Span) -> Span:
    """The span with the American spellings a vision model may have read corrected."""
    if span.kind is SpanKind.IMAGE:
        return span
    text = britishise(span.text)
    return span if text == span.text else span.model_copy(update={"text": text})


def _build_exercises(chunks: Sequence[ExerciseChunk], blueprint: Blueprint) -> list[Exercise]:
    """Every chunk as exercises, the project's opening, then the phases of the lesson type."""
    exercises: list[Exercise] = []
    project_steps = [c.instruction for c in chunks if c.project and not c.self_check]
    opened = False
    for number, chunk in enumerate(chunks, start=1):
        if chunk.project and not opened:
            opened = True
            exercises += _project_opening(chunk, number, project_steps)
        built = build_exercise(chunk, number)
        if built is None:
            continue
        main, follow = built
        exercises += [main, *follow]
    assign_phases(blueprint.number, exercises)
    return _order_by_phase(exercises)


def _project_opening(chunk: ExerciseChunk, number: int, steps: Sequence[str]) -> list[Exercise]:
    """The project's pictures as one gallery and its task and steps as one activity (the cards)."""
    title = chunk.heading or "Project"
    opening: list[Exercise] = []
    refs = [chunk.heading_ref] if chunk.heading_ref else []
    pictures = [
        (ref, span)
        for ref, span in chunk.prelude
        if span.kind is SpanKind.IMAGE and not _DECOR.search(span.text)
    ]
    sentences = [
        (ref, _plain(span.text)) for ref, span in chunk.prelude if _is_text(span) and span.text
    ]
    if pictures:
        content = {
            "instruction": "",
            "title": title,
            "images": _gallery_images(chunk.prelude, pictures),
            "questions": [],
        }
        opening.append(
            Exercise(
                id="eg",
                number=None,
                kind="gallery",
                instruction="",
                content=content,
                refs=[*refs, *(ref for ref, _s in pictures)],
                phase=Phase.PRODUCTION,
                heading=chunk.heading,
                project=True,
            )
        )
    content = {
        "type": ActivityType.PROJECT.value,
        "title": title,
        "skills": ["speaking", "writing"],
        "interaction": Interaction.GROUPS.value,
        "instructions": " ".join(text for _r, text in sentences),
        "student_steps": list(steps),
    }
    opening.append(
        Exercise(
            id="ep",
            number=None,
            kind="project",
            instruction=content["instructions"],
            content=content,
            refs=[*refs, *(ref for ref, _t in sentences)],
            phase=Phase.PRODUCTION,
            heading=chunk.heading,
            project=True,
        )
    )
    return opening


def _lead_in(exercises: Sequence[Exercise], part: Sequence[Exercise], reading: bool) -> set[str]:
    """The ids of the exercises that open a part (pictures and questions before the first task)."""
    lead: set[str] = set()
    for exercise in part:
        if exercise.kind in ("gallery", "question_set") or (reading and exercise.kind == "reading"):
            lead.add(exercise.id)
        else:
            break
    return lead


def assign_phases(number: int, exercises: list[Exercise]) -> None:
    """Set the phase of every exercise as the lesson type's blueprint says.

    Types 1 and 2 follow what the exercises are (conversation and rule boxes present, controlled
    exercises practise, free tasks produce). The others are fixed by the book's parts.
    """
    rules = [e for e in exercises if e.kind == "rule_box"]
    for n, rule in enumerate(rules):
        rule.first_rule = n == 0
    first_heading = exercises[0].heading if exercises else ""
    heading_of = {id(e): e.heading.lower() for e in exercises}
    if number in (5, 6):
        key = "speaking" if number == 5 else "writing"
        parts = [e for e in exercises if key not in heading_of[id(e)] and e.heading]
        lead = _lead_in(exercises, parts, reading=number == 5)
    else:
        lead = set()
    for e in exercises:
        base, heading = e.phase, heading_of[id(e)]
        if number == 7:
            e.phase = Phase.PRODUCTION if e.project else Phase.PRACTICE
        elif number == 3:
            if e.kind == "rule_box":
                e.phase = Phase.PRESENTATION if e.first_rule else Phase.PRACTICE
        elif number == 4:
            if e.kind == "dialogue":
                e.phase = Phase.PRESENTATION
            elif e.kind == "gallery" or e.heading == first_heading:
                e.phase = Phase.PRACTICE
        elif number in (5, 6):
            key = "speaking" if number == 5 else "writing"
            if key in heading or (not heading and base is Phase.PRODUCTION):
                e.phase = Phase.PRODUCTION
            elif e.id in lead:
                e.phase = Phase.PRESENTATION
            elif e.kind != "reading":
                e.phase = Phase.PRACTICE


_PHASE_ORDER = [Phase.PRESENTATION, Phase.PRACTICE, Phase.PRODUCTION]


def _order_by_phase(exercises: list[Exercise]) -> list[Exercise]:
    """Presentation first, then practice, then production; the textbook order inside each phase.

    A follow-up question such as *What other community helpers can you add?* is a free task, so it
    comes after the controlled exercises that the page prints after it.
    """
    return sorted(exercises, key=lambda exercise: _PHASE_ORDER.index(exercise.phase))


def _word_key(word: str) -> str:
    """One key for ``specialities`` and ``speciality``, ``artisans`` and ``artisan``."""
    key = word.lower().strip()
    for ending, replacement in (("ities", "ity"), ("ies", "y"), ("s", "")):
        if key.endswith(ending) and len(key) > len(ending) + 2:
            key = key[: -len(ending)] + replacement
            break
    return key


MAX_KEY_WORD_TOKENS = 3


def _key_words(exercises: Sequence[Exercise]) -> list[str]:
    """The words the lesson's vocabulary exercises list, once each, in page order.

    The left column of a matching exercise with words on the left and longer explanations on the
    right, the right column of a word-to-picture exercise and the word box of a gap fill.
    """
    found: dict[str, str] = {}

    def add(words: Sequence[str]) -> None:
        for word in words:
            clean = " ".join(word.split())
            if clean and len(clean.split()) <= MAX_KEY_WORD_TOKENS:
                found.setdefault(_word_key(clean), clean)

    for exercise in exercises:
        content = exercise.content
        vocabulary = "vocab" in exercise.heading.lower()
        if exercise.kind == "match":
            if content.get("left_images"):
                add(content["right"])
            elif (
                vocabulary
                and _is_word_column(content["left"])
                and not _is_word_column(content["right"])
            ):
                add(content["left"])
        elif exercise.kind == "gap_fill" and vocabulary:
            add(content.get("word_box", []))
    return list(found.values())


def _is_word_column(texts: Sequence[str]) -> bool:
    return bool(texts) and all(len(text.split()) <= MAX_KEY_WORD_TOKENS for text in texts)


# --- the model reply ---------------------------------------------------------------------------


class TargetReply(BaseModel):
    """A target word of a sentence and its sound."""

    model_config = ConfigDict(extra="ignore")

    word: str
    sound: str


class ExerciseReply(BaseModel):
    """Answers and teacher steps for one exercise."""

    model_config = ConfigDict(extra="ignore")

    exercise: str
    gaps: list[list[str]] = []
    letters: list[str] = []
    truths: list[bool] = []
    answers: list[str] = []
    word_sounds: list[str] = []
    targets: list[list[TargetReply]] = []
    glossary: dict[str, str] = {}
    steps: list[str] = []
    minutes: int = Field(default=4, ge=0, le=45)

    @field_validator("gaps", mode="before")
    @classmethod
    def _one_string_per_item(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [[item] if isinstance(item, str) else item for item in value]
        return value

    @field_validator("letters", mode="before")
    @classmethod
    def _letters_as_text(cls, value: Any) -> Any:
        return [str(item) for item in value] if isinstance(value, list) else value

    @field_validator("word_sounds", mode="before")
    @classmethod
    def _sounds_as_text(cls, value: Any) -> Any:
        return [str(item) for item in value] if isinstance(value, list) else value

    @field_validator("answers", mode="before")
    @classmethod
    def _answers_as_text(cls, value: Any) -> Any:
        return [str(item) for item in value] if isinstance(value, list) else value


class VocabReply(BaseModel):
    model_config = ConfigDict(extra="ignore")

    word: str
    ipa: str = ""
    pos: str = ""
    meaning_vi: str = ""
    example: str = ""
    span: str = ""


class PhaseReply(BaseModel):
    """A warm-up or a wrap-up written by the model."""

    model_config = ConfigDict(extra="ignore")

    title: str
    instructions: str
    teacher_steps: list[str] = []
    student_steps: list[str] = []
    interaction: Literal["whole_class", "pairs", "groups", "individual"] = "whole_class"
    skills: list[Literal["listening", "speaking", "reading", "writing"]] = []
    materials: list[str] = []


class FrameReply(BaseModel):
    """An AI-added frame: sentence starters (a presentation) or linking words (a paragraph)."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    instructions: str = ""
    starters: list[str] = []
    linking_words: list[str] = []
    word_count: int = 0
    teacher_steps: list[str] = []


class ParagraphReply(BaseModel):
    """A short model paragraph written for the writing task."""

    model_config = ConfigDict(extra="ignore")

    title: str = ""
    text: str = ""


class SecondaryDraft(BaseModel):
    """The one reply of the drafting call."""

    model_config = ConfigDict(extra="ignore")

    exercises: list[ExerciseReply]
    vocabulary: list[VocabReply] = []
    warm_up: PhaseReply
    production: PhaseReply | None = None
    wrap_up: PhaseReply
    homework: str = ""
    summary: list[str] = []
    speaking_frame: FrameReply | None = None
    writing_frame: FrameReply | None = None
    model_paragraph: ParagraphReply | None = None


Problems = dict[str, list[str]]
"""Problems by part of the reply: an exercise id, ``vocabulary:<word>``, ``warm_up``, ..."""


def _letter_index(letter: str) -> int | None:
    stripped = letter.strip().rstrip(".)").lower()
    return ord(stripped) - 97 if len(stripped) == 1 and stripped.isalpha() else None


def sound_key(sound: str, sounds: Sequence[str]) -> str | None:
    """The sound of ``sounds`` that ``sound`` names (slashes, spaces and ``:`` for the length mark
    are tolerated), else ``None``."""
    clean = sound.strip().strip("/").strip().replace(":", _LENGTH_MARK)
    return clean if clean in sounds else None


def occurs_as_word(word: str, text: str) -> str | None:
    """``word`` as written in ``text`` (whole word, any case), else ``None``."""
    found = re.search(rf"(?<![\w'-]){re.escape(word.strip())}(?![\w'-])", text, re.IGNORECASE)
    return found.group() if found else None


def _sound_sort_problems(content: Mapping[str, Any], reply: ExerciseReply) -> list[str]:
    problems: list[str] = []
    sounds = content["sounds"]
    allowed = ", ".join(f"/{sound}/" for sound in sounds)
    if content["words"]:
        if len(reply.word_sounds) != len(content["words"]):
            problems.append(f"word_sounds needs {len(content['words'])} entries (one per word)")
        elif any(sound_key(sound, sounds) is None for sound in reply.word_sounds):
            problems.append(f"every word sound must be one of {allowed}")
    if content["sentences"]:
        if len(reply.targets) != len(content["sentences"]):
            problems.append(f"targets needs {len(content['sentences'])} entries (one per sentence)")
            return problems
        for n, (sentence, targets) in enumerate(
            zip(content["sentences"], reply.targets, strict=True), 1
        ):
            if not targets:
                problems.append(f"sentence {n}: name at least one target word")
            for target in targets:
                if occurs_as_word(target.word, sentence["text"]) is None:
                    problems.append(f"sentence {n}: {target.word!r} is not a word of the sentence")
                if sound_key(target.sound, sounds) is None:
                    problems.append(f"sentence {n}: the sound of {target.word!r} must be {allowed}")
    return problems


def _exercise_problems(exercise: Exercise, reply: ExerciseReply | None) -> list[str]:
    if reply is None:
        return ["no entry for this exercise"]
    content = exercise.content
    problems: list[str] = []
    match exercise.kind:
        case "gap_fill":
            items = content["items"]
            if len(reply.gaps) != len(items):
                return [f"gaps needs {len(items)} entries (one per numbered sentence)"]
            box = {word.lower() for word in content.get("word_box", [])}
            for n, (item, answers) in enumerate(zip(items, reply.gaps, strict=True), 1):
                blanks = len(_BLANK.findall(item["text"]))
                if len(answers) != blanks or not all(a.strip() for a in answers):
                    problems.append(f"sentence {n} has {blanks} blank(s); give one answer for each")
                elif box and any(a.strip().lower() not in box for a in answers):
                    problems.append(f"sentence {n}: answers must come from the word box")
        case "match":
            left, right = len(content["left"]), len(content["right"])
            indexes = [_letter_index(letter) for letter in reply.letters]
            if len(indexes) != left:
                problems.append(f"letters needs {left} entries (one per left item)")
            elif any(i is None or not 0 <= i < right for i in indexes):
                problems.append(f"every letter must be between a and {chr(96 + right)}")
            elif len(set(indexes)) != len(indexes):
                problems.append("each right-hand letter may be used only once")
        case "choice":
            items = content["items"]
            indexes = [_letter_index(letter) for letter in reply.letters]
            if len(indexes) != len(items):
                problems.append(f"letters needs {len(items)} entries (one per question)")
            else:
                for n, (item, index) in enumerate(zip(items, indexes, strict=True), 1):
                    if index is None or not 0 <= index < len(item["options"]):
                        problems.append(f"question {n}: the letter is not one of its options")
        case "true_false":
            if len(reply.truths) != len(content["items"]):
                problems.append(f"truths needs {len(content['items'])} entries (one per statement)")
        case "sound_sort":
            problems = _sound_sort_problems(content, reply)
        case "grid":
            blanks = _grid_blanks(content["rows"])
            if blanks and len(reply.answers) != blanks:
                problems.append(f"answers needs {blanks} entries (one per blank, in order)")
        case "question_set":
            if reply.answers and len(reply.answers) != len(content["questions"]):
                problems.append(
                    f"answers needs {len(content['questions'])} entries or none (one per question)"
                )
    return problems


def _normal(text: str) -> str:
    return " ".join(text.replace(_RSQUO, "'").replace(_LSQUO, "'").split()).lower()


_PLACEHOLDER_WORDS = frozenset({"sb", "sth", "sbd", "somebody", "something", "someone", "one's"})


def occurs_in(word: str, text: str) -> bool:
    """Whether ``word`` (a word or phrase) is written in ``text``, tolerating an inflection.

    Every content word of the phrase must occur, as a word or as the start of one
    (``facility`` in ``facilities``); ``somebody`` and ``sth`` in a phrase are ignored.
    """
    haystack = _normal(text)
    tokens = [t for t in re.findall(r"[a-z0-9']+", _normal(word)) if t not in _PLACEHOLDER_WORDS]
    if not tokens:
        return False
    for token in tokens:
        stem = token if len(token) <= 4 else token[: len(token) - 2]
        if not re.search(rf"(?<![a-z0-9']){re.escape(stem)}", haystack):
            return False
    return True


def _span_lookup(plan: SecondaryPlan) -> dict[str, tuple[SpanRef, Span]]:
    return {ref.span_id: (ref, span) for ref, span in plan.spans}


def vocabulary_span(entry: VocabReply, plan: SecondaryPlan) -> tuple[SpanRef, Span] | None:
    """The span a vocabulary entry cites; when that span does not hold the word, the first
    span of the lesson that does (models are often one span off); ``None`` when none does."""
    spans = _span_lookup(plan)
    cited = spans.get(entry.span.strip().split(":")[-1])
    if cited is not None and occurs_in(entry.word, cited[1].text):
        return cited
    body = (SpanKind.PARAGRAPH, SpanKind.LIST_ITEM, SpanKind.TABLE, SpanKind.CAPTION)
    return next(
        (
            (ref, span)
            for ref, span in plan.spans
            if span.kind in body and occurs_in(entry.word, span.text)
        ),
        None,
    )


def _has_phrase(phrase: str, text: str) -> bool:
    """Whether the words of ``phrase`` are written in ``text`` in that order (any case)."""
    pattern = r"\s+".join(re.escape(word) for word in _normal(phrase).split())
    return (
        bool(pattern)
        and re.search(rf"(?<![a-z0-9']){pattern}(?![a-z0-9'])", _normal(text)) is not None
    )


def written_word(word: str, plan: SecondaryPlan) -> str:
    """``word`` spelled as the lesson's text spells it.

    A word that is written in the text as it is stays. Otherwise a spelling variant of one of its
    words (``specialty`` for ``speciality``, ``color`` for ``colour``) that the text does have
    replaces it; a word with no variant in the text is returned unchanged (the vocabulary check
    then decides about it).
    """
    clean = " ".join(word.split())
    texts = [span.text for _ref, span in plan.spans]
    if not clean or any(_has_phrase(clean, text) for text in texts):
        return clean
    tokens = clean.split()
    for position, token in enumerate(tokens):
        for variant in variants(token.strip(".,;:!?")):
            candidate = " ".join([*tokens[:position], variant, *tokens[position + 1 :]])
            if any(_has_phrase(candidate, text) for text in texts):
                return candidate
    return clean


def missing_key_words(draft: SecondaryDraft, plan: SecondaryPlan) -> list[str]:
    """The words of the lesson's vocabulary exercises that the vocabulary does not list."""
    listed = {_word_key(entry.word) for entry in draft.vocabulary}
    return [word for word in plan.key_words if _word_key(word) not in listed]


def _vocab_problems(entry: VocabReply, plan: SecondaryPlan) -> list[str]:
    if not entry.word.strip():
        return ["a vocabulary entry has no word"]
    if vocabulary_span(entry, plan) is None:
        return [
            f"{entry.word!r} is not written anywhere in the lesson's text; use words of the text"
        ]
    return []


def _phase_problems(reply: PhaseReply) -> list[str]:
    problems: list[str] = []
    if not reply.title.strip() or not reply.instructions.strip():
        problems.append("title and instructions are required")
    if len([s for s in reply.teacher_steps if s.strip()]) < 2:
        problems.append("teacher_steps needs at least 2 steps")
    if len([s for s in reply.student_steps if s.strip()]) < 2:
        problems.append("student_steps needs at least 2 steps")
    return problems


def reply_problems(draft: SecondaryDraft, plan: SecondaryPlan) -> Problems:
    """Why parts of a reply cannot be used; empty when the whole reply is fine."""
    problems: Problems = {}
    by_id = {reply.exercise.strip(): reply for reply in draft.exercises}
    for exercise in plan.exercises:
        reply = by_id.get(exercise.id)
        if exercise.needs_answers or reply is not None:
            found = _exercise_problems(exercise, reply)
            if found:
                problems[exercise.id] = found
    seen: set[str] = set()
    for entry in draft.vocabulary if plan.blueprint.new_words else []:
        key = f"vocabulary:{entry.word.strip().lower()}"
        found = _vocab_problems(entry, plan)
        if key in seen:
            found = [f"{entry.word!r} is listed twice"]
        seen.add(key)
        if found:
            problems[key] = found
    missing = missing_key_words(draft, plan) if plan.blueprint.new_words else []
    if missing:
        problems["vocabulary:missing"] = [
            "the vocabulary must also list these words of the lesson's vocabulary exercises, "
            f"spelled as the text has them: {', '.join(missing)}"
        ]
    phases = [("warm_up", draft.warm_up), ("wrap_up", draft.wrap_up)]
    if plan.needs_production:
        phases.append(("production", draft.production))
    for name, phase in phases:
        found = _phase_problems(phase) if phase is not None else ["a production task is required"]
        if found:
            problems[name] = found
    for name, found in _extra_problems(draft, plan).items():
        problems[name] = found
    return problems


MIN_STARTERS = 3
MIN_PARAGRAPH_WORDS = 30
MAX_PARAGRAPH_WORDS = 200
MAX_SUMMARY_WORDS = 22


def _extra_problems(draft: SecondaryDraft, plan: SecondaryPlan) -> Problems:
    """Problems of the parts a lesson type adds: the frames, the model paragraph, the summary."""
    problems: Problems = {}
    extras = plan.blueprint.extras
    if "speaking_frame" in extras:
        frame, found = draft.speaking_frame, []
        if frame is None:
            found = ["a speaking_frame is required"]
        else:
            if not frame.instructions.strip():
                found.append("instructions are required")
            if len([x for x in frame.starters if x.strip()]) < MIN_STARTERS:
                found.append(f"starters needs at least {MIN_STARTERS} sentence starters")
            if len([x for x in frame.teacher_steps if x.strip()]) < 2:
                found.append("teacher_steps needs at least 2 steps")
        if found:
            problems["speaking_frame"] = found
    if "writing_frame" in extras:
        frame, found = draft.writing_frame, []
        if frame is None:
            found = ["a writing_frame is required"]
        else:
            if not frame.instructions.strip():
                found.append("instructions are required")
            if len([x for x in frame.linking_words if x.strip()]) < MIN_STARTERS:
                found.append(f"linking_words needs at least {MIN_STARTERS} words")
            if len([x for x in frame.teacher_steps if x.strip()]) < 2:
                found.append("teacher_steps needs at least 2 steps")
        if found:
            problems["writing_frame"] = found
    if "model_paragraph" in extras:
        model, found = draft.model_paragraph, []
        words = len(model.text.split()) if model is not None else 0
        if not MIN_PARAGRAPH_WORDS <= words <= MAX_PARAGRAPH_WORDS:
            found = [
                f"model_paragraph.text needs {MIN_PARAGRAPH_WORDS} to {MAX_PARAGRAPH_WORDS} words"
            ]
        if found:
            problems["model_paragraph"] = found
    if needs_model_summary(plan):
        bullets = [b for b in draft.summary if b.strip()]
        if not 2 <= len(bullets) <= 6:
            problems["summary"] = ["summary needs 2 to 6 short bullets"]
        elif any(len(b.split()) > MAX_SUMMARY_WORDS for b in bullets):
            problems["summary"] = [f"each summary bullet is at most {MAX_SUMMARY_WORDS} words"]
    return problems


SUMMARY_FROM_MODEL = (4, 5, 6, 7)
"""Lesson types whose summary slide the model writes (the others are built from the page)."""


def dialogue_highlights(plan: SecondaryPlan) -> list[str]:
    """The phrases the page highlights in the lesson's conversations, once each."""
    found: list[str] = []
    for exercise in plan.exercises:
        if exercise.kind == "dialogue":
            for phrase in exercise.content.get("highlights", []):
                if phrase not in found:
                    found.append(phrase)
    return found


def needs_model_summary(plan: SecondaryPlan) -> bool:
    """Whether the model writes the summary slide (not when the page already gives its content)."""
    if plan.blueprint.number not in SUMMARY_FROM_MODEL:
        return False
    return not (plan.blueprint.number == 4 and dialogue_highlights(plan))


# --- the call ----------------------------------------------------------------------------------


def _outcomes_text(outcomes: Sequence[Outcome]) -> str:
    lines = [
        f"- {o.id}: {o.text}" + (f" [{', '.join(o.skills)}]" if o.skills else "") for o in outcomes
    ]
    return "\n".join(lines) or "(none)"


def _spans_text(plan: SecondaryPlan) -> str:
    return "\n".join(f"[{ref.span_id}] {' '.join(span.text.split())}" for ref, span in plan.spans)


def build_prompt(plan: SecondaryPlan, curriculum: Curriculum, reference: str = "") -> str:
    """The drafting prompt: the lesson's exercises and spans, the time plan, the reference."""
    minutes = plan.minutes()
    phases = ", ".join(f"{phase.value} {value}" for phase, value in minutes.items())
    exercises = "\n\n".join("\n".join(e.prompt_lines()) for e in plan.exercises) or "(none)"
    return load_prompt(
        "draft_secondary",
        period_minutes=str(curriculum.period_minutes),
        grade=str(curriculum.file.grade),
        unit=unit_label(plan.unit),
        lesson=f"{plan.lesson_no} {plan.lesson_name}" + (f" - {plan.topic}" if plan.topic else ""),
        phases=phases,
        outcomes=_outcomes_text(plan.outcomes),
        exercises=exercises,
        spans=_spans_text(plan) or "(none)",
        reference=reference.strip() or "(none)",
        key_words=", ".join(plan.key_words) or "(none)",
        production_part=_production_part(plan, minutes.get(Phase.PRODUCTION, 0)),
        blueprint=blueprint_prompt(plan.blueprint),
        vocabulary_part=_vocabulary_part(plan),
        summary_part=_summary_part(plan),
        extras_part=_extras_part(plan),
        warm_up_minutes=str(minutes.get(Phase.WARM_UP, PHASE_MINUTES[Phase.WARM_UP])),
        wrap_up_minutes=str(minutes.get(Phase.WRAP_UP, PHASE_MINUTES[Phase.WRAP_UP])),
    )


_VOCABULARY_PART = (
    '2. "vocabulary": the new or key words and phrases of this lesson\'s text that pupils need for '
    "the exercises, 5 to 8 in all. It must list ALL of these words, which the lesson's vocabulary "
    "exercises list (more than 8 is fine then, but add no others): {key_words}. When there are "
    "fewer than 5, add other new or key words of the text. Do not list the same word twice (not "
    'as a singular and a plural). For each: "word" exactly as it is written in the cited span, '
    'letter for letter (British spelling as the book has it, for example "colour", '
    '"speciality"; lower case unless a name), "ipa" (British, between slashes), "pos" (n, '
    'v, adj, phrasal verb ...), "meaning_vi" (a short Vietnamese gloss), "example" (a sentence '
    'copied exactly from the cited span, or ""), and "span": the id of the one span (for '
    'example "p1.s23") where the word occurs. Choose words that occur in the lesson\'s text; do '
    "not invent words."
)

_SUMMARY_PARTS = {
    4: (
        "3 to 6 useful phrases of the Everyday English exchanges (the ones to recap), each copied "
        "exactly as written in the lesson's text."
    ),
    5: (
        "2 or 3 key ideas of the reading text, each one short sentence (at most 14 words) that "
        "pupils can say, using words of the text."
    ),
    6: (
        "a checklist of 4 items for the paragraph pupils write (each at most 12 words, starting "
        'with a verb, for example "Start with ..."): the starter, the guiding questions, linking '
        "words, the word count and spelling."
    ),
    7: (
        "3 or 4 short statements of what pupils can now do in this unit (each at most 14 words, "
        'starting with "I can"), taken from the Now I can list.'
    ),
}


def _vocabulary_part(plan: SecondaryPlan) -> str:
    if not plan.blueprint.new_words:
        return '2. "vocabulary": not needed for this kind of lesson. Return [].'
    return _VOCABULARY_PART.format(key_words=", ".join(plan.key_words) or "(none)")


def _summary_part(plan: SecondaryPlan) -> str:
    if not needs_model_summary(plan):
        return "not needed: the summary slide is built from the page. Return []."
    return _SUMMARY_PARTS[plan.blueprint.number]


def _extras_part(plan: SecondaryPlan) -> str:
    """The prompt's items for the AI-added parts of this lesson type (empty for most)."""
    lines: list[str] = []
    extras = plan.blueprint.extras
    if "speaking_frame" in extras:
        lines.append(
            '7. "speaking_frame": a frame for the short presentation of the speaking task. '
            '"title" (at most 5 words), "instructions" (one short sentence for pupils: use the '
            'sentence starters and speak for about one minute), "starters" (4 to 6 sentence '
            "starters that follow the order of the speaking questions, each ending with ..., for "
            'example "My speciality is ..."), "teacher_steps" (2 to 4 steps: modelling, timing '
            "with a one-minute timer, feedback). It is labelled AI-added."
        )
    if "writing_frame" in extras:
        lines.append(
            '7. "writing_frame": a frame for the paragraph pupils write. "title" (at most 5 '
            'words), "instructions" (one short sentence for pupils), "starters" (2 or 3 sentence '
            'starters after the one the book gives, each ending with ...), "linking_words" (4 to '
            'six linking words or phrases such as "and", "but", "because", "so"), "word_count" '
            '(the number the book names, else 80) and "teacher_steps" (2 to 4 steps). It is '
            "labelled AI-added."
        )
    if "model_paragraph" in extras:
        lines.append(
            '8. "model_paragraph": {"title": "Model paragraph", "text": ...}: a model of the '
            "paragraph the book asks for, about 60 to 90 words, in simple present-tense English "
            "that uses the starter the book gives, answers the guiding questions and uses the "
            "linking words of the frame. It uses only words of the unit's pages or very common "
            "ones. It is labelled AI-added."
        )
    return "\n".join(lines)


def _production_part(plan: SecondaryPlan, minutes: int) -> str:
    """The prompt's item for a speaking task, when the page has no free task of its own."""
    if not plan.needs_production:
        return '3b. "production" is not needed: the page already has free tasks. Omit it or null.'
    return (
        f'3b. "production": a {minutes}-minute speaking task for pairs or groups that uses '
        "the words of this lesson and nothing else new, same fields as the warm-up. Pupils talk "
        "about "
        "their own lives or invent a short exchange, so it is free practice, not a drill; "
        "it is clearly different from the textbook's exercises. It is labelled AI-added."
    )


_REJECTION = (
    "Your reply cannot be used as it is:\n{problems}\n"
    "Reply again with the whole JSON object, fixing these problems and changing nothing else."
)


@dataclass
class SecondaryReport:
    """What the model call did, for the CLI and the tests."""

    model_called: bool = False
    reprompted: bool = False
    problems: Problems = field(default_factory=dict)
    """Problems left after the last attempt; those parts use the deterministic fallback."""
    fallback_exercises: list[str] = field(default_factory=list)
    fallback_warm_up: bool = False
    fallback_wrap_up: bool = False
    fallback_production: bool = False
    fallback_extras: list[str] = field(default_factory=list)
    """AI-added parts of the lesson type (a frame, the model paragraph) that were left out."""
    """The lesson had no free task and the model's speaking task was missing or unusable."""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    """What the paid calls cost (cached replies are free); ``None`` for a model without a price."""
    cached: bool = False


CALL_ATTEMPTS = 3
CALL_PAUSE_S = 5.0
_NO_RETRY_STATUS = frozenset({400, 401, 402, 403, 404, 422})


class _Metered:
    """Forwards to a provider and adds the usage of every response to a report."""

    def __init__(self, inner: LLMProvider, report: SecondaryReport) -> None:
        self._inner = inner
        self._report = report
        self.name = inner.name
        self.model = inner.model

    def _complete_with_pauses(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        """The call, tried again after a pause when the network or the endpoint failed (the
        provider's own retries last a few seconds; a connection that drops longer must not lose
        a lesson). A rejected request or key is not repeated."""
        for attempt in range(1, CALL_ATTEMPTS + 1):
            try:
                return self._inner.complete(messages, **kwargs)
            except LLMError as exc:
                if attempt == CALL_ATTEMPTS or exc.status in _NO_RETRY_STATUS:
                    raise
                logger.warning("the drafting call failed (%s); trying again", exc)
                time.sleep(CALL_PAUSE_S * attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def complete(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        response = self._complete_with_pauses(messages, **kwargs)
        report = self._report
        if response.cached:
            report.cached = True
            report.cost_usd = report.cost_usd or 0.0
            return response
        report.input_tokens += response.usage.input_tokens or 0
        report.output_tokens += response.usage.output_tokens or 0
        price = estimate_cost_usd(response.model, response.usage)
        if price is not None:
            report.cost_usd = (report.cost_usd or 0.0) + price
        return response


def _list_problems(problems: Problems) -> str:
    return "\n".join(f"- {key}: {'; '.join(found)}" for key, found in problems.items())


_KEY_NOISE = re.compile(r"[^A-Za-z0-9_]")


def _clean_keys(value: Any) -> Any:
    """The reply with every key reduced to its plain name: the Meta endpoint sometimes writes a
    stray colon or quote into a key (``ipa:``), which would otherwise drop the field."""
    if isinstance(value, dict):
        return {
            _KEY_NOISE.sub("", str(key)) or key: _clean_keys(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_clean_keys(item) for item in value]
    return value


def _complete_draft(provider: LLMProvider, messages: list[Message]) -> SecondaryDraft:
    """One plain request (no ``response_format``, no schema pasted into the prompt: the shape is
    described in the prompt, and constrained decoding garbled keys on the Meta endpoint) and the
    parsed reply; ``StructuredOutputError`` when it does not parse or validate."""
    response = provider.complete(messages, temperature=0.0)
    try:
        return SecondaryDraft.model_validate(_clean_keys(json.loads(extract_json(response.text))))
    except json.JSONDecodeError as exc:
        error = f"The reply is not valid JSON: {exc}"
    except ValidationError as exc:
        error = f"The JSON does not match the shape: {exc.errors(include_url=False)[:5]}"
    raise StructuredOutputError(f"No valid reply. Last error: {error}", [response.text])


def request_draft(
    plan: SecondaryPlan,
    curriculum: Curriculum,
    provider: LLMProvider,
    reference: str,
    report: SecondaryReport,
) -> tuple[SecondaryDraft | None, Problems]:
    """One structured call and, when the reply has problems, one re-prompt.

    Returns the last parsed reply (``None`` when no attempt parsed) and its problems; network and
    model failures other than unparseable output propagate as ``LLMError``.
    """
    messages = [Message(role="user", content=build_prompt(plan, curriculum, reference))]
    report.model_called = True
    provider = _Metered(provider, report)
    draft: SecondaryDraft | None = None
    problems: Problems = {}
    for attempt in range(2):
        try:
            draft = _complete_draft(provider, messages)
        except StructuredOutputError as exc:
            reply_text, problems = exc.attempts[-1], {"reply": [str(exc)]}
            draft = None
        else:
            for entry in draft.vocabulary:
                entry.word = written_word(entry.word, plan)
            reply_text, problems = draft.model_dump_json(), reply_problems(draft, plan)
            if not problems:
                return draft, {}
        if attempt == 0:
            report.reprompted = True
            messages = [
                *messages,
                Message(role="assistant", content=reply_text),
                Message(role="user", content=_REJECTION.format(problems=_list_problems(problems))),
            ]
    return draft, problems


# --- blocks ------------------------------------------------------------------------------------

DEFAULT_MINUTES: dict[str, int] = {
    "dialogue": 6,
    "reading": 6,
    "rule_box": 3,
    "gap_fill": 5,
    "match": 4,
    "choice": 4,
    "true_false": 4,
    "question_set": 5,
    "sound_sort": 5,
    "activity": 5,
    "gallery": 2,
    "grid": 5,
    "self_check": 3,
    "project": 3,
    "speaking_frame": 5,
    "writing_frame": 6,
    "model_paragraph": 3,
}

DEFAULT_STEPS: dict[str, list[str]] = {
    "dialogue": [
        "Set the scene with the picture, then play the recording while pupils follow.",
        "Play it again, then ask two or three pairs of pupils to read the roles aloud.",
    ],
    "reading": [
        "Pupils read the text silently, then read it aloud in turns.",
        "Ask a few questions to check understanding.",
    ],
    "rule_box": [
        "Read the box with the class and write one more example on the board.",
        "Ask pupils to give their own examples.",
    ],
    "gap_fill": [
        "Pupils read the sentences and fill in the blanks on their own.",
        "Pupils compare with a partner, then check the answers together.",
    ],
    "match": [
        "Pupils match the items on their own, then compare with a partner.",
        "Check the answers together and read each pair aloud.",
    ],
    "choice": [
        "Pupils choose the answers on their own.",
        "Check together and ask pupils to say why each answer is right.",
    ],
    "true_false": [
        "Pupils decide true or false for each sentence.",
        "Check together and ask pupils to correct the false sentences.",
    ],
    "question_set": [
        "Pupils work in pairs to answer the questions.",
        "Invite some pairs to share their answers with the class.",
    ],
    "sound_sort": [
        "Say each word aloud and let pupils repeat it, then play the recording.",
        "Pupils sort the words by their vowel sound; check together and repeat the sounds.",
    ],
    "activity": [
        "Explain the task and give pupils a minute to prepare.",
        "Pupils do the task, then share with the class.",
    ],
    "gallery": [
        "Show the pictures and ask pupils what they can see.",
        "Take answers from the class and lead into the task.",
    ],
    "grid": [
        "Pupils fill in the table on their own or in groups.",
        "Check together.",
    ],
    "self_check": [
        "Read each line with the class.",
        "Pupils tick one, two or three ticks for how well they can do it, then compare in pairs.",
    ],
    "project": [
        "Form groups and read the task and the steps together.",
        "Groups work through the steps; walk round and help.",
        "Each group reports to the class.",
    ],
}


FIXED_MINUTES = frozenset({"gallery", "self_check", "project"})
"""Kinds whose minutes are not taken from the model's reply."""


class _Ids:
    def __init__(self) -> None:
        self.used: set[str] = set()

    def next(self, prefix: str = "b") -> str:
        while (candidate := new_id(prefix)) in self.used:
            pass
        self.used.add(candidate)
        return candidate


def _citation(ref: SpanRef, span: Span | None) -> Citation:
    quote = " ".join(span.text.split())[:80] if span is not None else ""
    return Citation(doc_id=ref.doc_id, page=ref.page, span_id=ref.span_id, quote=quote or None)


def _citations(refs: Sequence[SpanRef], plan: SecondaryPlan) -> list[Citation]:
    seen: set[tuple[str, str]] = set()
    citations: list[Citation] = []
    for ref in refs:
        key = (ref.doc_id, ref.span_id)
        if key in seen:
            continue
        seen.add(key)
        span = (
            plan.docs[ref.doc_id].find_span(ref.page, ref.span_id)
            if ref.doc_id in plan.docs
            else None
        )
        citations.append(_citation(ref, span))
    return citations


def _teacher_note(minutes: int, steps: Sequence[str]) -> str:
    lines = [f"About {max(minutes, 1)} min."]
    lines += [f"{n}. {step.strip()}" for n, step in enumerate(steps, start=1) if step.strip()]
    return "\n".join(lines)


def _gap_answers(reply: ExerciseReply) -> list[str | list[str]]:
    return [answers[0] if len(answers) == 1 else list(answers) for answers in reply.gaps]


def _with_answers(exercise: Exercise, reply: ExerciseReply) -> dict[str, Any]:
    """The exercise content with the (validated) answers of the model merged in."""
    content = dict(exercise.content)
    match exercise.kind:
        case "gap_fill":
            content["items"] = [
                {"text": item["text"], "answer": answer}
                for item, answer in zip(content["items"], _gap_answers(reply), strict=True)
            ]
        case "match":
            content["answer"] = {i: _letter_index(letter) for i, letter in enumerate(reply.letters)}
        case "choice":
            content["items"] = [
                {**item, "answer": item["options"][_letter_index(letter) or 0]}
                for item, letter in zip(content["items"], reply.letters, strict=True)
            ]
        case "true_false":
            content["items"] = [
                {**item, "answer": truth}
                for item, truth in zip(content["items"], reply.truths, strict=True)
            ]
        case "sound_sort":
            sounds = content["sounds"]
            content["words"] = [
                {"word": word["word"], "sound": sound_key(sound, sounds)}
                for word, sound in zip(content["words"], reply.word_sounds, strict=True)
            ]
            content["sentences"] = [
                {
                    "text": sentence["text"],
                    "targets": [
                        {
                            "word": occurs_as_word(target.word, sentence["text"]),
                            "sound": sound_key(target.sound, sounds),
                        }
                        for target in targets
                    ],
                }
                for sentence, targets in zip(content["sentences"], reply.targets, strict=True)
            ]
        case "grid":
            content["answers"] = [a.strip() for a in reply.answers]
        case "question_set" if reply.answers:
            content["questions"] = [
                {**question, "answer": answer}
                for question, answer in zip(content["questions"], reply.answers, strict=True)
            ]
        case "reading" if not exercise.pair:
            glossary = {
                w.strip(): m.strip() for w, m in reply.glossary.items() if w.strip() and m.strip()
            }
            paragraphs = [
                {
                    **paragraph,
                    "highlight": [w for w in glossary if w.lower() in paragraph["text"].lower()],
                }
                for paragraph in content["paragraphs"]
            ]
            content["paragraphs"] = paragraphs
            content["glossary"] = {
                w: m for w, m in glossary.items() if any(w in p["highlight"] for p in paragraphs)
            }
    return content


def _question_list(exercise: Exercise) -> dict[str, Any]:
    """An exercise whose answers are missing, as a plain question list (no answer key)."""
    content = exercise.content
    prompt = ""
    match exercise.kind:
        case "gap_fill":
            questions = [item["text"] for item in content["items"]]
            if content.get("word_box"):
                prompt = "Word box: " + " / ".join(content["word_box"])
        case "match":
            questions = list(content["left"])
            prompt = "Choose from: " + "  ".join(
                f"{chr(97 + n)}. {text}" for n, text in enumerate(content["right"])
            )
        case "choice":
            questions = [
                f"{item['stem']}  "
                + "  ".join(f"{chr(65 + i)}. {option}" for i, option in enumerate(item["options"]))
                for item in content["items"]
            ]
        case "true_false":
            questions = [item["statement"] for item in content["items"]]
            prompt = "True or false?"
        case "grid":
            questions = [" | ".join(row) for row in content["rows"]]
            prompt = " | ".join(content["header"])
        case "sound_sort":
            questions = [word["word"] for word in content["words"]]
            questions += [sentence["text"] for sentence in content["sentences"]]
            prompt = "Sounds: " + "  ".join(f"/{sound}/" for sound in content["sounds"])
        case _:
            questions = []
    return {
        "instruction": exercise.instruction,
        "prompt": prompt,
        "questions": [{"question": question} for question in questions],
    }


def _exercise_block(
    exercise: Exercise,
    reply: ExerciseReply | None,
    usable: bool,
    plan: SecondaryPlan,
    ids: _Ids,
    minutes: int,
) -> tuple[Block, bool]:
    """The block for one exercise, and whether it fell back to a plain question list.

    ``usable``: the reply has an entry for the exercise and it passed validation.
    """
    steps = [s.strip() for s in (reply.steps if reply else []) if s.strip()]
    steps = (steps or list(DEFAULT_STEPS[exercise.kind]))[:4]
    if exercise.track:
        steps.insert(0, f"Play track {exercise.track}.")
    if exercise.listening and exercise.needs_answers:
        steps.append("The answers are suggested from the topic: check them against the recording.")
    kind, fell_back = exercise.kind, False
    content = dict(exercise.content)
    if usable and reply is not None:
        content = _with_answers(exercise, reply)
    elif exercise.needs_answers:
        kind, content, fell_back = "question_set", _question_list(exercise), True
    if kind == "dialogue":
        content = {
            "context": exercise.instruction or None,
            "lines": content["lines"],
            "highlights": content.get("highlights", []),
        }
    elif kind in ("activity", "project"):
        content = {**content, "minutes": max(minutes, 1), "teacher_steps": steps[:5]}
        kind = "activity"
    elif exercise.number and kind != "rule_box":
        content["exercise_no"] = exercise.number
    block = BlockAdapter.validate_python(
        {
            "id": ids.next(),
            "kind": kind,
            "content": content,
            "sources": _citations(exercise.refs, plan),
            "provenance": Provenance.EXTRACTED,
            "teacher_note": None if kind == "activity" else _teacher_note(minutes, steps[:5]),
        }
    )
    return block, fell_back


def _vocabulary_block(
    draft: SecondaryDraft | None, problems: Problems, plan: SecondaryPlan, ids: _Ids
) -> VocabularyBlock | None:
    if draft is None:
        return None
    entries: list[tuple[VocabItem, SpanRef]] = []
    for entry in draft.vocabulary:
        if problems.get(f"vocabulary:{entry.word.strip().lower()}"):
            continue
        found = vocabulary_span(entry, plan)
        if found is None:
            continue
        ref, span = found
        example = " ".join(entry.example.split())
        if example and _normal(example) not in _normal(span.text):
            example = ""
        entries.append(
            (
                VocabItem(
                    word=entry.word.strip(),
                    ipa=entry.ipa.strip() or None,
                    pos=entry.pos.strip() or None,
                    meaning_vi=entry.meaning_vi.strip() or None,
                    example=example or None,
                ),
                ref,
            )
        )
    listed = {_word_key(item.word) for item, _ref in entries}
    for word in plan.key_words:  # a word of the vocabulary exercises the reply left out
        found = vocabulary_span(VocabReply(word=word), plan)
        if _word_key(word) not in listed and found is not None:
            entries.append((VocabItem(word=word), found[0]))
    entries = _capped(entries, {_word_key(word) for word in plan.key_words})
    if not entries:
        return None
    return VocabularyBlock(
        id=ids.next(),
        kind="vocabulary",
        content=VocabularyContent(items=[item for item, _ref in entries]),
        sources=_citations([ref for _item, ref in entries], plan),
        provenance=Provenance.DERIVED,
    )


def _capped(
    entries: list[tuple[VocabItem, SpanRef]], keep: set[str]
) -> list[tuple[VocabItem, SpanRef]]:
    """At most :data:`MAX_VOCABULARY` entries, but never fewer than the words of the lesson's
    vocabulary exercises (they come first; other words only fill what is left)."""
    room = max(MAX_VOCABULARY - sum(_word_key(item.word) in keep for item, _r in entries), 0)
    kept: list[tuple[VocabItem, SpanRef]] = []
    for item, ref in entries:
        if _word_key(item.word) in keep:
            kept.append((item, ref))
        elif room > 0:
            kept.append((item, ref))
            room -= 1
    return kept


# --- templates (the fallback) ------------------------------------------------------------------


def template_warm_up(plan: SecondaryPlan) -> PhaseReply:
    topic = plan.unit.title.lower()
    return PhaseReply(
        title=f"Talk about {topic}",
        instructions=f"Tell your partner what you know about {topic}.",
        teacher_steps=[
            f"Write the unit title on the board: {plan.unit.title}.",
            "Ask two or three simple questions about the topic; pupils answer in short phrases.",
            "Put pupils in pairs for one minute to share one idea each.",
            "Collect two or three ideas on the board and lead into the first exercise.",
        ],
        student_steps=[
            "Look at the topic.",
            "Tell your partner one thing you know about it.",
            "Share your idea with the class.",
        ],
        interaction="pairs",
        skills=["speaking", "listening"],
    )


def template_wrap_up(plan: SecondaryPlan) -> PhaseReply:
    return PhaseReply(
        title="Say it together",
        instructions="Say the new words and tell your partner what you learnt.",
        teacher_steps=[
            "Show the summary slide and say each word; pupils repeat after you.",
            "Ask pupils to tell a partner one thing they learnt today.",
            "Praise clear speaking and set the homework.",
        ],
        student_steps=[
            "Repeat the words after the teacher.",
            "Tell your partner one thing you learnt today.",
        ],
        interaction="whole_class",
        skills=["speaking", "listening"],
    )


def template_production(plan: SecondaryPlan) -> PhaseReply:
    topic = plan.unit.title.lower()
    return PhaseReply(
        title="Talk about it",
        instructions=f"Use the new words to talk about {topic} with your partner.",
        teacher_steps=[
            "Show the summary words on the board and let pupils read them once.",
            "Put pupils in pairs; each pupil says three sentences with the new words.",
            "Walk round and help; note good sentences.",
            "Ask two or three pairs to share their best sentence with the class.",
        ],
        student_steps=[
            "Choose three new words.",
            "Say a sentence with each word to your partner.",
            "Listen to your partner and say if the sentences are true.",
        ],
        interaction="pairs",
        skills=["speaking", "listening"],
    )


TEMPLATE_HOMEWORK = "Read the lesson again at home and learn the new words."


def _activity_block(
    reply: PhaseReply,
    minutes: int,
    ids: _Ids,
    *,
    provenance: Provenance,
    depends_on: Sequence[str] = (),
) -> ActivityBlock:
    return ActivityBlock(
        id=ids.next(),
        kind="activity",
        content=ActivityContent(
            type=ActivityType.OTHER,
            title=_clean_title(reply.title) or "Activity",
            skills=list(reply.skills) or ["speaking", "listening"],
            interaction=Interaction(reply.interaction),
            minutes=max(minutes, 1),
            instructions=reply.instructions.strip(),
            teacher_steps=[s.strip() for s in reply.teacher_steps if s.strip()],
            student_steps=[s.strip() for s in reply.student_steps if s.strip()],
            materials=[m.strip() for m in reply.materials if m.strip()],
            competency_tags=["communication"],
        ),
        provenance=provenance,
        depends_on=list(depends_on),
    )


# --- assembling the lesson ---------------------------------------------------------------------


def _timed_items(
    plan: SecondaryPlan,
    phase: Phase,
    replies: Mapping[str, ExerciseReply],
    vocabulary: bool,
    extras: Mapping[str, Any] | None = None,
) -> list[tuple[str, int]]:
    """``(key, weight)`` for every timed item of a phase: exercises and the new-words cards."""
    weights: list[tuple[str, int]] = []
    for exercise in plan.phase_exercises(phase):
        reply = replies.get(exercise.id)
        minutes = reply.minutes if reply and reply.minutes > 0 else DEFAULT_MINUTES[exercise.kind]
        if exercise.kind in FIXED_MINUTES:  # the model times the steps, not the opening slides
            minutes = DEFAULT_MINUTES[exercise.kind]
        weights.append((exercise.id, minutes))
    if phase is Phase.PRESENTATION and vocabulary:
        weights.append(("vocabulary", VOCABULARY_WEIGHT))
    if phase is Phase.PRODUCTION and plan.needs_production:
        weights.append(("production", DEFAULT_MINUTES["activity"]))
    if phase is Phase.PRODUCTION:
        weights += [(name, DEFAULT_MINUTES[name]) for name in (extras or {})]
    return weights


def _picture_words(blocks: Mapping[str, Block], vocabulary: VocabularyBlock | None) -> None:
    """Give a new word the textbook picture that a word-to-picture exercise matched it with."""
    if vocabulary is None:
        return
    by_key = {_word_key(item.word): item for item in vocabulary.content.items}
    for block in blocks.values():
        if block.kind != "match" or not block.content.left_images:
            continue
        content = block.content
        for left, right in content.answer.items():
            item = by_key.get(_word_key(content.right[right]))
            picture = content.left_images[left]
            if item is not None and picture is not None and item.image is None:
                item.image = picture


def _reading_picture(exercise: Exercise) -> None:
    """A reading shows its textbook picture beside the text: move it into the content."""
    for entries in (exercise.before, exercise.after):
        if entries:
            ref, span = entries.pop(0)
            exercise.content["image"] = _citation(ref, span).model_dump()
            return


def _highlight_from_match(
    reading: Exercise, match: Exercise | None, reply: ExerciseReply | None, usable: bool
) -> None:
    """A reading whose highlighted words are the left column of a matching exercise: highlight them
    in the paragraphs and use the book's own definitions (the match's key) as the glossary."""
    if match is None:
        return
    left, right = match.content["left"], match.content["right"]
    glossary: dict[str, str] = {}
    if usable and reply is not None:
        for n, letter in enumerate(reply.letters):
            index = _letter_index(letter)
            if index is not None and n < len(left) and 0 <= index < len(right):
                glossary[left[n]] = right[index]
    paragraphs = []
    for paragraph in reading.content["paragraphs"]:
        lowered = paragraph["text"].lower()
        paragraphs.append({**paragraph, "highlight": [w for w in left if w.lower() in lowered]})
    reading.content["paragraphs"] = paragraphs
    shown = {word for paragraph in paragraphs for word in paragraph["highlight"]}
    reading.content["glossary"] = {w: m for w, m in glossary.items() if w in shown}


def _frame_block(
    kind: str, frame: FrameReply, minutes: int, ids: _Ids, plan: SecondaryPlan
) -> ActivityBlock:
    """The AI-added frame of a speaking or writing task as an activity (starters, timer)."""
    steps = [s.strip() for s in frame.starters if s.strip()][:5]
    writing = kind == "writing_frame"
    if writing:
        words = [w.strip() for w in frame.linking_words if w.strip()]
        if words:
            steps.append("Use linking words: " + ", ".join(words) + ".")
        if frame.word_count > 0:
            steps.append(f"Write about {frame.word_count} words.")
    return ActivityBlock(
        id=ids.next(),
        kind="activity",
        content=ActivityContent(
            type=ActivityType.LETS_WRITE if writing else ActivityType.ROLE_PLAY,
            title=_clean_title(frame.title)
            or ("Paragraph frame" if writing else "Presentation frame"),
            skills=["writing"] if writing else ["speaking"],
            interaction=Interaction.INDIVIDUAL if writing else Interaction.GROUPS,
            minutes=max(minutes, 1),
            instructions=frame.instructions.strip(),
            teacher_steps=[s.strip() for s in frame.teacher_steps if s.strip()][:5],
            student_steps=steps,
            materials=[] if writing else ["timer"],
            competency_tags=["communication"],
        ),
        provenance=Provenance.GENERATED,
    )


def _model_paragraph_block(
    model: ParagraphReply, frame: FrameReply | None, ids: _Ids
) -> ReadingBlock:
    """The short model paragraph (AI-added); its linking words are highlighted."""
    paragraphs, glossary = [], {}
    for text in (t.strip() for t in re.split(r"\n\s*\n|\n", model.text) if t.strip()):
        found: list[str] = []
        for word in frame.linking_words if frame else []:
            hit = occurs_as_word(word, text)
            if hit and hit not in found:
                found.append(hit)
                glossary[hit] = "linking word"
        paragraphs.append(ReadingParagraph(text=text, highlight=found))
    return ReadingBlock(
        id=ids.next(),
        kind="reading",
        content=ReadingContent(
            instruction="",
            title=_clean_title(model.title) or "Model paragraph",
            paragraphs=paragraphs,
            glossary=glossary,
        ),
        provenance=Provenance.GENERATED,
    )


CHECKLIST_TEMPLATE = [
    "Start with the sentence from the book.",
    "Answer all the guiding questions.",
    "Use linking words.",
    "Check your spelling and punctuation.",
]


def _summary_bullets(
    plan: SecondaryPlan, draft: SecondaryDraft | None, problems: Problems, words: list[str]
) -> list[str]:
    """The summary slide of the lesson type: words, sounds, rules with an example, useful
    phrases (from the page), or the model's key ideas / checklist / recap."""
    number = plan.blueprint.number
    fallback = plan.topic or plan.unit.title
    if number == 1:
        return [", ".join(words)] if words else [fallback]
    if number == 2:
        sounds = next((e.content["sounds"] for e in plan.exercises if e.kind == "sound_sort"), [])
        bullets = [", ".join(words)] if words else []
        if sounds:
            bullets.append("Sounds: " + "  ".join(f"/{sound}/" for sound in sounds))
        return bullets or [fallback]
    if number == 3:
        bullets = []
        for exercise in plan.exercises:
            if exercise.kind != "rule_box":
                continue
            topic = re.sub(r"^remember\W*", "", exercise.content["title"], flags=re.I).strip()
            example = exercise.content["examples"][0] if exercise.content["examples"] else ""
            bullets.append(f"{topic}: {example}" if topic and example else example or topic)
        return [b for b in bullets if b] or [fallback]
    if number == 4 and (phrases := dialogue_highlights(plan)):
        return phrases[:6]
    if draft is not None and needs_model_summary(plan) and not problems.get("summary"):
        return [b.strip() for b in draft.summary if b.strip()]
    if number == 6:
        return list(CHECKLIST_TEMPLATE)
    if number == 7:
        items = next((e.content["items"] for e in plan.exercises if e.kind == "self_check"), [])
        return [f"I can {item}" for item in items[:4]] or [fallback]
    return [fallback]


def _warm_up_pictures(plan: SecondaryPlan, ids: _Ids) -> GalleryBlock | None:
    """The page's pictures that no exercise uses, as the picture prompt of the warm-up."""
    spare: list[Entry] = []
    seen: set[str] = set()
    for exercise in plan.exercises:
        for ref, span in (*exercise.before, *exercise.after):
            if ref.span_id not in seen and span.text.strip():
                seen.add(ref.span_id)
                spare.append((ref, span))
    if not spare:
        return None
    spare = spare[:MAX_WARM_UP_PICTURES]
    return GalleryBlock(
        id=ids.next(),
        kind="gallery",
        content=GalleryContent(
            instruction="",
            title="",
            images=[GalleryImage(**item) for item in _gallery_images([], spare)],
            questions=[],
        ),
        sources=_citations([ref for ref, _s in spare], plan),
        provenance=Provenance.EXTRACTED,
    )


MAX_WARM_UP_PICTURES = 3


def assemble(
    plan: SecondaryPlan,
    curriculum: Curriculum,
    draft: SecondaryDraft | None,
    problems: Problems,
    report: SecondaryReport,
) -> Lesson:
    """The lesson from the plan and the (validated) reply; failed parts use the fallback."""
    ids = _Ids()
    blueprint = plan.blueprint
    replies = {r.exercise.strip(): r for r in (draft.exercises if draft else [])}
    for exercise in plan.exercises:
        if exercise.kind == "reading":
            _reading_picture(exercise)
    vocabulary = _vocabulary_block(draft, problems, plan, ids) if blueprint.new_words else None
    by_id = {exercise.id: exercise for exercise in plan.exercises}
    for exercise in plan.exercises:
        if exercise.kind == "reading" and exercise.pair:
            _highlight_from_match(
                exercise,
                by_id.get(exercise.pair),
                replies.get(exercise.pair),
                exercise.pair in replies and not problems.get(exercise.pair),
            )

    extras = {
        name: getattr(draft, name)
        for name in blueprint.extras
        if draft is not None and not problems.get(name) and getattr(draft, name) is not None
    }
    report.fallback_extras = [name for name in blueprint.extras if name not in extras]
    minutes = plan.minutes(vocabulary=vocabulary is not None)

    warm = draft.warm_up if draft and not problems.get("warm_up") else None
    wrap = draft.wrap_up if draft and not problems.get("wrap_up") else None
    report.fallback_warm_up, report.fallback_wrap_up = warm is None, wrap is None
    warm = warm or template_warm_up(plan)
    wrap = wrap or template_wrap_up(plan)
    production = draft.production if draft and not problems.get("production") else None
    if plan.needs_production:
        report.fallback_production = production is None
        production = production or template_production(plan)
    homework = (draft.homework.strip() if draft else "") or TEMPLATE_HOMEWORK

    blocks: dict[str, Block] = {}
    sections: list[Section] = []

    def add_section(phase: Phase, section_blocks: list[Block], title: str | None = None) -> None:
        for block in section_blocks:
            blocks[block.id] = block
        sections.append(
            Section(
                id=f"s_{phase.value}",
                phase=phase,
                title=title or PHASE_TITLES[phase],
                minutes=minutes.get(phase, 0),
                block_ids=[b.id for b in section_blocks],
            )
        )

    objectives = [
        ObjectiveBlock(
            id=ids.next(),
            kind="objective",
            content=ObjectiveContent(text=o.text, outcome_ids=[o.id], skills=list(o.skills)),
            provenance=Provenance.DERIVED,
        )
        for o in plan.outcomes
    ]
    warm_block = _activity_block(warm, minutes[Phase.WARM_UP], ids, provenance=Provenance.GENERATED)
    pictures = _warm_up_pictures(plan, ids) if blueprint.warm_pictures else None
    add_section(
        Phase.WARM_UP,
        [*objectives, *([pictures] if pictures else []), warm_block],
        title=warm.title.strip() or None,
    )

    for phase in (Phase.PRESENTATION, Phase.PRACTICE, Phase.PRODUCTION):
        if phase not in minutes:
            continue
        timed = _timed_items(plan, phase, replies, vocabulary is not None, extras)
        shares = scale_minutes([weight for _key, weight in timed], minutes[phase])
        share_of = {key: share for (key, _weight), share in zip(timed, shares, strict=True)}
        section_blocks: list[Block] = []
        placed = False
        for exercise in plan.phase_exercises(phase):
            reply = replies.get(exercise.id)
            usable = reply is not None and not problems.get(exercise.id)
            block, fell_back = _exercise_block(
                exercise, reply, usable, plan, ids, share_of.get(exercise.id, 3)
            )
            if fell_back:
                report.fallback_exercises.append(exercise.id)
            section_blocks.append(block)
            if (
                vocabulary
                and not placed
                and phase is Phase.PRESENTATION
                and exercise.kind in ("dialogue", "reading")
            ):
                section_blocks.append(vocabulary)
                placed = True
        if vocabulary and phase is Phase.PRESENTATION:
            if not placed:
                section_blocks.insert(0, vocabulary)
            vocabulary.teacher_note = _teacher_note(
                share_of.get("vocabulary", VOCABULARY_WEIGHT),
                [
                    "Show each word card, say the word and let pupils repeat it.",
                    "Tap the card for the Vietnamese meaning and ask pupils to find the word "
                    "in the text.",
                ],
            )
        if phase is Phase.PRODUCTION:
            if plan.needs_production and production is not None:
                section_blocks.append(
                    _activity_block(
                        production,
                        share_of.get("production", 3),
                        ids,
                        provenance=Provenance.GENERATED,
                    )
                )
            for name in blueprint.extras:
                extra = extras.get(name)
                if name == "model_paragraph" and extra is not None:
                    section_blocks.append(
                        _model_paragraph_block(extra, extras.get("writing_frame"), ids)
                    )
                elif extra is not None:
                    section_blocks.append(
                        _frame_block(name, extra, share_of.get(name, 3), ids, plan)
                    )
        add_section(phase, section_blocks)

    words = [item.word for item in vocabulary.content.items] if vocabulary else []
    _picture_words(blocks, vocabulary)
    summary = SummaryBlock(
        id=ids.next(),
        kind="summary",
        content=SummaryContent(bullets=_summary_bullets(plan, draft, problems, words)),
        provenance=Provenance.DERIVED,
        depends_on=[vocabulary.id] if vocabulary else [],
    )
    recap = _activity_block(
        wrap, minutes[Phase.WRAP_UP], ids, provenance=Provenance.DERIVED, depends_on=[summary.id]
    )
    note = TeacherNoteBlock(
        id=ids.next(),
        kind="teacher_note",
        content=TeacherNoteContent(text=f"Homework: {homework}"),
        provenance=Provenance.GENERATED,
    )
    add_section(Phase.WRAP_UP, [summary, recap, note], title="Wrap-up and homework")
    return Lesson(
        id=ids.next("l"),
        title=plan.title,
        grade=curriculum.file.grade,
        curriculum=CurriculumRef(
            version=curriculum.file.version,
            unit_id=plan.unit.id,
            unit_no=plan.unit.textbook_units[0].unit if plan.unit.textbook_units else None,
            unit_title=plan.unit.title,
            lesson_no=plan.lesson_no,
            period=None,
            outcome_ids=[o.id for o in plan.outcomes],
        ),
        sources=[
            SourceRef(doc_id=doc.doc_id, filename=doc.filename, sha256=doc.sha256)
            for doc in plan.docs.values()
        ],
        sections=sections,
        blocks=blocks,
    )


def draft_secondary_lesson(
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    *,
    unit_id: str,
    lesson_no: int,
    provider: LLMProvider | None = None,
    reference: str = "",
) -> tuple[Lesson, SecondaryReport]:
    """One lesson of a secondary unit: the plan by code, answers and teacher layer by one call.

    Without ``provider`` the lesson is complete but has no answer keys, the warm-up and wrap-up
    come from templates. With one, the reply is validated and re-prompted once; whatever is
    still wrong falls back part by part (``SecondaryReport`` says which). ``reference`` is the
    cleaned text of a teacher's plan (:mod:`classdeck.planning.reference`).
    """
    plan = plan_lesson(sources, curriculum, unit_id=unit_id, lesson_no=lesson_no)
    report = SecondaryReport()
    draft: SecondaryDraft | None = None
    problems: Problems = {}
    if provider is not None:
        draft, problems = request_draft(plan, curriculum, provider, reference, report)
        if draft is None:
            report.error = _list_problems(problems)
        if problems:
            logger.warning("the drafting reply still has problems: %s", _list_problems(problems))
    report.problems = problems
    lesson = assemble(plan, curriculum, draft, problems, report)
    return lesson, report
