"""Curriculum units built on the fly from the ingested pages.

Teachers pick a grade and a unit *number*; they do not edit curriculum files. ``curriculum_for``
returns the curriculum the pipeline needs for that choice: the unit ``ta{grade}.u{n}`` comes from
the grade's YAML file when it lists it, else it is built from the pages that were ingested
(``docs/local-app.md``, "Grades without curriculum data").

* Grades 1-5 (primary shape): the title is the first heading of the unit, the key words are the
  content words of the lesson pages, the patterns are the lines with a blank (``Hello, I'm ___.``)
  or else the short questions, the outcomes are written from those.
* Grades 6-9 (secondary shape): the title is the first heading that is not a section banner, the
  outcomes come from the "This unit includes" box when it was read, else one generic outcome
  covers all seven lessons.

A unit built this way has no closed word list: the check that new words come from the unit's own
pages (``english.words_from_unit``) replaces the grade word list.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from classdeck.curriculum.loader import Curriculum, CurriculumNotFound, load_curriculum
from classdeck.curriculum.models import (
    CurriculumFile,
    Outcome,
    Pattern,
    TextbookUnitRef,
    Unit,
    VocabularyScope,
)
from classdeck.lesson.schema import SourceDocument, Span, SpanKind

DEFAULT_VERSION = "gdpt-2018"
GRADES = range(1, 10)
MAX_UNIT_NUMBER = 99
SECONDARY_FROM = 6

_UNIT_ID = re.compile(r"^(?:ta(?P<grade>\d{1,2})\.)?u?(?P<number>\d{1,2})$", re.IGNORECASE)
_UNIT_PREFIX = re.compile(r"^\s*unit\s*\d+\s*[:.\-\u2013\u2014]*\s*", re.IGNORECASE)
_NUMBER_PREFIX = re.compile(r"^\s*\d+\s*[:.\-\u2013\u2014]*\s+")
_INSTRUCTION = re.compile(r"^\s*\d{1,2}[.)]\s")
_SPEAKER = re.compile(r"^\s*[A-Z][a-z]{1,12}:\s+")
_BLANK = re.compile(r"_{2,}|\u2026{2,}|\.{4,}")
_WORD = re.compile(r"[A-Za-z]+(?:['\u2019][a-z]+)?")
_LESSON_HEADING = re.compile(r"^\s*lesson\s*\d+\s*$", re.IGNORECASE)

MAX_KEY_WORDS = 24
MAX_PATTERNS = 4


def unit_id_for(grade: int, number: int) -> str:
    return f"ta{grade}.u{number}"


def parse_unit_number(value: str | int, grade: int | None = None) -> int:
    """The unit number of ``3``, ``"3"``, ``"u3"`` or ``"ta9.u3"``.

    A grade prefix must agree with ``grade`` when one is given. Raises ``ValueError``.
    """
    if isinstance(value, int):
        number = value
    else:
        match = _UNIT_ID.match(str(value).strip())
        if match is None:
            raise ValueError(
                f"unit must be a number such as 3 (or an id such as ta3.u3): {value!r}"
            )
        if grade is not None and match["grade"] and int(match["grade"]) != grade:
            raise ValueError(f"unit {value!r} belongs to grade {match['grade']}, not grade {grade}")
        number = int(match["number"])
    if not 1 <= number <= MAX_UNIT_NUMBER:
        raise ValueError(f"unit number must be between 1 and {MAX_UNIT_NUMBER}, got {number}")
    return number


# --- reading the pages -------------------------------------------------------------------------


def _documents(
    sources: Mapping[str, SourceDocument] | Iterable[SourceDocument],
) -> list[SourceDocument]:
    if isinstance(sources, Mapping):
        return list(sources.values())
    return list(sources)


def _spans(documents: Sequence[SourceDocument]) -> list[Span]:
    return [
        span
        for document in documents
        for page in document.pages
        for span in page.spans
        if span.kind is not SpanKind.IMAGE and span.text.strip()
    ]


def _clean(text: str) -> str:
    return " ".join(text.split())


def _is_banner(text: str) -> bool:
    from classdeck.planning.secondary import section_lesson

    return section_lesson(text) is not None


def unit_title(spans: Sequence[Span], *, secondary: bool) -> str:
    """The unit's name: the first heading with the ``Unit 3`` prefix taken off; ``""`` if none.

    Section banners, ``Lesson 2`` headings and an all-capitals title (``LOCAL COMMUNITY``) are
    handled: banners and lesson headings are skipped, capitals become sentence case.
    """
    for span in spans:
        if span.kind is not SpanKind.HEADING:
            continue
        text = _clean(span.text)
        if not text or _LESSON_HEADING.match(text) or (secondary and _is_banner(text)):
            continue
        title = _NUMBER_PREFIX.sub("", _UNIT_PREFIX.sub("", text)).strip(" :.-")
        if not title or re.fullmatch(r"unit\s*\d*", title, re.IGNORECASE):
            continue
        letters = [c for c in title if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            title = title.capitalize()
        return title
    return ""


# --- primary -----------------------------------------------------------------------------------

_INSTRUCTION_WORDS = frozenset(
    {
        "listen",
        "point",
        "say",
        "look",
        "repeat",
        "complete",
        "read",
        "tick",
        "write",
        "talk",
        "sing",
        "match",
        "circle",
        "draw",
        "colour",
        "color",
        "ask",
        "answer",
        "partner",
        "let",
        "lets",
        "then",
        "unit",
        "lesson",
        "period",
    }
)


def _content_spans(spans: Sequence[Span]) -> list[str]:
    """Texts of the pupils' material: no headings, no numbered instruction lines, no name lists."""
    texts: list[str] = []
    for span in spans:
        if span.kind in (SpanKind.HEADING, SpanKind.CAPTION):
            continue
        if _INSTRUCTION.match(span.text) or span.kind is SpanKind.INSTRUCTION:
            continue
        text = _SPEAKER.sub("", _clean(span.text))
        tokens = text.split()
        if len(tokens) >= 3 and all(t[:1].isupper() and t.isalpha() for t in tokens):
            continue  # "Lan Duc Khoa Mi": names under a picture
        if text:
            texts.append(text)
    return texts


def key_words(spans: Sequence[Span]) -> list[str]:
    """Content words of the unit's pages, most frequent first (first appearance breaks ties)."""
    from classdeck.english.validators import FUNCTION_WORDS

    counts: Counter[str] = Counter()
    first: dict[str, int] = {}
    capitalised_mid: set[str] = set()
    lower_seen: set[str] = set()
    for text in _content_spans(spans):
        previous_ends_sentence = True
        for token in re.finditer(r"[A-Za-z]+(?:['\u2019][a-z]+)?|[.!?]", text):
            piece = token.group(0)
            if piece in ".!?":
                previous_ends_sentence = True
                continue
            word = piece.lower().replace("\u2019", "'")
            if piece[:1].isupper() and not previous_ends_sentence and piece != "I":
                capitalised_mid.add(word)
            elif piece[:1].islower():
                lower_seen.add(word)
            previous_ends_sentence = False
            counts[word] += 1
            first.setdefault(word, len(first))
    words = [
        word
        for word in counts
        if len(word) > 2
        and word not in FUNCTION_WORDS
        and word not in _INSTRUCTION_WORDS
        and "'" not in word
        and not (word in capitalised_mid and word not in lower_seen)
    ]
    words.sort(key=lambda w: (-counts[w], first[w]))
    chosen = words[:MAX_KEY_WORDS]
    chosen.sort(key=lambda w: first[w])
    return chosen


def patterns_of(spans: Sequence[Span]) -> list[Pattern]:
    """Lines with a blank (``Hello, I'm ___.``); else the short questions of the pages."""
    seen: set[str] = set()
    found: list[str] = []
    texts = _content_spans(spans)
    for raw in texts:
        text = re.sub(r"_{2,}", "___", raw)
        if _BLANK.search(text) and len(text.split()) <= 9 and text not in seen:
            seen.add(text)
            found.append(text)
    if not found:
        for text in texts:
            for sentence in re.findall(r"[^.!?]*\?", text):
                sentence = sentence.strip()
                if 2 <= len(sentence.split()) <= 8 and sentence not in seen:
                    seen.add(sentence)
                    found.append(sentence)
    return [Pattern(pattern=text) for text in found[:MAX_PATTERNS]]


def _words_of(text: str) -> list[str]:
    from classdeck.english.validators import FUNCTION_WORDS

    return [
        w
        for w in (m.group(0).lower().replace("\u2019", "'") for m in _WORD.finditer(text))
        if w not in FUNCTION_WORDS and len(w) > 1
    ]


def primary_unit(grade: int, number: int, spans: Sequence[Span]) -> Unit:
    uid = unit_id_for(grade, number)
    title = unit_title(spans, secondary=False) or f"Unit {number}"
    words = key_words(spans)
    patterns = patterns_of(spans)
    instructions = " ".join(s.text.lower() for s in spans if _INSTRUCTION.match(s.text))
    outcomes = [
        Outcome(
            id=f"{uid}.o1",
            text="Recognise, say and use the key words of the unit"
            + (f": {', '.join(words[:6])}." if words else "."),
            skills=["listening", "speaking"],
            keywords=words[:12],
        )
    ]
    for pattern in patterns[:2]:
        outcomes.append(
            Outcome(
                id=f"{uid}.o{len(outcomes) + 1}",
                text=f"Use “{pattern.pattern}” in a short exchange.",
                skills=["speaking", "listening"],
                keywords=_words_of(pattern.pattern),
            )
        )
    if re.search(r"\b(read|write|complete)\b", instructions):
        outcomes.append(
            Outcome(
                id=f"{uid}.o{len(outcomes) + 1}",
                text="Read and write short words and sentences from the unit.",
                skills=["reading", "writing"],
                keywords=["read", "write", "complete"],
            )
        )
    return Unit(
        id=uid,
        title=title,
        textbook_units=[TextbookUnitRef(series="global-success", unit=number)],
        lessons=3,
        outcomes=outcomes,
        vocabulary=words,
        patterns=patterns,
    )


# --- secondary ---------------------------------------------------------------------------------

_BOX_CATEGORIES: list[tuple[str, tuple[str, ...], list[str], list[int]]] = [
    ("vocabulary", ("vocabulary",), ["listening", "reading"], [1, 2, 7]),
    ("pronunciation", ("pronunciation",), ["listening", "speaking"], [2, 7]),
    ("grammar", ("grammar",), ["speaking", "writing"], [3, 7]),
    ("everyday english", ("everyday english", "communication"), ["speaking", "listening"], [4]),
    ("reading", ("reading",), ["reading"], [5]),
    ("speaking", ("speaking",), ["speaking"], [5]),
    ("listening", ("listening",), ["listening"], [6]),
    ("writing", ("writing",), ["writing"], [6]),
    ("skills", ("skills",), [], [5, 6]),
]


def _includes_box(spans: Sequence[Span]) -> list[str]:
    """The lines of the "This unit includes" box, up to the next heading."""
    lines: list[str] = []
    inside = False
    for span in spans:
        text = _clean(span.text)
        if not inside:
            inside = "this unit includes" in text.lower()
            if inside and ":" in text:
                tail = text.split(":", 1)[1].strip()
                if tail:
                    lines.append(tail)
            continue
        if span.kind is SpanKind.HEADING or _INSTRUCTION.match(text):
            break
        lines.extend(part.strip() for part in span.text.splitlines() if part.strip())
        if len(lines) > 40:
            break
    return lines


def secondary_unit(grade: int, number: int, spans: Sequence[Span]) -> Unit:
    uid = unit_id_for(grade, number)
    title = unit_title(spans, secondary=True) or f"Unit {number}"
    outcomes: list[Outcome] = []
    topics: list[str] = []
    for line in _includes_box(spans):
        label, sep, rest = line.partition(" | ") if " | " in line else line.partition(":")
        key = " ".join(label.lower().split())
        rest = rest.strip(" .;")
        category = next((c for c in _BOX_CATEGORIES if key in c[1]), None)
        if category is None or not sep or not rest:
            topics.append(line.strip(" .;:"))
            continue
        _name, _labels, skills, lessons = category
        outcomes.append(
            Outcome(
                id=f"{uid}.o{len(outcomes) + 1}",
                text=f"{label.strip().capitalize()}: {rest}.",
                skills=skills,  # type: ignore[arg-type]
                keywords=_words_of(rest)[:8],
                lessons=lessons,
            )
        )
    if topics and not outcomes:
        outcomes.append(
            Outcome(
                id=f"{uid}.o1",
                text=f"Talk about {', '.join(topics[:6])} with the words of the unit.",
                skills=["listening", "reading"],
                keywords=[w for t in topics[:6] for w in _words_of(t)][:8],
                lessons=[1, 2, 7],
            )
        )
    if not outcomes:
        outcomes = [
            Outcome(
                id=f"{uid}.o1",
                text=f"Learn the vocabulary, grammar and skills of Unit {number} from its "
                "textbook pages.",
                lessons=[1, 2, 3, 4, 5, 6, 7],
            )
        ]
    else:
        covered = {n for outcome in outcomes for n in outcome.lessons}
        missing = [n for n in range(1, 8) if n not in covered]
        if missing:
            outcomes.append(
                Outcome(
                    id=f"{uid}.o{len(outcomes) + 1}",
                    text=f"Practise the language of Unit {number} from its textbook pages.",
                    lessons=missing,
                )
            )
    return Unit(
        id=uid,
        title=title,
        textbook_units=[TextbookUnitRef(series="global-success", unit=number)],
        lessons=7,
        outcomes=outcomes,
    )


# --- the curriculum ----------------------------------------------------------------------------


def _blank_file(version: str, grade: int) -> CurriculumFile:
    secondary = grade >= SECONDARY_FROM
    return CurriculumFile(
        version=version,
        subject="tieng-anh",
        grade=grade,
        status="seed",
        period_minutes=45 if secondary else 35,
        unit_shape="secondary" if secondary else "primary",
        vocabulary=VocabularyScope(level="A2-B1" if secondary else "pre-A1"),
        units=[],
    )


def curriculum_for(
    grade: int,
    unit_number: int | str,
    sources: Mapping[str, SourceDocument] | Iterable[SourceDocument],
    *,
    curriculum_dir: Path | None = None,
    version: str = DEFAULT_VERSION,
) -> Curriculum:
    """The curriculum for ``(grade, unit_number)``, with the unit ``ta{grade}.u{unit_number}``.

    The YAML file of the grade supplies the unit when it lists it. Otherwise the unit is built
    from ``sources`` and added to the grade's file (or to a blank grade when there is no file),
    whose closed word list is dropped: the unit's own pages are the reference.
    """
    if grade not in GRADES:
        raise ValueError(f"grade must be 1 to 9, got {grade}")
    number = parse_unit_number(unit_number, grade)
    uid = unit_id_for(grade, number)
    if curriculum_dir is None:
        from classdeck.config import get_settings

        curriculum_dir = get_settings().curriculum_dir
    try:
        curriculum = load_curriculum(curriculum_dir, version, grade)
    except CurriculumNotFound:
        curriculum = None
    if curriculum is not None and any(unit.id == uid for unit in curriculum.file.units):
        return curriculum

    spans = _spans(_documents(sources))
    base = curriculum.file if curriculum is not None else _blank_file(version, grade)
    built = (
        secondary_unit(grade, number, spans)
        if grade >= SECONDARY_FROM
        else primary_unit(grade, number, spans)
    )
    vocabulary = base.vocabulary.model_copy(update={"words": []})
    return Curriculum(
        base.model_copy(update={"units": [*base.units, built], "vocabulary": vocabulary})
    )
