"""HTML deck writer: one self-contained, pupil-facing slide file with a teacher layer.

The deck is the primary export (``docs/output-formats.md``, "HTML deck"): a 16:9 stage scaled to
the window, one slide per block as the slide-type table prescribes, every picture embedded as a
data URI and no external request of any kind. Pupil mode shows clean slides; teacher mode (key
``T`` or ``?teacher=1``) adds the notes panel, the source footers, the AI-added badges, the
warnings and the source map. Rendering is string substitution over ``deck_template.html`` with
escaped fragments; the look comes from ``themes/<theme>.css``.
"""

from __future__ import annotations

import base64
import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

from classdeck.english.rules import PHASES
from classdeck.export.source_map import (
    AI_ADDED_BADGE,
    AI_ADDED_NOTE,
    AI_PICTURE_CAPTION,
    PROVENANCE_LABEL,
    build_source_map,
    collect_warnings,
    ensure_exportable,
    format_check,
    format_citation,
    origin_text,
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
    Provenance,
    Section,
    SentencePatternBlock,
    SourceDocument,
    SpanKind,
    SummaryBlock,
    TeacherNoteBlock,
    VocabItem,
    VocabularyBlock,
)

DEFAULT_THEME = "kids"
TEMPLATE_NAME = "deck_template.html"
THEMES_DIR = "themes"

PHASE_NAME: dict[Phase, str] = {
    Phase.WARM_UP: "Warm-up",
    Phase.PRESENTATION: "Presentation",
    Phase.PRACTICE: "Practice",
    Phase.PRODUCTION: "Production",
    Phase.WRAP_UP: "Wrap-up",
}
_PHASE_DESCRIPTION: dict[Phase, str] = {spec.phase: spec.description for spec in PHASES}

_SLOT = re.compile(r"(_{2,})")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_VOID_TAGS = frozenset({"img", "meta", "br", "hr", "link", "input"})
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


# --- resources ---------------------------------------------------------------------------------


def available_themes() -> list[str]:
    """Theme names, one per ``themes/<name>.css`` shipped with the package."""
    folder = resources.files("classdeck.export").joinpath(THEMES_DIR)
    return sorted(
        entry.name[: -len(".css")] for entry in folder.iterdir() if entry.name.endswith(".css")
    )


@lru_cache(maxsize=8)
def _theme_css(theme: str) -> str:
    themes = available_themes()
    if theme not in themes:
        raise ValueError(f"unknown theme {theme!r}; available themes: {', '.join(themes)}")
    resource = resources.files("classdeck.export").joinpath(THEMES_DIR, f"{theme}.css")
    return resource.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def _template() -> str:
    return resources.files("classdeck.export").joinpath(TEMPLATE_NAME).read_text(encoding="utf-8")


def _substitute(template: str, values: Mapping[str, str]) -> str:
    """Replace every ``{{name}}`` once; inserted text is never scanned again."""

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise KeyError(f"template placeholder {{{{{name}}}}} has no value")
        return values[name]

    return _PLACEHOLDER.sub(replace, template)


# --- fragments ---------------------------------------------------------------------------------


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _attr(name: str, value: str | bool | None) -> str:
    if value is None or value is False:
        return ""
    key = name.rstrip("_").replace("_", "-")
    return f" {key}" if value is True else f' {key}="{_esc(value)}"'


def _el(tag: str, inner: str = "", /, **attrs: str | bool | None) -> str:
    """``<tag attrs>inner</tag>``; ``class_`` and ``data_block_id`` become ``class`` and
    ``data-block-id``, ``None``/``False`` attributes are dropped, ``True`` is a bare attribute."""
    rendered = "".join(_attr(name, value) for name, value in attrs.items())
    if tag in _VOID_TAGS:
        return f"<{tag}{rendered}>"
    return f"<{tag}{rendered}>{inner}</{tag}>"


def _icon(name: str) -> str:
    """An empty span the theme fills with an emoji (or hides); the HTML itself stays plain."""
    return _el("span", class_="icon", data_icon=name, aria_hidden="true")


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _countdown(minutes: int) -> str:
    seconds = minutes * 60
    label = f"Countdown, {minutes} minute{'s' if minutes != 1 else ''}"
    return _el(
        "div",
        _el("span", _clock(seconds), class_="time") + _el("span", "Tap to start", class_="hint ui"),
        class_="countdown tap ui",
        role="timer",
        tabindex="0",
        data_seconds=str(seconds),
        aria_label=label,
    )


def _note(label: str, text: str) -> str:
    return _el("p", _el("strong", f"{_esc(label)}: ") + _esc(text))


def _note_list(label: str, items: Sequence[str], *, ordered: bool = False) -> str:
    listed = "".join(_el("li", _esc(item)) for item in items)
    return _el("p", _el("strong", _esc(label))) + _el("ol" if ordered else "ul", listed)


def _words(value: str) -> str:
    return value.replace("_", " ")


def _slot_markup(pattern: str) -> str:
    """The pattern with each ``___`` slot wrapped in a dashed-box span."""
    return "".join(
        _el("span", _esc(part), class_="slot", data_empty=part)
        if part.startswith("__")
        else _esc(part)
        for part in _SLOT.split(pattern)
        if part
    )


def slot_fills(pattern: str, example: str) -> list[str] | None:
    """What goes into each ``___`` slot so that the pattern reads as ``example``.

    The literal parts of the pattern are matched in order (ignoring case): the first must open
    the example and the last must close it. ``None`` when the example does not fit the pattern,
    or the pattern has no slot.
    """
    parts = _SLOT.split(pattern)
    literals, slots = parts[0::2], parts[1::2]
    if not slots:
        return None
    lowered = example.lower()
    first = literals[0]
    if first and not lowered.startswith(first.lower()):
        return None
    position = len(first)
    fills: list[str] = []
    for index, literal in enumerate(literals[1:]):
        last = index == len(slots) - 1
        if last and not literal:
            end = len(example)
        elif last:
            end = len(example) - len(literal)
            if end < position or not lowered.endswith(literal.lower()):
                return None
        else:
            # A literal between two slots is never empty: the slot regex is greedy.
            end = lowered.find(literal.lower(), position)
            if end < 0:
                return None
        fill = example[position:end].strip()
        if not fill:
            return None
        fills.append(fill)
        position = end + len(literal)
    return fills


def _example_item(pattern: str, example: str) -> str:
    """An example as a list item; ``data-fills`` holds the slot text when it aligns."""
    fills = slot_fills(pattern, example)
    return _el(
        "li",
        _esc(example),
        class_="example",
        data_fills=json.dumps(fills, ensure_ascii=False) if fills else None,
    )


def _mark_letters(word: str, letters: str) -> str:
    """The word with every occurrence of the target letters wrapped in ``<mark>``."""
    if not letters:
        return _esc(word)
    pieces: list[str] = []
    position = 0
    for match in re.finditer(re.escape(letters), word, re.IGNORECASE):
        pieces.append(_esc(word[position : match.start()]))
        pieces.append(_el("mark", _esc(match.group())))
        position = match.end()
    pieces.append(_esc(word[position:]))
    return "".join(pieces)


def _footer_text(block: Block) -> str:
    """``b_02 · Source: p. 6, ¶3``, or ``Derived from …`` for derived blocks."""
    parts = [block.id]
    citations = list(dict.fromkeys(format_citation(citation) for citation in block.sources))
    if citations:
        parts.append("Source: " + ", ".join(citations))
    if block.provenance is Provenance.DERIVED:
        refs = list(block.depends_on)
        if isinstance(block, ObjectiveBlock):
            refs.extend(block.content.outcome_ids)
        parts.append("Derived from " + (", ".join(refs) or "curriculum"))
    return " · ".join(parts)


def _block_attrs(block: Block) -> dict[str, str]:
    return {
        "data_block_id": block.id,
        "data_kind": block.kind,
        "data_provenance": block.provenance.value,
    }


# --- slides ------------------------------------------------------------------------------------


@dataclass
class _Slide:
    type: str
    phase: str
    body: str
    footer: str = ""
    badge: bool = False
    teacher_only: bool = False
    notes: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    def render(self, number: int, total: int) -> str:
        classes = ["slide"]
        if self.teacher_only:
            classes.append("teacher")
        if self.badge:
            classes.append("badged")
        parts = [_el("div", class_="band", aria_hidden="true")]
        if self.badge:
            parts.append(_el("span", _esc(AI_ADDED_BADGE), class_="badge teacher"))
        parts.append(_el("div", self.body, class_="slide-body"))
        if self.footer:
            parts.append(_el("footer", _esc(self.footer), class_="teacher slide-footer"))
        if self.notes:
            panel = _el("h2", "Teacher notes") + "".join(self.notes)
            parts.append(_el("aside", panel, class_="teacher notes", aria_label="Teacher notes"))
        return _el(
            "section",
            "\n".join(parts),
            class_=" ".join(classes),
            data_phase=self.phase,
            data_slide_type=self.type,
            aria_roledescription="slide",
            aria_label=f"Slide {number} of {total}",
            **self.extra,
        )


class _DeckBuilder:
    def __init__(
        self,
        lesson: Lesson,
        sources: Mapping[str, SourceDocument] | None,
        image_root: Path | None,
        title: str,
    ) -> None:
        self.lesson = lesson
        self.sources = sources
        self.image_root = image_root
        self.title = title
        self.slides: list[_Slide] = []
        self._pictures: dict[Path, str | None] = {}

    def build(self) -> str:
        cover = self._cover()
        for section in self.lesson.sections:
            self._divider(section)
            for block_id in section.block_ids:
                self._block(section, self.lesson.blocks[block_id], cover)
        self._notes_slide()
        self._source_map_slide()
        total = len(self.slides)
        return "\n".join(slide.render(n, total) for n, slide in enumerate(self.slides, start=1))

    # -- plumbing

    def _add(
        self,
        slide_type: str,
        phase: str,
        body: str,
        *,
        block: Block | None = None,
        first: bool = True,
        notes: Sequence[str] = (),
        teacher_only: bool = False,
        extra: Mapping[str, str] | None = None,
    ) -> _Slide:
        """Append a slide; a block slide gets the footer, the badge and the block's own note."""
        slide = _Slide(slide_type, phase, body, teacher_only=teacher_only, extra=dict(extra or {}))
        if block is not None:
            slide.footer = _footer_text(block)
            slide.badge = block.provenance is Provenance.GENERATED
            if slide.badge:
                slide.notes.append(_el("p", _esc(AI_ADDED_NOTE), class_="ai-note"))
            if first and block.teacher_note:
                slide.notes.append(_note("Teacher note", block.teacher_note))
        slide.notes.extend(notes)
        self.slides.append(slide)
        return slide

    def _picture(self, citation: Citation | None) -> str | None:
        """A ``data:`` URI for the image span a citation points at, if its file is under
        ``image_root``; paths that escape the root and unknown formats give ``None``."""
        if citation is None or self.sources is None or self.image_root is None:
            return None
        document = self.sources.get(citation.doc_id)
        span = document.find_span(citation.page, citation.span_id) if document else None
        if span is None or span.kind is not SpanKind.IMAGE or not span.image_path:
            return None
        return self._file_uri(span.image_path)

    def _file_uri(self, relative: str) -> str | None:
        """A ``data:`` URI for ``relative`` under ``image_root``; ``None`` when it escapes the root,
        is missing or is not a known image format."""
        if self.image_root is None:
            return None
        root = self.image_root.resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        if path not in self._pictures:
            self._pictures[path] = _data_uri(path.read_bytes())
        return self._pictures[path]

    def _vocab_picture(self, item: VocabItem) -> tuple[str | None, bool]:
        """The flashcard picture and whether it was generated: the cited textbook picture first,
        else the item's ``generated_picture``."""
        uri = self._picture(item.image)
        if uri:
            return uri, False
        if item.generated_picture:
            uri = self._file_uri(item.generated_picture)
            if uri:
                return uri, True
        return None, False

    def _image(
        self,
        uri: str,
        *,
        block_id: str,
        item: int,
        alt: str,
        class_: str = "",
        generated: bool = False,
    ) -> str:
        return _el(
            "img",
            class_=class_ or None,
            data_block_id=block_id,
            data_item=str(item),
            data_generated="true" if generated else None,
            alt=alt,
            src=uri,
        )

    # -- framing slides

    def _cover(self) -> _Slide:
        lesson, ref = self.lesson, self.lesson.curriculum
        meta = [
            f"Grade {lesson.grade}",
            ref.unit_id,
            f"Lesson {ref.lesson_no}" if ref.lesson_no is not None else "",
            f"Period {ref.period}" if ref.period is not None else "",
        ]
        strip = "".join(
            _el(
                "li",
                _el("span", class_="chip")
                + _icon(section.phase.value)
                + _el("span", _esc(PHASE_NAME[section.phase]), class_="label ui")
                + _el("span", f"{section.minutes} min", class_="minutes ui"),
                data_phase=section.phase.value,
            )
            for section in lesson.sections
        )
        total = sum(section.minutes for section in lesson.sections)
        body = _el("h1", _esc(self.title), class_="slide-title")
        body += _el("p", _esc(" · ".join(part for part in meta if part)), class_="meta ui")
        if strip:
            body += _el("ol", strip, class_="phase-strip", aria_label="Lesson phases")
            body += _el("p", f"{total} min", class_="total ui")
        return self._add("cover", "lesson", body)

    def _divider(self, section: Section) -> None:
        phase = section.phase
        card = _icon(phase.value)
        card += _el("p", _esc(PHASE_NAME[phase]), class_="phase-name ui")
        card += _el("h2", _esc(section.title), class_="slide-title")
        card += _el("p", f"{section.minutes} min", class_="minutes ui")
        if section.minutes:
            card += _countdown(section.minutes)
        notes = [_el("p", _esc(_PHASE_DESCRIPTION[phase]))]
        self._add(
            "divider",
            phase.value,
            _el("div", card, class_="card"),
            notes=notes,
            extra={"data_section_id": section.id, "data_minutes": str(section.minutes)},
        )

    def _notes_slide(self) -> None:
        warnings = collect_warnings(self.lesson)
        if not warnings:
            return
        items = "".join(
            _el("li", _esc(format_check(block_id, check))) for block_id, check in warnings
        )
        body = _el("h2", "Notes", class_="slide-title") + _el("ul", items, class_="warnings")
        self._add("notes", "teacher", body, teacher_only=True)

    def _source_map_slide(self) -> None:
        header = "".join(_el("th", h) for h in ("Block", "Kind", "Provenance", "Citation", "Quote"))
        rows = "".join(
            _el(
                "tr",
                _el("td", _esc(row.block_id))
                + _el("td", _esc(_words(row.kind.value)))
                + _el("td", _esc(PROVENANCE_LABEL[row.provenance]))
                + _el("td", _esc(origin_text(row)))
                + _el("td", _esc(row.quote or "")),
            )
            for row in build_source_map(self.lesson, self.sources)
        )
        table = _el(
            "table", _el("thead", _el("tr", header)) + _el("tbody", rows), class_="source-map"
        )
        body = _el("h2", "Source map", class_="slide-title") + table
        self._add("source-map", "teacher", body, teacher_only=True)

    # -- blocks

    def _block(self, section: Section, block: Block, cover: _Slide) -> None:
        match block:
            case ObjectiveBlock():
                cover.notes.append(self._objective(block))
            case TeacherNoteBlock():
                self.slides[-1].notes.append(self._teacher_note(block))
            case VocabularyBlock():
                self._vocabulary(section, block)
            case SentencePatternBlock():
                self._pattern(section, block)
            case DialogueBlock():
                self._dialogue(section, block)
            case PhonicsBlock():
                self._phonics(section, block)
            case ActivityBlock():
                self._activity(section, block)
            case SummaryBlock():
                self._summary(section, block)
            case ImageRefBlock():
                self._image_ref(section, block)

    def _objective(self, block: ObjectiveBlock) -> str:
        """Objectives have no slide; they sit in the cover's notes panel."""
        content = block.content
        details = []
        if content.outcome_ids:
            details.append("Outcomes: " + ", ".join(content.outcome_ids))
        if content.skills:
            details.append("Skills: " + ", ".join(skill.value for skill in content.skills))
        label = "Objective"
        if block.provenance is Provenance.GENERATED:
            label += f" ({AI_ADDED_BADGE})"
        inner = _note(label, content.text)
        if block.provenance is Provenance.GENERATED:
            inner += _el("p", _esc(AI_ADDED_NOTE), class_="ai-note")
        if details:
            inner += _el("p", _esc(" · ".join(details)))
        if block.teacher_note:
            inner += _el("p", _esc(block.teacher_note))
        inner += _el("p", _esc(_footer_text(block)), class_="origin")
        return _el("div", inner, class_="teacher block", **_block_attrs(block))

    def _teacher_note(self, block: TeacherNoteBlock) -> str:
        """Teacher notes have no slide; they join the previous slide's notes panel."""
        label = "Teacher note"
        if block.provenance is Provenance.GENERATED:
            label += f" ({AI_ADDED_BADGE})"
        inner = _note(label, block.content.text)
        if block.provenance is Provenance.GENERATED:
            inner += _el("p", _esc(AI_ADDED_NOTE), class_="ai-note")
        if block.teacher_note:
            inner += _el("p", _esc(block.teacher_note))
        inner += _el("p", _esc(_footer_text(block)), class_="origin")
        return _el("div", inner, class_="teacher block", **_block_attrs(block))

    def _vocabulary(self, section: Section, block: VocabularyBlock) -> None:
        items = block.content.items
        for index, item in enumerate(items):
            picture, generated = self._vocab_picture(item)
            reveal = bool(item.meaning_vi)
            inner = ""
            if picture:
                inner += self._image(
                    picture,
                    block_id=block.id,
                    item=index,
                    alt=item.word,
                    class_="picture",
                    generated=generated,
                )
                if generated:
                    inner += _el("span", _esc(AI_PICTURE_CAPTION), class_="ai-picture teacher")
            inner += _el("p", _esc(item.word), class_="word")
            if item.ipa:
                inner += _el("p", _esc(item.ipa), class_="ipa")
            if reveal:
                inner += _el("p", _esc(item.meaning_vi), class_="meaning", lang="vi")
                inner += _el("p", "Tap to see", class_="hint ui")
            classes = ["block", "flashcard", "card"]
            if picture:
                classes.append("has-picture")
            elif not reveal:
                classes.append("bare")
            if reveal:
                classes += ["tap", "reveal"]
            card = _el(
                "article",
                inner,
                class_=" ".join(classes),
                **_block_attrs(block),
                data_item=str(index),
                role="button" if reveal else None,
                tabindex="0" if reveal else None,
                aria_pressed="false" if reveal else None,
            )
            notes = []
            if item.pos:
                notes.append(_note("Part of speech", item.pos))
            if item.example:
                notes.append(_note("Example", item.example))
            if item.image is not None:
                notes.append(_note("Picture", format_citation(item.image)))
            self._add(
                "flashcard", section.phase.value, card, block=block, first=index == 0, notes=notes
            )
        if not items:
            # Keep a carrier so the block's footer, badge and teacher note still render.
            empty = _el(
                "article",
                _el("p", "No words yet", class_="hint ui"),
                class_="block flashcard card",
                **_block_attrs(block),
            )
            self._add("flashcard", section.phase.value, empty, block=block)

    def _pattern(self, section: Section, block: SentencePatternBlock) -> None:
        content = block.content
        examples = "".join(_example_item(content.pattern, ex) for ex in content.examples)
        interactive = bool(content.examples)
        inner = _el("p", _slot_markup(content.pattern), class_="pattern-text")
        inner += _el("p", class_="example-line", aria_live="polite")
        if content.function:
            inner += _el("p", _esc(content.function), class_="function")
        if interactive:
            inner += _el("ul", examples, class_="examples")
            inner += _el("p", "Tap to try", class_="hint ui")
        body = _el(
            "div",
            inner,
            class_="block pattern card tap cycle" if interactive else "block pattern card",
            **_block_attrs(block),
            role="button" if interactive else None,
            tabindex="0" if interactive else None,
        )
        notes = [_note("Notes", content.notes)] if content.notes else []
        self._add("pattern", section.phase.value, body, block=block, notes=notes)

    def _dialogue(self, section: Section, block: DialogueBlock) -> None:
        content = block.content
        sides: dict[str, int] = {}
        bubbles = []
        for number, line in enumerate(content.lines):
            side = "left" if sides.setdefault(line.speaker, len(sides)) % 2 == 0 else "right"
            avatar = _el(
                "span", _esc(line.speaker[:1].upper() or "?"), class_="avatar", aria_hidden="true"
            )
            speaker = (
                _el("span", _esc(f"{line.speaker}: "), class_="sr-only") if line.speaker else ""
            )
            speech = _el("p", speaker + _esc(line.text), class_="speech")
            bubbles.append(
                _el(
                    "div",
                    avatar + speech,
                    class_=f"bubble step-item {'pending ' if number else ''}{side}",
                    data_line=str(number),
                    data_speaker=line.speaker,
                )
            )
        inner = ""
        if content.context:
            inner += _el("p", _esc(content.context), class_="chat-context")
        inner += _el("div", "".join(bubbles), class_="chat")
        inner += _el("p", "Tap for the next line", class_="hint ui")
        body = _el(
            "div",
            inner,
            class_="block dialogue tap stepper" + ("" if len(content.lines) > 1 else " complete"),
            **_block_attrs(block),
            role="button",
            tabindex="0",
            aria_label="Dialogue: tap to show the next line",
        )
        notes = [_note("Context", content.context)] if content.context else []
        self._add("dialogue", section.phase.value, body, block=block, notes=notes)

    def _phonics(self, section: Section, block: PhonicsBlock) -> None:
        content = block.content
        inner = _el("p", _esc(content.sound), class_="sound")
        if content.letters:
            inner += _el("p", _esc(content.letters), class_="letters")
        if content.words:
            words = "".join(
                _el("li", _mark_letters(word, content.letters)) for word in content.words
            )
            inner += _el("ul", words, class_="words")
        if content.chant_lines:
            chant = "".join(_el("li", _esc(line)) for line in content.chant_lines)
            inner += _el("ul", chant, class_="chant")
        body = _el("div", inner, class_="block phonics", **_block_attrs(block))
        self._add("phonics", section.phase.value, body, block=block)

    def _activity(self, section: Section, block: ActivityBlock) -> None:
        content = block.content
        heading = _icon(content.type.value) + _el("h2", _esc(content.title), class_="slide-title")
        inner = _el("div", heading, class_="heading")
        if content.instructions:
            inner += _el("p", _esc(content.instructions), class_="instructions")
        if content.student_steps:
            steps = "".join(
                _el(
                    "li",
                    _el("span", str(number), class_="num", aria_hidden="true")
                    + _el("span", _esc(step), class_="step-text"),
                    class_="step card",
                )
                for number, step in enumerate(content.student_steps, start=1)
            )
            inner += _el("ol", steps, class_="steps")
        inner += _countdown(content.minutes)
        body = _el("div", inner, class_="block activity", **_block_attrs(block))

        notes = [
            _note("Activity", f"{_words(content.type.value)} · {content.minutes} min"),
            _note("Interaction", _words(content.interaction.value)),
        ]
        if content.skills:
            notes.append(_note("Skills", ", ".join(skill.value for skill in content.skills)))
        if content.teacher_steps:
            notes.append(_note_list("Teacher steps", content.teacher_steps, ordered=True))
        if content.materials:
            notes.append(_note("Materials", ", ".join(content.materials)))
        if content.competency_tags:
            notes.append(_note("Competences", ", ".join(content.competency_tags)))
        self._add("activity", section.phase.value, body, block=block, notes=notes)

    def _summary(self, section: Section, block: SummaryBlock) -> None:
        """The lesson's words (with pictures where available) and its pattern(s); the block's
        own bullets go to the notes, or to the slide when there is nothing else to show."""
        cards = []
        for vocabulary in self.lesson.ordered_blocks():
            if not isinstance(vocabulary, VocabularyBlock):
                continue
            for index, item in enumerate(vocabulary.content.items):
                picture, generated = self._vocab_picture(item)
                image = (
                    self._image(
                        picture,
                        block_id=vocabulary.id,
                        item=index,
                        alt=item.word,
                        generated=generated,
                    )
                    if picture
                    else ""
                )
                cards.append(
                    _el(
                        "figure",
                        image + _el("figcaption", _esc(item.word)),
                        class_="word-card card",
                    )
                )
        patterns = [
            _el("p", _slot_markup(pattern.content.pattern), class_="pattern-text")
            for pattern in self.lesson.ordered_blocks()
            if isinstance(pattern, SentencePatternBlock)
        ]
        inner = _el("h2", "Let's remember!", class_="slide-title ui")
        if cards:
            inner += _el("div", "".join(cards), class_="word-grid")
        inner += "".join(patterns)
        bullets = block.content.bullets
        if bullets and not cards and not patterns:
            inner += _el("ul", "".join(_el("li", _esc(b)) for b in bullets), class_="bullets")
        body = _el("div", inner, class_="block summary", **_block_attrs(block))
        notes = [_note_list("Summary", bullets)] if bullets else []
        self._add("summary", section.phase.value, body, block=block, notes=notes)

    def _image_ref(self, section: Section, block: ImageRefBlock) -> None:
        content = block.content
        picture = next((uri for c in block.sources if (uri := self._picture(c)) is not None), None)
        caption = _el("figcaption", _esc(content.caption)) if content.caption else ""
        if picture:
            image = self._image(
                picture, block_id=block.id, item=0, alt=content.alt, class_="picture"
            )
            body = _el("figure", image + caption, class_="block image-ref", **_block_attrs(block))
        else:
            missing = _el(
                "p", _el("span", "Picture: ", class_="ui") + _esc(content.alt), class_="placeholder"
            )
            body = _el("figure", missing + caption, class_="block image-ref", **_block_attrs(block))
        notes = [_note("Picture", content.alt)]
        self._add("image", section.phase.value, body, block=block, notes=notes)


def _data_uri(data: bytes) -> str | None:
    """``data:image/...;base64,…`` for PNG, JPEG, GIF or WebP bytes; ``None`` otherwise."""
    media_type = next((mime for magic, mime in _IMAGE_SIGNATURES if data.startswith(magic)), None)
    if media_type is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    if media_type is None:
        return None
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


# --- behaviour ---------------------------------------------------------------------------------

SCRIPT = r"""
(function () {
  'use strict';
  var WIDTH = 1920, HEIGHT = 1080;
  var body = document.body;
  var stage = document.getElementById('stage');
  var dots = document.getElementById('dots');
  var slides = Array.prototype.slice.call(stage.querySelectorAll('.slide'));
  var shown = null;

  function teacherMode() { return body.classList.contains('teacher-mode'); }

  function visible() {
    return slides.filter(function (slide) {
      return teacherMode() || !slide.classList.contains('teacher');
    });
  }

  function fit() {
    var scale = Math.min(window.innerWidth / WIDTH, window.innerHeight / HEIGHT);
    stage.style.transform = 'scale(' + scale + ')';
    stage.style.left = Math.floor((window.innerWidth - WIDTH * scale) / 2) + 'px';
    stage.style.top = Math.floor((window.innerHeight - HEIGHT * scale) / 2) + 'px';
  }

  function renderDots(list, index) {
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    list.forEach(function (slide, n) {
      var dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'dot ui' + (n === index ? ' current' : '');
      dot.setAttribute('data-phase', slide.getAttribute('data-phase') || '');
      dot.setAttribute('aria-label', 'Slide ' + (n + 1) + ' of ' + list.length);
      dot.addEventListener('click', function () { show(n); });
      dots.appendChild(dot);
    });
  }

  function show(index) {
    var list = visible();
    if (!list.length) return;
    index = Math.max(0, Math.min(index, list.length - 1));
    slides.forEach(function (slide) {
      slide.classList.remove('active');
      slide.setAttribute('aria-hidden', 'true');
    });
    shown = list[index];
    shown.classList.add('active');
    shown.removeAttribute('aria-hidden');
    renderDots(list, index);
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, '', '#' + (index + 1));
    }
  }

  function position() {
    var list = visible();
    var index = list.indexOf(shown);
    if (index >= 0) return index;
    var before = slides.indexOf(shown);
    for (var n = list.length - 1; n >= 0; n--) {
      if (slides.indexOf(list[n]) < before) return n;
    }
    return 0;
  }

  function next() { show(position() + 1); }
  function prev() { show(position() - 1); }
  function refresh() { show(position()); }

  function act(el) {
    if (el.classList.contains('reveal')) {
      var on = el.classList.toggle('revealed');
      el.setAttribute('aria-pressed', on ? 'true' : 'false');
    } else if (el.classList.contains('stepper')) {
      step(el);
    } else if (el.classList.contains('cycle')) {
      cycle(el);
    } else if (el.classList.contains('countdown')) {
      countdown(el);
    }
  }

  function advance() {
    var target = shown && shown.querySelector('.tap');
    if (target) act(target);
  }

  function step(el) {
    var pending = el.querySelector('.step-item.pending');
    if (pending) {
      pending.classList.remove('pending');
      el.classList.toggle('complete', !el.querySelector('.step-item.pending'));
      return;
    }
    Array.prototype.forEach.call(el.querySelectorAll('.step-item'), function (item, n) {
      if (n > 0) item.classList.add('pending');
    });
    el.classList.toggle('complete', !el.querySelector('.step-item.pending'));
  }

  function cycle(el) {
    var examples = el.querySelectorAll('.example');
    var slots = el.querySelectorAll('.slot');
    var line = el.querySelector('.example-line');
    if (!examples.length) return;
    var index = parseInt(el.getAttribute('data-index') || '-1', 10) + 1;
    if (index >= examples.length) index = -1;
    el.setAttribute('data-index', String(index));
    var fills = null;
    Array.prototype.forEach.call(examples, function (example, n) {
      example.classList.toggle('current', n === index);
    });
    if (index >= 0 && examples[index].hasAttribute('data-fills')) {
      try {
        fills = JSON.parse(examples[index].getAttribute('data-fills'));
      } catch (err) {
        fills = null;
      }
    }
    Array.prototype.forEach.call(slots, function (slot, n) {
      var text = fills ? fills[n] : null;
      slot.textContent = text || slot.getAttribute('data-empty');
      slot.classList.toggle('filled', !!text);
    });
    if (line) line.textContent = index >= 0 && !fills ? examples[index].textContent : '';
  }

  function clock(seconds) {
    var minutes = Math.floor(seconds / 60), rest = seconds % 60;
    return minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function countdown(el) {
    var total = parseInt(el.getAttribute('data-seconds'), 10) || 0;
    var time = el.querySelector('.time');
    var hint = el.querySelector('.hint');
    if (el.remaining === undefined) el.remaining = total;
    if (el.classList.contains('done')) {
      el.classList.remove('done');
      el.remaining = total;
      time.textContent = clock(total);
      hint.textContent = 'Tap to start';
      return;
    }
    if (el.timer) {
      clearInterval(el.timer);
      el.timer = null;
      el.classList.remove('running');
      hint.textContent = 'Tap to continue';
      return;
    }
    var end = Date.now() + el.remaining * 1000;
    el.classList.add('running');
    hint.textContent = 'Tap to pause';
    el.timer = setInterval(function () {
      var left = Math.max(0, Math.round((end - Date.now()) / 1000));
      el.remaining = left;
      time.textContent = clock(left);
      if (left === 0) {
        clearInterval(el.timer);
        el.timer = null;
        el.classList.remove('running');
        el.classList.add('done');
        hint.textContent = "Time's up! Tap to reset";
      }
    }, 250);
  }

  function fullscreen() {
    var root = document.documentElement;
    if (document.fullscreenElement) {
      if (document.exitFullscreen) document.exitFullscreen();
    } else if (root.requestFullscreen) {
      var request = root.requestFullscreen();
      if (request && request.catch) request.catch(function () {});
    }
  }

  function setTeacher(on) {
    body.classList.toggle('teacher-mode', on);
    if (on) body.classList.add('notes-open');
    refresh();
  }

  stage.addEventListener('click', function (event) {
    var tap = event.target.closest('.tap');
    if (tap) { act(tap); return; }
    if (event.target.closest('button, a, .notes, .dots')) return;
    var rect = stage.getBoundingClientRect();
    var x = (event.clientX - rect.left) / rect.width;
    if (x >= 2 / 3) next();
    else if (x <= 1 / 3) prev();
  });

  var touch = null;
  stage.addEventListener('touchstart', function (event) {
    var point = event.changedTouches[0];
    touch = { x: point.clientX, y: point.clientY };
  }, { passive: true });
  stage.addEventListener('touchend', function (event) {
    if (!touch) return;
    var point = event.changedTouches[0];
    var dx = point.clientX - touch.x, dy = point.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) next(); else prev();
  }, { passive: true });

  document.addEventListener('keydown', function (event) {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    var target = event.target && event.target.closest ? event.target : document.body;
    var activate = event.key === 'Enter' || event.key === ' ';
    if (activate && target.closest('button, a')) return;
    var focused = target.closest('.tap');
    if (activate && focused) {
      event.preventDefault();
      act(focused);
      return;
    }
    switch (event.key) {
      case 'ArrowRight': case 'ArrowDown': case ' ': case 'PageDown':
        event.preventDefault(); next(); break;
      case 'ArrowLeft': case 'ArrowUp': case 'PageUp':
        event.preventDefault(); prev(); break;
      case 'Home': event.preventDefault(); show(0); break;
      case 'End': event.preventDefault(); show(visible().length - 1); break;
      case 'Enter': event.preventDefault(); advance(); break;
      case 'f': case 'F': fullscreen(); break;
      case 't': case 'T': setTeacher(!teacherMode()); break;
      case 'n': case 'N': body.classList.toggle('notes-open'); break;
      case 'p': case 'P': window.print(); break;
    }
  });

  var params = new URLSearchParams(window.location.search);
  if (params.get('teacher') === '1') {
    body.classList.add('teacher-mode');
    body.classList.add('notes-open');
  }
  var start = parseInt((window.location.hash || '').slice(1), 10);
  shown = slides[0] || null;
  show(isNaN(start) ? 0 : start - 1);
  fit();
  window.addEventListener('resize', fit);
  window.addEventListener('orientationchange', fit);
})();
""".strip()


# --- entry points ------------------------------------------------------------------------------


def render_html(
    lesson: Lesson,
    *,
    sources: Mapping[str, SourceDocument] | None = None,
    image_root: Path | None = None,
    theme: str = DEFAULT_THEME,
    title: str | None = None,
) -> str:
    """The complete deck as one HTML document, without writing it.

    Raises :class:`~classdeck.export.source_map.ExportBlocked` if any check failed and
    ``ValueError`` for an unknown theme. Pictures are embedded only when ``sources`` and
    ``image_root`` are given and the cited image span's file exists under ``image_root``.
    """
    ensure_exportable(lesson)
    theme_css = _theme_css(theme)
    heading = title or lesson.title
    slides = _DeckBuilder(lesson, sources, image_root, heading).build()
    return _substitute(
        _template(),
        {
            "lang": _esc(lesson.language),
            "title": _esc(heading),
            "theme": _esc(theme),
            "theme_css": theme_css,
            "lesson_id": _esc(lesson.id),
            "slides": slides,
            "script": SCRIPT,
        },
    )


def export_html(
    lesson: Lesson,
    out_path: Path,
    *,
    sources: Mapping[str, SourceDocument] | None = None,
    image_root: Path | None = None,
    theme: str = DEFAULT_THEME,
    title: str | None = None,
) -> Path:
    """Write the lesson as a self-contained HTML deck and return the path.

    Same gate and picture rules as :func:`render_html`; nothing is written when it raises.
    """
    document = render_html(lesson, sources=sources, image_root=image_root, theme=theme, title=title)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8", newline="\n")
    return path
