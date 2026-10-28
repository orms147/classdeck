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
from classdeck.export import fit
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
    ChoiceBlock,
    Citation,
    DialogueBlock,
    DialogueLine,
    GalleryBlock,
    GapFillBlock,
    GapItem,
    GridBlock,
    ImageRefBlock,
    Lesson,
    MatchBlock,
    ObjectiveBlock,
    Phase,
    PhonicsBlock,
    Provenance,
    QuestionSetBlock,
    ReadingBlock,
    ReadingParagraph,
    RuleBoxBlock,
    Section,
    SelfCheckBlock,
    SentencePatternBlock,
    SentencePatternContent,
    SoundSentence,
    SoundSortBlock,
    SoundSortContent,
    SourceDocument,
    SpanKind,
    SummaryBlock,
    TeacherNoteBlock,
    TrueFalseBlock,
    VocabItem,
    VocabularyBlock,
)
from classdeck.lesson.exercises import (
    FALSE,
    TRUE,
    answer_pairs,
    exercise_title,
    instruction_of,
    letter,
    slashed,
)
from classdeck.lesson.labels import lesson_label, unit_line

DEFAULT_THEME = "sunny"
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
_PHASE_DESCRIPTION_SECONDARY: dict[Phase, str] = {
    Phase.WARM_UP: "Activate the topic; no new language.",
    Phase.PRESENTATION: "Input: the conversation or text, then the new words.",
    Phase.PRACTICE: "Controlled practice: the textbook's exercises, in their order.",
    Phase.PRODUCTION: "Freer use of the language: quiz, speaking or writing.",
    Phase.WRAP_UP: "Recap the words, praise the class, set the homework.",
}
"""Lower-secondary lessons (grade 6 and above) describe their phases in their own words."""

READING_CHARS_PER_SLIDE = 620
READING_CHARS_WITH_PICTURE = 380
DENSE_CHARS = 330
TIGHT_MATCH_CHARS = 240
"""A matching exercise whose right-hand column has more characters than this gets smaller type."""
DIALOGUE_BUDGET = 650
"""Design pixels of speech bubbles one dialogue slide may hold (the slide is 1080 high)."""

_SLOT = re.compile(r"(_{2,})")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_VOID_TAGS = frozenset({"img", "meta", "br", "hr", "link", "input", "col"})
_IMAGE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


# --- resources ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeInfo:
    """What a theme file declares about itself in its manifest comment."""

    id: str
    label: str
    description: str
    suits: str


_MANIFEST = re.compile(
    r"\A\s*/\*\s*theme:\s*(?P<id>[a-z][a-z0-9_-]*)\s*\|\s*(?P<label>[^|]+?)\s*\|"
    r"\s*(?P<description>[^|]+?)\s*\|\s*suits:\s*(?P<suits>[^|]+?)\s*\*/"
)


def parse_manifest(css: str, *, name: str = "theme") -> ThemeInfo:
    """The manifest comment at the top of a theme file:
    ``/* theme: <id> | <label> | <description> | suits: <grades> */``."""
    match = _MANIFEST.match(css)
    if match is None:
        raise ValueError(
            f"theme {name!r} must start with a manifest comment "
            "'/* theme: <id> | <label> | <description> | suits: <grades> */'"
        )
    return ThemeInfo(**{key: " ".join(value.split()) for key, value in match.groupdict().items()})


def _theme_files() -> dict[str, str]:
    folder = resources.files("classdeck.export").joinpath(THEMES_DIR)
    return {
        entry.name[: -len(".css")]: entry.read_text(encoding="utf-8").strip()
        for entry in sorted(folder.iterdir(), key=lambda entry: entry.name)
        if entry.name.endswith(".css")
    }


def available_themes() -> list[ThemeInfo]:
    """Every ``themes/<id>.css`` shipped with the package, by id, with its manifest.

    A file whose manifest is missing, malformed or names another id raises ``ValueError``.
    """
    infos: list[ThemeInfo] = []
    for name, css in _theme_files().items():
        info = parse_manifest(css, name=name)
        if info.id != name:
            raise ValueError(f"theme file {name}.css declares the id {info.id!r}")
        infos.append(info)
    return infos


def theme_ids() -> list[str]:
    """The ids of :func:`available_themes`."""
    return [info.id for info in available_themes()]


def _theme_css(theme: str) -> str:
    files = _theme_files()
    if theme not in files:
        raise ValueError(f"unknown theme {theme!r}; available themes: {', '.join(files)}")
    parse_manifest(files[theme], name=theme)
    return files[theme]


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


def _fld(block_id: str, path: str, **extra: str | None) -> dict[str, str | None]:
    """Attributes that make an element editable in place: the block it belongs to and the path
    of the text inside it (``docs/output-formats.md``, "Editing on the slide")."""
    return {"data_field_block": block_id, "data_field": path, **extra}


def _icon(name: str, extra: str = "") -> str:
    """An empty span the theme fills with an emoji (or hides); the HTML itself stays plain."""
    return _el("span", class_=f"icon {extra}".strip(), data_icon=name, aria_hidden="true")


def _speak(text: str) -> str:
    """The pronunciation button for ``text``; empty when there is nothing to say.

    The glyph is drawn by the stylesheet (``.speak__icon``), so a theme can swap it."""
    spoken = " ".join(text.split())
    if not spoken:
        return ""
    return _el(
        "button",
        _el("span", class_="speak__icon", aria_hidden="true"),
        class_="speak ui",
        type="button",
        aria_label="Listen",
        data_say=spoken,
    )


def _decorations() -> str:
    """Three empty, inert elements per slide that a theme may draw on."""
    return "".join(
        _el("div", class_=f"deco deco--{number}", aria_hidden="true") for number in (1, 2, 3)
    )


def _clock(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


def _countdown(minutes: int) -> str:
    seconds = minutes * 60
    label = f"Countdown, {minutes} minute{'s' if minutes != 1 else ''}"
    return _el(
        "div",
        _el("span", _clock(seconds), class_="countdown__time")
        + _el("span", "Tap to start", class_="countdown__hint hint ui"),
        class_="countdown tap ui",
        role="timer",
        tabindex="0",
        data_seconds=str(seconds),
        aria_label=label,
    )


def _text(text: str, field: Mapping[str, str | None] | None) -> str:
    """Escaped ``text``; with a field map it sits in an editable ``<span>``."""
    return _el("span", _esc(text), **field) if field else _esc(text)


def _note(label: str, text: str, field: Mapping[str, str | None] | None = None) -> str:
    return _el("p", _el("strong", f"{_esc(label)}: ") + _text(text, field))


def _note_steps(label: str, text: str, field: Mapping[str, str | None] | None = None) -> str:
    """A note that may hold steps: the first line after the label, the numbered lines as a list.

    With a field map the note is one editable unit: while editing, the page swaps it for the raw
    text (``data-raw``) and rebuilds the markup afterwards."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        shown = _note(label, text)
    else:
        steps = "".join(_el("li", _esc(re.sub(r"^\d+[.)]\s*", "", line))) for line in lines[1:])
        shown = _el("p", _el("strong", f"{_esc(label)}: ") + _esc(lines[0])) + _el("ol", steps)
    if not field:
        return shown
    return _el(
        "div",
        shown,
        class_="note-steps",
        data_raw=text,
        data_rebuild="note",
        data_label=label,
        data_multiline="true",
        **field,
    )


def _note_list(
    label: str,
    items: Sequence[str],
    *,
    ordered: bool = False,
    fields: Sequence[Mapping[str, str | None]] | None = None,
) -> str:
    listed = "".join(
        _el("li", _text(item, fields[number] if fields else None))
        for number, item in enumerate(items)
    )
    return _el("p", _el("strong", _esc(label))) + _el("ol" if ordered else "ul", listed)


def _words(value: str) -> str:
    return value.replace("_", " ")


def _slot_markup(pattern: str) -> str:
    """The pattern with each ``___`` slot wrapped in a dashed-box span."""
    return "".join(
        _el("span", _esc(part), class_="pattern__slot", data_empty=part)
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


def _pattern_speech(content: SentencePatternContent) -> str:
    """What the pattern's speaker button says: the first example, else the pattern without slots."""
    return content.examples[0] if content.examples else _SLOT.sub(" ", content.pattern)


def _example_item(pattern: str, example: str, field: Mapping[str, str | None]) -> str:
    """An example as a list item; ``data-fills`` holds the slot text when it aligns."""
    fills = slot_fills(pattern, example)
    return _el(
        "li",
        _el("span", _esc(example), class_="pattern__example-text", **field) + _speak(example),
        class_="pattern__example",
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


_SPAN_NUMBER = re.compile(r"p\d+\.s(\d+)")


def _compact_sources(citations: Sequence[Citation]) -> list[str]:
    """Citations as ``p. 6, ¶3`` per page, runs of consecutive paragraphs as ``¶3-9``."""
    pages: dict[int, list[int]] = {}
    verbatim: list[str] = []
    for citation in citations:
        found = _SPAN_NUMBER.fullmatch(citation.span_id)
        if found is None:
            verbatim.append(format_citation(citation))
        else:
            pages.setdefault(citation.page, []).append(int(found.group(1)))
    shown: list[str] = []
    for page, numbers in pages.items():
        runs: list[list[int]] = []
        for number in sorted(set(numbers)):
            if runs and number == runs[-1][-1] + 1:
                runs[-1].append(number)
            else:
                runs.append([number])
        listed = ", ".join(
            f"¶{run[0]}" if len(run) == 1 else f"¶{run[0]}-{run[-1]}" for run in runs
        )
        shown.append(f"p. {page}, {listed}")
    return list(dict.fromkeys([*shown, *verbatim]))


def _footer_text(block: Block) -> str:
    """``b_02 · Source: p. 6, ¶3``, or ``Derived from …`` for derived blocks."""
    parts = [block.id]
    citations = _compact_sources(block.sources)
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
    kind: str = ""
    footer: str = ""
    badge: bool = False
    teacher_only: bool = False
    notes: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)

    def render(self, number: int, total: int) -> str:
        classes = ["slide", f"slide--{self.type}"]
        if self.teacher_only:
            classes.append("teacher")
        if self.badge:
            classes.append("badged")
        parts = [_el("div", class_="band", aria_hidden="true"), _decorations()]
        if self.badge:
            parts.append(_el("span", _esc(AI_ADDED_BADGE), class_="badge teacher"))
        parts.append(_el("div", self.body, class_="slide-body"))
        if self.footer:
            parts.append(_el("footer", _esc(self.footer), class_="teacher footer"))
        if self.notes:
            panel = _el("h2", "Teacher notes", class_="notes__title") + "".join(self.notes)
            parts.append(_el("aside", panel, class_="teacher notes", aria_label="Teacher notes"))
        return _el(
            "section",
            "\n".join(parts),
            class_=" ".join(classes),
            data_phase=self.phase,
            data_kind=self.kind or self.type,
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
            blocks = [self.lesson.blocks[block_id] for block_id in section.block_ids]
            position = 0
            while position < len(blocks):
                end = position
                while end < len(blocks) and isinstance(blocks[end], ImageRefBlock):
                    end += 1
                if end - position > 1:  # pictures in a row share slides, never one slide each
                    self._gallery(
                        section, [b for b in blocks[position:end] if isinstance(b, ImageRefBlock)]
                    )
                    position = end
                    continue
                self._block(section, blocks[position], cover)
                position += 1
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
            slide.kind = block.kind
            slide.footer = _footer_text(block)
            slide.badge = block.provenance is Provenance.GENERATED
            if slide.badge:
                slide.notes.append(_el("p", _esc(AI_ADDED_NOTE), class_="notes__ai"))
            if first and block.teacher_note:
                slide.notes.append(
                    _note_steps("Teacher note", block.teacher_note, _fld(block.id, "teacher_note"))
                )
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
        lesson = self.lesson
        meta = [lesson_label(lesson)]
        strip = "".join(
            _el(
                "li",
                _el("span", class_="phase-strip__chip")
                + _icon(section.phase.value, "phase-strip__icon")
                + _el("span", _esc(PHASE_NAME[section.phase]), class_="phase-strip__label ui")
                + _el("span", f"{section.minutes} min", class_="phase-strip__minutes ui"),
                class_="phase-strip__item",
                data_phase=section.phase.value,
            )
            for section in lesson.sections
        )
        total = sum(section.minutes for section in lesson.sections)
        inner = _el("h1", _esc(self.title), class_="cover__title slide-title")
        inner += _el("p", _esc(" · ".join(part for part in meta if part)), class_="cover__meta ui")
        if strip:
            inner += _el("ol", strip, class_="phase-strip", aria_label="Lesson phases")
            inner += _el("p", f"{total} min", class_="cover__total ui")
        return self._add("cover", "lesson", _el("div", inner, class_="cover"))

    def _divider(self, section: Section) -> None:
        phase = section.phase
        card = _icon(phase.value, "divider__icon")
        card += _el("p", _esc(PHASE_NAME[phase]), class_="divider__phase ui")
        card += _el("h2", _esc(section.title), class_="divider__title slide-title")
        card += _el("p", f"{section.minutes} min", class_="divider__minutes ui")
        if section.minutes:
            card += _countdown(section.minutes)
        descriptions = _PHASE_DESCRIPTION_SECONDARY if self.lesson.grade > 5 else _PHASE_DESCRIPTION
        notes = [_el("p", _esc(descriptions[phase]))]
        self._add(
            "divider",
            phase.value,
            _el("div", card, class_="divider card"),
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
            case ReadingBlock():
                self._reading(section, block)
            case RuleBoxBlock():
                self._rule_box(section, block)
            case MatchBlock():
                self._match(section, block)
            case GapFillBlock():
                self._gap_fill(section, block)
            case ChoiceBlock():
                self._choice(section, block)
            case TrueFalseBlock():
                self._true_false(section, block)
            case QuestionSetBlock():
                self._question_set(section, block)
            case SoundSortBlock():
                self._sound_sort(section, block)
            case GalleryBlock():
                self._gallery_block(section, block)
            case GridBlock():
                self._grid(section, block)
            case SelfCheckBlock():
                self._self_check(section, block)

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
        inner = _note(label, content.text, _fld(block.id, "content/text"))
        if block.provenance is Provenance.GENERATED:
            inner += _el("p", _esc(AI_ADDED_NOTE), class_="notes__ai")
        if details:
            inner += _el("p", _esc(" · ".join(details)))
        if block.teacher_note:
            inner += _el(
                "p",
                _esc(block.teacher_note),
                data_multiline="true",
                **_fld(block.id, "teacher_note"),
            )
        inner += _el("p", _esc(_footer_text(block)), class_="notes__origin")
        return _el("div", inner, class_="teacher block notes__block", **_block_attrs(block))

    def _teacher_note(self, block: TeacherNoteBlock) -> str:
        """Teacher notes have no slide; they join the previous slide's notes panel."""
        label = "Teacher note"
        if block.provenance is Provenance.GENERATED:
            label += f" ({AI_ADDED_BADGE})"
        inner = _note(label, block.content.text, _fld(block.id, "content/text"))
        if block.provenance is Provenance.GENERATED:
            inner += _el("p", _esc(AI_ADDED_NOTE), class_="notes__ai")
        if block.teacher_note:
            inner += _el(
                "p",
                _esc(block.teacher_note),
                data_multiline="true",
                **_fld(block.id, "teacher_note"),
            )
        inner += _el("p", _esc(_footer_text(block)), class_="notes__origin")
        return _el("div", inner, class_="teacher block notes__block", **_block_attrs(block))

    def _wordcards(self, section: Section, block: VocabularyBlock) -> None:
        """Lower-secondary vocabulary: about four word cards to a slide, each one tap-to-reveal."""
        items = block.content.items
        pictures = [self._vocab_picture(item) for item in items]
        heights = [
            fit.wordcard_height(item.word, item.example, bool(pictures[n][0]))
            for n, item in enumerate(items)
        ]

        def cost(i: int, j: int) -> float:
            return sum(max(heights[n : n + 2]) + 28 for n in range(i, j, 2))

        ranges = fit.paginate(
            len(items), cost, budget=fit.WORDCARD_BUDGET, max_items=fit.MAX_WORDCARDS
        )
        for index, (first, last) in enumerate(ranges):
            cards = []
            notes: list[str] = []
            for number in range(first, last):
                item = items[number]
                picture, generated = pictures[number]
                path = f"content/items/{number}"
                reveal = bool(item.meaning_vi)
                inner = ""
                if picture:
                    inner += self._image(
                        picture,
                        block_id=block.id,
                        item=number,
                        alt=item.word,
                        class_="wordcard__picture",
                        generated=generated,
                    )
                    if generated:
                        inner += _el(
                            "span", _esc(AI_PICTURE_CAPTION), class_="wordcard__ai teacher"
                        )
                inner += _el(
                    "div",
                    _el(
                        "p",
                        _esc(item.word),
                        class_="wordcard__word",
                        **_fld(block.id, f"{path}/word"),
                    )
                    + _speak(item.word),
                    class_="wordcard__wordline",
                )
                if item.ipa:
                    inner += _el(
                        "p",
                        _esc(item.ipa),
                        class_="wordcard__ipa",
                        **_fld(block.id, f"{path}/ipa"),
                    )
                if reveal:
                    inner += _el(
                        "p",
                        _esc(item.meaning_vi),
                        class_="wordcard__meaning",
                        lang="vi",
                        **_fld(block.id, f"{path}/meaning_vi"),
                    )
                if item.example:
                    inner += _el(
                        "div",
                        _el(
                            "p",
                            _esc(item.example),
                            class_="wordcard__example-text",
                            **_fld(block.id, f"{path}/example"),
                        )
                        + _speak(item.example),
                        class_="wordcard__example",
                    )
                classes = ["block", "wordcard", "card"]
                if picture:
                    classes.append("has-picture")
                if reveal:
                    classes += ["tap", "reveal"]
                cards.append(
                    _el(
                        "article",
                        inner,
                        class_=" ".join(classes),
                        **_block_attrs(block),
                        data_item=str(number),
                        role="button" if reveal else None,
                        tabindex="0" if reveal else None,
                        aria_pressed="false" if reveal else None,
                    )
                )
                if item.pos:
                    notes.append(
                        _note(
                            "Part of speech",
                            f"{item.word}: {item.pos}",
                            _fld(block.id, f"{path}/pos"),
                        )
                    )
                if item.image is not None:
                    notes.append(_note("Picture", f"{item.word}: {format_citation(item.image)}"))
            hint = (
                _el("p", "Tap a card to see the meaning", class_="wordcards__hint hint ui")
                if any(item.meaning_vi for item in items[first:last])
                else ""
            )
            body = _el(
                "div",
                _el(
                    "div",
                    "".join(cards),
                    class_="wordcards" + (" wordcards--single" if last - first == 1 else ""),
                )
                + hint,
                class_="block wordcards-slide",
                **_block_attrs(block),
            )
            self._add(
                "flashcard",
                section.phase.value,
                body,
                block=block,
                first=index == 0,
                notes=notes,
            )
        if not items:
            self._vocabulary_empty(section, block)

    def _vocabulary_empty(self, section: Section, block: VocabularyBlock) -> None:
        empty = _el(
            "article",
            _el("p", "No words yet", class_="flashcard__hint hint ui"),
            class_="block flashcard card",
            **_block_attrs(block),
        )
        self._add("flashcard", section.phase.value, empty, block=block)

    def _vocabulary(self, section: Section, block: VocabularyBlock) -> None:
        if self.lesson.grade > 5:
            self._wordcards(section, block)
            return
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
                    class_="flashcard__picture",
                    generated=generated,
                )
                if generated:
                    inner += _el("span", _esc(AI_PICTURE_CAPTION), class_="flashcard__ai teacher")
            path = f"content/items/{index}"
            inner += _el(
                "div",
                _el(
                    "p", _esc(item.word), class_="flashcard__word", **_fld(block.id, f"{path}/word")
                )
                + _speak(item.word),
                class_="flashcard__wordline",
            )
            if item.ipa:
                inner += _el(
                    "p", _esc(item.ipa), class_="flashcard__ipa", **_fld(block.id, f"{path}/ipa")
                )
            if reveal:
                inner += _el(
                    "p",
                    _esc(item.meaning_vi),
                    class_="flashcard__meaning",
                    lang="vi",
                    **_fld(block.id, f"{path}/meaning_vi"),
                )
            if item.example:
                inner += _el(
                    "div",
                    _el(
                        "p",
                        _esc(item.example),
                        class_="flashcard__example-text",
                        **_fld(block.id, f"{path}/example"),
                    )
                    + _speak(item.example),
                    class_="flashcard__example",
                )
            if reveal:
                inner += _el("p", "Tap to see", class_="flashcard__hint hint ui")
            classes = ["block", "flashcard", "card"]
            if picture:
                classes.append("has-picture")
            elif not reveal:
                classes.append("bare")
            if item.example:
                classes.append("has-example")
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
                notes.append(
                    _note("Part of speech", item.pos, _fld(block.id, f"content/items/{index}/pos"))
                )
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
                _el("p", "No words yet", class_="flashcard__hint hint ui"),
                class_="block flashcard card",
                **_block_attrs(block),
            )
            self._add("flashcard", section.phase.value, empty, block=block)

    def _pattern(self, section: Section, block: SentencePatternBlock) -> None:
        content = block.content
        examples = "".join(
            _example_item(content.pattern, ex, _fld(block.id, f"content/examples/{number}"))
            for number, ex in enumerate(content.examples)
        )
        interactive = bool(content.examples)
        inner = _el(
            "div",
            _el(
                "p",
                _slot_markup(content.pattern),
                class_="pattern__text",
                data_raw=content.pattern,
                data_rebuild="slots",
                **_fld(block.id, "content/pattern"),
            )
            + _speak(_pattern_speech(content)),
            class_="pattern__head",
        )
        inner += _el("p", class_="pattern__line", aria_live="polite")
        if content.function:
            inner += _el(
                "p",
                _esc(content.function),
                class_="pattern__function",
                **_fld(block.id, "content/function"),
            )
        if interactive:
            inner += _el("ul", examples, class_="pattern__examples")
            inner += _el("p", "Tap to try", class_="pattern__hint hint ui")
        body = _el(
            "div",
            inner,
            class_=("block pattern card tap cycle" if interactive else "block pattern card")
            + (" pattern--long" if _long_pattern(content) else ""),
            **_block_attrs(block),
            role="button" if interactive else None,
            tabindex="0" if interactive else None,
        )
        notes = (
            [_note("Notes", content.notes, _fld(block.id, "content/notes"))]
            if content.notes
            else []
        )
        self._add("pattern", section.phase.value, body, block=block, notes=notes)

    def _dialogue(self, section: Section, block: DialogueBlock) -> None:
        content = block.content
        room = DIALOGUE_BUDGET - (100 if content.highlights else 0)  # the chips take a row
        parts = _dialogue_parts(content.lines, dense=False, budget=room)
        dense = len(parts) > -(-len(content.lines) // fit.MAX_DIALOGUE_LINES)
        if dense:  # the lines are long: smaller bubbles, and as few slides as that allows
            parts = _dialogue_parts(content.lines, dense=True, budget=room)
        sides: dict[str, int] = {}
        for line in content.lines:  # one side per speaker for the whole conversation
            sides.setdefault(line.speaker, len(sides))
        for index, (start, lines) in enumerate(parts):
            label = f"({index + 1}/{len(parts)})" if len(parts) > 1 else ""
            self._dialogue_slide(
                section,
                block,
                lines,
                start,
                sides,
                dense,
                label,
                index == 0,
                index == len(parts) - 1,
            )

    def _dialogue_slide(
        self,
        section: Section,
        block: DialogueBlock,
        lines: Sequence[DialogueLine],
        start: int,
        sides: Mapping[str, int],
        dense: bool,
        label: str,
        first: bool,
        last: bool = False,
    ) -> None:
        content = block.content
        bubbles = []
        for number, line in enumerate(lines):
            side = "left" if sides[line.speaker] % 2 == 0 else "right"
            avatar = _el(
                "span",
                _esc(line.speaker[:1].upper() or "?"),
                class_="bubble__avatar",
                aria_hidden="true",
            )
            index = start + number
            speaker = (
                _el(
                    "span",
                    _el(
                        "span",
                        _esc(line.speaker),
                        class_="bubble__who-name",
                        **_fld(block.id, f"content/lines/{index}/speaker"),
                    )
                    + ": ",
                    class_="bubble__who sr-only",
                )
                if line.speaker
                else ""
            )
            speech = _el(
                "p",
                speaker
                + _el(
                    "span",
                    _esc(line.text),
                    class_="bubble__line",
                    **_fld(block.id, f"content/lines/{index}/text"),
                ),
                class_="bubble__text",
            )
            bubbles.append(
                _el(
                    "div",
                    avatar + speech + _speak(line.text),
                    class_=f"bubble bubble--{side} step-item{' pending' if number else ''}",
                    data_line=str(start + number),
                    data_speaker=line.speaker,
                )
            )
        inner = ""
        if content.context or label:
            context = (
                _el(
                    "span",
                    _esc(content.context),
                    class_="dialogue__context-text",
                    **_fld(block.id, "content/context"),
                )
                if content.context
                else ""
            )
            part = _el("span", label, class_="dialogue__part ui") if label else ""
            inner += _el(
                "p",
                " ".join(piece for piece in (context, part) if piece),
                class_="dialogue__context",
            )
        inner += _el("div", "".join(bubbles), class_="chat")
        if content.highlights and last:
            chips = "".join(
                _el(
                    "li",
                    _esc(phrase),
                    class_="dialogue__chip",
                    **_fld(block.id, f"content/highlights/{number}"),
                )
                for number, phrase in enumerate(content.highlights)
            )
            inner += _el("ul", chips, class_="dialogue__highlights", aria_label="Key phrases")
        inner += _el("p", "Tap for the next line", class_="dialogue__hint hint ui")
        body = _el(
            "div",
            inner,
            class_="block dialogue tap stepper"
            + (" dialogue--dense" if dense else "")
            + ("" if len(lines) > 1 else " complete"),
            **_block_attrs(block),
            role="button",
            tabindex="0",
            aria_label="Dialogue: tap to show the next line",
        )
        notes = [_note("Context", content.context)] if content.context and first else []
        self._add("dialogue", section.phase.value, body, block=block, first=first, notes=notes)

    def _phonics(self, section: Section, block: PhonicsBlock) -> None:
        content = block.content
        words = content.words
        ranges = fit.paginate(
            len(words),
            lambda i, j: float(j - i),
            budget=fit.MAX_PHONICS_WORDS,
            max_items=fit.MAX_PHONICS_WORDS,
        ) or [(0, 0)]
        for index, (first, last) in enumerate(ranges):
            inner = _el(
                "p", _esc(content.sound), class_="phonics__sound", **_fld(block.id, "content/sound")
            )
            if content.letters:
                inner += _el(
                    "p",
                    _esc(content.letters),
                    class_="phonics__letters",
                    **_fld(block.id, "content/letters"),
                )
            if last > first:
                items = "".join(
                    _el(
                        "li",
                        _el(
                            "span",
                            _mark_letters(words[number], content.letters),
                            class_="phonics__word-text",
                            data_raw=words[number],
                            data_rebuild="marks",
                            **_fld(block.id, f"content/words/{number}"),
                        )
                        + _speak(words[number]),
                        class_="phonics__word",
                    )
                    for number in range(first, last)
                )
                inner += _el("ul", items, class_="phonics__words")
            if content.chant_lines and index == len(ranges) - 1:
                chant = "".join(
                    _el(
                        "li",
                        _esc(line),
                        class_="phonics__chant-line",
                        **_fld(block.id, f"content/chant_lines/{number}"),
                    )
                    for number, line in enumerate(content.chant_lines)
                )
                inner += _el("ul", chant, class_="phonics__chant")
            if len(ranges) > 1:
                inner += _el("span", f"({index + 1}/{len(ranges)})", class_="phonics__part ui")
            body = _el("div", inner, class_="block phonics", **_block_attrs(block))
            self._add("phonics", section.phase.value, body, block=block, first=index == 0)

    def _activity(self, section: Section, block: ActivityBlock) -> None:
        content = block.content
        steps = content.student_steps
        fixed = (
            96
            + 28
            + 80  # the card's padding and the gaps, as measured
            + (
                fit.text_rows(content.instructions, 1500, 44) * 55 + 28
                if content.instructions
                else 0
            )
        )
        heights = [fit.step_height(step) for step in steps]

        def cost(i: int, j: int) -> float:
            rows = sum(max(heights[n : n + 2]) + 28 for n in range(i, j, 2))
            return fixed + rows + (28 + 170 if i == 0 else 0)

        ranges = fit.paginate(
            len(steps), cost, budget=fit.ACTIVITY_BUDGET, max_items=fit.MAX_STEPS
        ) or [(0, 0)]
        notes = self._activity_notes(block)
        for index, (first, last) in enumerate(ranges):
            heading = _icon(content.type.value, "activity__icon") + _el(
                "h2",
                _esc(content.title),
                class_="activity__title slide-title",
                **_fld(block.id, "content/title"),
            )
            if len(ranges) > 1:
                heading += _el("span", f"({index + 1}/{len(ranges)})", class_="activity__part ui")
            inner = _el("div", heading, class_="activity__heading")
            if content.instructions:
                inner += _el(
                    "p",
                    _esc(content.instructions),
                    class_="activity__instructions",
                    **_fld(block.id, "content/instructions"),
                )
            if last > first:
                items = "".join(
                    _el(
                        "li",
                        _el("span", str(number + 1), class_="step__number", aria_hidden="true")
                        + _el(
                            "span",
                            _esc(steps[number]),
                            class_="step__text",
                            **_fld(block.id, f"content/student_steps/{number}"),
                        ),
                        class_="step card",
                    )
                    for number in range(first, last)
                )
                inner += _el(
                    "ol",
                    items,
                    class_="activity__steps"
                    + (" activity__steps--single" if last - first == 1 else ""),
                    start=str(first + 1),
                )
            if index == 0:
                inner += _countdown(content.minutes)
            body = _el("div", inner, class_="block activity", **_block_attrs(block))
            self._add(
                "activity",
                section.phase.value,
                body,
                block=block,
                first=index == 0,
                notes=notes if index == 0 else (),
            )

    def _activity_notes(self, block: ActivityBlock) -> list[str]:
        content = block.content
        notes = [
            _note("Activity", f"{_words(content.type.value)} · {content.minutes} min"),
            _note("Interaction", _words(content.interaction.value)),
        ]
        if content.skills:
            notes.append(_note("Skills", ", ".join(skill.value for skill in content.skills)))
        if content.teacher_steps:
            notes.append(
                _note_list(
                    "Teacher steps",
                    content.teacher_steps,
                    ordered=True,
                    fields=[
                        _fld(block.id, f"content/teacher_steps/{number}")
                        for number in range(len(content.teacher_steps))
                    ],
                )
            )
        if content.materials:
            notes.append(_note("Materials", ", ".join(content.materials)))
        if content.competency_tags:
            notes.append(_note("Competences", ", ".join(content.competency_tags)))
        return notes

    def _summary(self, section: Section, block: SummaryBlock) -> None:
        """The lesson's words (with pictures where available) and its pattern(s); the block's
        own bullets go to the notes, or to the slide when there is nothing else to show."""
        cards: list[tuple[str, bool]] = []
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
                        class_="summary-card__picture",
                        generated=generated,
                    )
                    if picture
                    else ""
                )
                caption = _el(
                    "figcaption",
                    _esc(item.word),
                    class_="summary-card__word",
                    **_fld(vocabulary.id, f"content/items/{index}/word"),
                )
                classes = ["summary-card", "card"]
                if not picture:
                    classes.append("summary-card--bare")
                if len(item.word) > 11:
                    classes.append("summary-card--xlong")
                elif len(item.word) > 9:
                    classes.append("summary-card--long")
                cards.append(
                    (
                        _el(
                            "figure", image + caption + _speak(item.word), class_=" ".join(classes)
                        ),
                        bool(picture),
                    )
                )
        patterns = [
            _el(
                "div",
                _el(
                    "p",
                    _slot_markup(pattern.content.pattern),
                    class_="summary__pattern-text",
                    data_raw=pattern.content.pattern,
                    data_rebuild="slots",
                    **_fld(pattern.id, "content/pattern"),
                )
                + _speak(_pattern_speech(pattern.content)),
                class_="summary__pattern",
            )
            for pattern in self.lesson.ordered_blocks()
            if isinstance(pattern, SentencePatternBlock)
        ]
        bullets = block.content.bullets
        pattern_height = 150 * len(patterns)
        per_row = fit.CARDS_PER_ROW

        def cost(i: int, j: int) -> float:
            rows = sum(
                fit.summary_row_height(any(c[1] for c in cards[n : n + per_row]))
                for n in range(i, j, per_row)
            )
            return 100 + rows + (pattern_height if j == len(cards) else 0)

        ranges = fit.paginate(
            len(cards), cost, budget=fit.SUMMARY_BUDGET, max_items=len(cards) or 1
        ) or [(0, 0)]
        for index, (first, last) in enumerate(ranges):
            title = "Let's remember!"
            if len(ranges) > 1:
                title += f" ({index + 1}/{len(ranges)})"
            inner = _el("h2", _esc(title), class_="summary__title slide-title ui")
            if cards:
                inner += _el("div", "".join(c[0] for c in cards[first:last]), class_="summary-grid")
            if last == len(cards):
                inner += "".join(patterns)
            if bullets and not cards and not patterns:
                inner += _el(
                    "ul",
                    "".join(
                        _el("li", _esc(b), **_fld(block.id, f"content/bullets/{number}"))
                        for number, b in enumerate(bullets)
                    ),
                    class_="summary__bullets",
                )
            body = _el("div", inner, class_="block summary", **_block_attrs(block))
            self._summary_slide(section, block, body, index == 0)

    def _summary_slide(self, section: Section, block: SummaryBlock, body: str, first: bool) -> None:
        bullets = block.content.bullets
        notes = (
            [
                _note_list(
                    "Summary",
                    bullets,
                    fields=[_fld(block.id, f"content/bullets/{n}") for n in range(len(bullets))],
                )
            ]
            if bullets and first
            else []
        )
        self._add("summary", section.phase.value, body, block=block, first=first, notes=notes)

    def _image_ref(self, section: Section, block: ImageRefBlock) -> None:
        content = block.content
        picture = next((uri for c in block.sources if (uri := self._picture(c)) is not None), None)
        caption = (
            _el(
                "figcaption",
                _esc(content.caption),
                class_="image-ref__caption",
                **_fld(block.id, "content/caption"),
            )
            if content.caption
            else ""
        )
        if picture:
            image = self._image(
                picture, block_id=block.id, item=0, alt=content.alt, class_="image-ref__picture"
            )
            body = _el("figure", image + caption, class_="block image-ref", **_block_attrs(block))
        else:
            missing = _el(
                "p",
                _el("span", "Picture: ", class_="ui")
                + _el("span", _esc(content.alt), **_fld(block.id, "content/alt")),
                class_="image-ref__placeholder",
            )
            body = _el("figure", missing + caption, class_="block image-ref", **_block_attrs(block))
        notes = [_note("Picture", content.alt, _fld(block.id, "content/alt"))]
        self._add("image", section.phase.value, body, block=block, notes=notes)

    def _gallery(self, section: Section, blocks: Sequence[ImageRefBlock]) -> None:
        """Consecutive pictures on shared slides: at most six to a slide, cut evenly."""
        ranges = fit.paginate(
            len(blocks), lambda i, j: 0, budget=1, max_items=fit.MAX_GALLERY_PICTURES
        )
        for index, (first, last) in enumerate(ranges):
            group = blocks[first:last]
            count = len(group)
            columns = count if count <= 3 else 2 if count == 4 else 3
            rows = -(-count // columns)
            figures = []
            for block in group:
                content = block.content
                picture = next(
                    (uri for c in block.sources if (uri := self._picture(c)) is not None), None
                )
                caption = (
                    _el(
                        "figcaption",
                        _esc(content.caption),
                        class_="gallery__caption",
                        **_fld(block.id, "content/caption"),
                    )
                    if content.caption
                    else ""
                )
                if picture:
                    shown = self._image(
                        picture,
                        block_id=block.id,
                        item=0,
                        alt=content.alt,
                        class_="gallery__picture",
                    )
                else:
                    shown = _el(
                        "p",
                        _el("span", "Picture: ", class_="ui")
                        + _el("span", _esc(content.alt), **_fld(block.id, "content/alt")),
                        class_="gallery__placeholder",
                    )
                figures.append(
                    _el("figure", shown + caption, class_="gallery__item", **_block_attrs(block))
                )
            title = (
                _el("span", f"({index + 1}/{len(ranges)})", class_="gallery__part ui")
                if len(ranges) > 1
                else ""
            )
            body = _el(
                "div",
                title
                + _el(
                    "div",
                    "".join(figures),
                    class_=f"gallery__grid gallery--cols{columns} gallery--rows{rows}",
                ),
                class_="block gallery",
                **_block_attrs(group[0]),
            )
            slide = self._add(
                "image",
                section.phase.value,
                body,
                block=group[0],
                notes=[_note("Picture", b.content.alt, _fld(b.id, "content/alt")) for b in group],
            )
            citations = [c for b in group for c in b.sources]
            parts = [", ".join(b.id for b in group)]
            if cited := _compact_sources(citations):
                parts.append("Source: " + ", ".join(cited))
            slide.footer = " · ".join(parts)
            slide.badge = any(b.provenance is Provenance.GENERATED for b in group)
            for extra in group[1:]:
                if extra.teacher_note:
                    slide.notes.append(
                        _note_steps(
                            "Teacher note", extra.teacher_note, _fld(extra.id, "teacher_note")
                        )
                    )

    # -- exercises (docs/secondary-units.md, "Interactive exercise blocks")

    def _exercise_slides(
        self,
        section: Section,
        block: Block,
        pages: Sequence[_Page],
        *,
        bar: str,
        notes: Sequence[str] = (),
    ) -> None:
        """One slide per page: the head, the page's markup, the buttons and the answer key."""
        keys = answer_pairs(block)
        number = getattr(block.content, "exercise_no", None)
        for index, page in enumerate(pages, start=1):
            part = (index, len(pages)) if len(pages) > 1 else None
            classes = (
                f"block ex ex--{block.kind}"
                + (" ex--dense" if page.dense else "")
                + (" ex--tight" if page.tight else "")
            )
            body = _el(
                "div",
                _ex_head(block, part) + page.markup + bar,
                class_=classes,
                **_block_attrs(block),
                data_part=f"{index}/{len(pages)}",
            )
            page_notes: list[str] = []
            if number and index == 1:
                page_notes.append(_note("Exercise", str(number)))
            key = [line for item, line in keys if page.start <= item < page.start + page.count]
            if key:
                page_notes.append(_note_list("Answer key", key))
            if index == 1:
                page_notes.extend(notes)
            self._add(
                "exercise",
                section.phase.value,
                body,
                block=block,
                first=index == 1,
                notes=page_notes,
                extra={"data_exercise": block.kind},
            )

    def _reading(self, section: Section, block: ReadingBlock) -> None:
        content = block.content
        # pictures printed with the paragraphs are shown beside them, not twice
        own = any(p.image is not None for p in content.paragraphs)
        picture = (
            None
            if own
            else next(
                (
                    uri
                    for c in [content.image, *block.sources]
                    if (uri := self._picture(c)) is not None
                ),
                None,
            )
        )
        glossary = {word.lower(): meaning for word, meaning in content.glossary.items()}
        paragraphs = content.paragraphs
        tip = 100 if glossary else 0  # room for the meaning that a tap on a word shows

        # a first paragraph too long for the slide beside a full-size picture gets a narrow one
        narrow = (
            picture is not None
            and bool(paragraphs)
            and fit.reading_paragraph_height(paragraphs[0].text, fit.READING_TEXT_BESIDE_PICTURE)
            + 62
            + tip
            > fit.EX_BUDGET
        )
        beside_width = fit.READING_TEXT_NARROW if narrow else fit.READING_TEXT_BESIDE_PICTURE

        def reading_cost(i: int, j: int) -> float:
            beside = picture is not None and i == 0
            width = beside_width if beside else fit.READING_TEXT_FULL
            text = sum(
                max(
                    fit.reading_paragraph_height(p.text, width - (330 if p.image else 0)),
                    240.0 if p.image else 0.0,
                )
                for p in paragraphs[i:j]
            )
            return max(text + 62 + tip, (330 if narrow else 430) if beside else 0)

        groups: list[list[ReadingParagraph]] = [
            list(paragraphs[first:last])
            for first, last in fit.paginate(
                len(paragraphs), reading_cost, budget=fit.EX_BUDGET, max_items=len(paragraphs) or 1
            )
        ]

        pages: list[_Page] = []
        start = 0
        for index, group in enumerate(groups):
            rows = "".join(
                _el(
                    "div",
                    self._paragraph_picture(block, start + number, paragraph)
                    + _el(
                        "p",
                        _highlighted(paragraph.text, paragraph.highlight, glossary),
                        class_="reading__para",
                        **_fld(block.id, f"content/paragraphs/{start + number}/text"),
                    )
                    + _speak(paragraph.text),
                    class_="reading__row",
                )
                for number, paragraph in enumerate(group)
            )
            text = rows
            if glossary:
                text += _el("p", class_="reading__tip", role="status", aria_live="polite")
            inner = ""
            if picture and index == 0:
                inner += _el(
                    "img",
                    class_="reading__picture",
                    alt=content.title or "Reading picture",
                    src=picture,
                    data_block_id=block.id,
                    data_item="0",
                )
            inner += _el("div", text, class_="reading__text")
            chars = sum(len(paragraph.text) for paragraph in group)
            pages.append(
                _Page(
                    _el(
                        "div",
                        inner,
                        class_="reading__body"
                        + (" has-picture" if picture and index == 0 else "")
                        + (" has-picture--narrow" if narrow and index == 0 else ""),
                    ),
                    start,
                    len(group),
                    chars > DENSE_CHARS + 120,
                )
            )
            start += len(group)
        notes = (
            [_note_list("Glossary", [f"{word}: {meaning}" for word, meaning in glossary.items()])]
            if glossary
            else []
        )
        self._exercise_slides(section, block, pages, bar="", notes=notes)

    def _paragraph_picture(
        self, block: ReadingBlock, number: int, paragraph: ReadingParagraph
    ) -> str:
        """The picture printed with a paragraph, shown beside it."""
        uri = self._picture(paragraph.image)
        if uri is None:
            return ""
        return self._image(
            uri,
            block_id=block.id,
            item=number,
            alt=paragraph.text[:80],
            class_="reading__para-picture",
        )

    def _rule_box(self, section: Section, block: RuleBoxBlock) -> None:
        content = block.content
        points, examples = content.points, content.examples
        frame = fit.rule_frame_height(content.title)
        heights = [fit.rule_point_height(point) for point in points] + [
            fit.rule_example_height(example) for example in examples
        ]
        split = len(points)

        def cost(i: int, j: int) -> float:
            both = i < split < j
            return frame + sum(heights[i:j]) + (24 if both else 0)

        ranges = fit.paginate(
            len(heights), cost, budget=fit.RULE_BUDGET, max_items=len(heights) or 1
        ) or [(0, 0)]
        for index, (first, last) in enumerate(ranges):
            inner = _el(
                "h2",
                _esc(content.title),
                class_="rule-box__title slide-title",
                **_fld(block.id, "content/title"),
            )
            if len(ranges) > 1:
                inner += _el("span", f"({index + 1}/{len(ranges)})", class_="rule-box__part ui")
            shown = [n for n in range(first, min(last, split))]
            if shown:
                items = "".join(
                    _el(
                        "li",
                        _esc(points[n]),
                        class_="rule-box__point",
                        **_fld(block.id, f"content/points/{n}"),
                    )
                    for n in shown
                )
                inner += _el("ul", items, class_="rule-box__points")
            samples = [n - split for n in range(max(first, split), last)]
            if samples:
                items = "".join(
                    _el(
                        "li",
                        _el(
                            "span",
                            _esc(examples[n]),
                            class_="rule-box__example-text",
                            **_fld(block.id, f"content/examples/{n}"),
                        )
                        + _speak(examples[n]),
                        class_="rule-box__example",
                    )
                    for n in samples
                )
                inner += _el("ul", items, class_="rule-box__examples")
            body = _el("aside", inner, class_="block rule-box", role="note", **_block_attrs(block))
            self._add("rule", section.phase.value, body, block=block, first=index == 0)

    def _match(self, section: Section, block: MatchBlock) -> None:
        content = block.content
        answer = content.answer
        unused = [i for i in range(len(content.right)) if i not in set(answer.values())]
        count = len(content.left)
        has_picture = [self._match_picture(block, "left", i) is not None for i in range(count)]
        left_height = [fit.match_item_height(text) for text in content.left]
        right_height = [fit.match_item_height(text) for text in content.right]

        def cost(i: int, j: int) -> float:
            wanted = [answer[n] for n in range(i, j)] + (unused if j == count else [])
            right = sum(right_height[r] for r in wanted)
            if all(has_picture[i:j]):
                return max(-(-(j - i) // 3) * 276, right)
            return max(sum(left_height[i:j]), right)

        pictured_all = count > 0 and all(has_picture)
        slices = fit.paginate(
            count,
            cost,
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_PICTURE_MATCH_ITEMS if pictured_all else fit.MAX_MATCH_ITEMS,
        )
        pages: list[_Page] = []
        for number, (first, last) in enumerate(slices):
            chunk = list(range(first, last))
            wanted = sorted(answer[i] for i in chunk)
            if number == len(slices) - 1:
                wanted = sorted([*wanted, *unused])
            local = {right: position for position, right in enumerate(wanted)}
            local_answer = {position: local[answer[i]] for position, i in enumerate(chunk)}
            left_items = "".join(
                _match_item(
                    "left",
                    position,
                    str(i + 1),
                    content.left[i],
                    _fld(block.id, f"content/left/{i}"),
                    self._match_picture(block, "left", i),
                )
                for position, i in enumerate(chunk)
            )
            right_items = "".join(
                _match_item(
                    "right",
                    position,
                    letter(position),
                    content.right[right],
                    _fld(block.id, f"content/right/{right}"),
                    self._match_picture(block, "right", right),
                )
                for position, right in enumerate(wanted)
            )
            pictured = any(has_picture[i] for i in chunk)
            key = ", ".join(
                f"{chunk[left_index] + 1}-{letter(right_index)}"
                for left_index, right_index in local_answer.items()
            )
            inner = _el(
                "div",
                _el("ol", left_items, class_="match__left", aria_label="Left column")
                + _el("ul", right_items, class_="match__right", aria_label="Right column"),
                class_="match match--pictures" if pictured else "match",
                data_answer=json.dumps(local_answer),
            )
            inner += _el("p", _esc(f"Answers: {key}"), class_="match__key")
            texts = [content.left[i] for i in chunk if not pictured] + [
                content.right[r] for r in wanted
            ]
            tight = sum(len(content.right[r]) for r in wanted) > TIGHT_MATCH_CHARS
            pages.append(_Page(inner, chunk[0], len(chunk), _dense(texts), tight))
        self._exercise_slides(section, block, pages, bar=_ex_bar(check=True))

    def _match_picture(self, block: MatchBlock, side: str, index: int) -> str | None:
        """The ``<img>`` of a matching item that has a picture, else ``None``."""
        content = block.content
        citations = content.left_images if side == "left" else content.right_images
        texts = content.left if side == "left" else content.right
        if index >= len(citations) or citations[index] is None:
            return None
        uri = self._picture(citations[index])
        if uri is None:
            return None
        return self._image(
            uri, block_id=block.id, item=index, alt=texts[index], class_="match__picture"
        )

    def _gap_fill(self, section: Section, block: GapFillBlock) -> None:
        content = block.content
        box = ""
        if content.word_box:
            chips = "".join(
                _el(
                    "li",
                    _esc(word),
                    class_="wordbox__chip",
                    data_word=word.lower(),
                    **_fld(block.id, f"content/word_box/{number}"),
                )
                for number, word in enumerate(content.word_box)
            )
            box = _el("ul", chips, class_="wordbox", aria_label="Word box")
        pages: list[_Page] = []
        box_height = fit.word_box_height(content.word_box)
        heights = [
            fit.gap_item_height(item.text, item.answers, n + 1)
            for n, item in enumerate(content.items)
        ]
        for chunk_start, chunk_end in fit.paginate(
            len(heights),
            lambda i, j: box_height + sum(heights[i:j]),
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_GAP_ITEMS,
        ):
            chunk = content.items[chunk_start:chunk_end]
            rows = "".join(
                _el(
                    "li",
                    _el("span", str(chunk_start + position + 1), class_="gap-item__no")
                    + _el(
                        "p",
                        _gap_markup(item.text, item.answers),
                        class_="gap-item__text",
                        data_raw=item.text,
                        data_rebuild="gap",
                        **_fld(block.id, f"content/items/{chunk_start + position}/text"),
                    )
                    + _gap_answers(block.id, chunk_start + position, item),
                    class_="gap-item",
                )
                for position, item in enumerate(chunk)
            )
            inner = box + _el("ol", rows, class_="gap-list")
            texts = [item.text for item in chunk] + content.word_box
            pages.append(_Page(inner, chunk_start, len(chunk), _dense(texts)))
        self._exercise_slides(section, block, pages, bar=_ex_bar())

    def _choice(self, section: Section, block: ChoiceBlock) -> None:
        pages: list[_Page] = []
        items = block.content.items
        specs = [(item.stem, item.options) for item in items]
        for chunk_start, chunk_end in fit.paginate(
            len(items),
            lambda i, j: fit.choice_layout(specs[i:j], i)[0],
            budget=fit.EX_BUDGET,
            max_items=(
                fit.MAX_CHOICE_ITEMS_FOUR_OPTIONS
                if any(len(item.options) >= 4 for item in items)
                else fit.MAX_CHOICE_ITEMS
            ),
        ):
            chunk = items[chunk_start:chunk_end]
            rows = ""
            for position, item in enumerate(chunk):
                correct = item.options.index(item.answer)
                options = "".join(
                    _el(
                        "button",
                        _el("span", letter(index), class_="choice__letter", aria_hidden="true")
                        + _el(
                            "span",
                            _esc(option),
                            class_="choice__text",
                            **_fld(
                                block.id,
                                f"content/items/{chunk_start + position}/options/{index}",
                            ),
                        ),
                        type="button",
                        class_="choice__option",
                        data_act="option",
                        data_index=str(index),
                        data_correct="true" if index == correct else None,
                    )
                    for index, option in enumerate(item.options)
                )
                rows += _el(
                    "li",
                    _el(
                        "p",
                        _el("span", str(chunk_start + position + 1), class_="choice__no")
                        + _el(
                            "span",
                            _esc(item.stem),
                            class_="choice__stem-text",
                            **_fld(block.id, f"content/items/{chunk_start + position}/stem"),
                        ),
                        class_="choice__stem",
                    )
                    + _el("div", options, class_="choice__options", role="group"),
                    class_="choice",
                    data_answer=str(correct),
                )
            two_columns = fit.choice_layout(specs[chunk_start:chunk_end], chunk_start)[1]
            columns = " choice-list--cols" if two_columns else ""
            inner = _el("ol", rows, class_="choice-list" + columns)
            texts = [t for item in chunk for t in (item.stem, *item.options)]
            pages.append(_Page(inner, chunk_start, len(chunk), _dense(texts, 260)))
        self._exercise_slides(section, block, pages, bar=_ex_bar())

    def _true_false(self, section: Section, block: TrueFalseBlock) -> None:
        pages: list[_Page] = []
        items = block.content.items
        heights = [fit.true_false_item_height(i.statement, n + 1) for n, i in enumerate(items)]
        for chunk_start, chunk_end in fit.paginate(
            len(items),
            lambda i, j: sum(heights[i:j]),
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_TF_ITEMS,
        ):
            chunk = items[chunk_start:chunk_end]
            rows = ""
            for position, item in enumerate(chunk):
                buttons = "".join(
                    _el(
                        "button",
                        label,
                        type="button",
                        class_=f"tf__btn tf__btn--{value}",
                        data_act="tf",
                        data_value=value,
                        data_correct="true" if (value == "true") == item.answer else None,
                    )
                    for value, label in (("true", TRUE), ("false", FALSE))
                )
                rows += _el(
                    "li",
                    _el(
                        "p",
                        _el("span", str(chunk_start + position + 1), class_="tf__no")
                        + _el(
                            "span",
                            _esc(item.statement),
                            class_="tf__text",
                            **_fld(block.id, f"content/items/{chunk_start + position}/statement"),
                        ),
                        class_="tf__statement",
                    )
                    + _el("div", buttons, class_="tf__btns", role="group"),
                    class_="tf",
                    data_answer="true" if item.answer else "false",
                )
            inner = _el("ol", rows, class_="tf-list")
            pages.append(
                _Page(inner, chunk_start, len(chunk), _dense([i.statement for i in chunk], 280))
            )
        self._exercise_slides(section, block, pages, bar=_ex_bar())

    def _question_set(self, section: Section, block: QuestionSetBlock) -> None:
        content = block.content
        pages: list[_Page] = []
        heights = [fit.question_height(q.question, n + 1) for n, q in enumerate(content.questions)]
        prompt_height = (
            fit.text_rows(content.prompt, fit.CONTENT_WIDTH, 32) * 40 + 28 if content.prompt else 0
        )
        for chunk_start, chunk_end in fit.paginate(
            len(heights),
            lambda i, j: prompt_height + sum(heights[i:j]),
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_QUESTIONS,
        ):
            chunk = content.questions[chunk_start:chunk_end]
            rows = ""
            for position, question in enumerate(chunk):
                number = chunk_start + position
                # A sample answer is printed with the key; an empty one is only an edit field.
                answer = _el(
                    "p",
                    _esc(question.answer),
                    class_="qset__answer" if question.answer else "qset__sample edit-only",
                    data_placeholder="Sample answer",
                    **_fld(block.id, f"content/questions/{number}/answer"),
                )
                rows += _el(
                    "li",
                    _el("span", str(number + 1), class_="qset__no")
                    + _el(
                        "p",
                        _esc(question.question),
                        class_="qset__text",
                        **_fld(block.id, f"content/questions/{number}/question"),
                    )
                    + _speak(question.question)
                    + answer,
                    class_="qset__q" + (" is-revealed" if position == 0 else ""),
                )
            inner = _el("ol", rows, class_="qset__list")
            if content.prompt:
                inner = (
                    _el(
                        "p",
                        _esc(content.prompt),
                        class_="qset__prompt",
                        **_fld(block.id, "content/prompt"),
                    )
                    + inner
                )
            texts = [q.question for q in chunk]
            pages.append(_Page(inner, chunk_start, len(chunk), _dense(texts, 260)))
        bar = _ex_bar(show="Show all", more=len(content.questions) > 1)
        self._exercise_slides(section, block, pages, bar=bar)

    def _gallery_block(self, section: Section, block: GalleryBlock) -> None:
        """The page's pictures (with their labels and captions) and the questions about them."""
        content = block.content
        images = content.images
        columns = len(images) if len(images) <= 3 else 2 if len(images) == 4 else 3
        rows = -(-len(images) // columns)
        picture_height = 300 if rows == 1 else 440
        question_heights = [
            fit.text_rows(f"{n + 1}  {q}", fit.CONTENT_WIDTH - 64) * 57 + 24
            for n, q in enumerate(content.questions)
        ]

        def cost(i: int, j: int) -> float:
            return (picture_height if i == 0 else 0) + sum(question_heights[i:j])

        ranges = fit.paginate(
            len(question_heights), cost, budget=fit.EX_BUDGET - 40, max_items=fit.MAX_QUESTIONS
        ) or [(0, 0)]
        pages: list[_Page] = []
        for index, (first, last) in enumerate(ranges):
            inner = ""
            if index == 0:
                figures = []
                for number, image in enumerate(content.images):
                    uri = self._picture(image.image)
                    path = f"content/images/{number}"
                    if uri:
                        shown = self._image(
                            uri,
                            block_id=block.id,
                            item=number,
                            alt=image.alt or image.caption or image.label,
                            class_="gal__picture",
                        )
                    else:
                        shown = _el(
                            "p",
                            _el("span", "Picture: ", class_="ui")
                            + _el("span", _esc(image.alt), **_fld(block.id, f"{path}/alt")),
                            class_="gal__placeholder",
                        )
                    label = (
                        _el(
                            "span",
                            _esc(image.label),
                            class_="gal__label",
                            **_fld(block.id, f"{path}/label"),
                        )
                        if image.label
                        else ""
                    )
                    caption = (
                        _el(
                            "figcaption",
                            _esc(image.caption),
                            class_="gal__caption",
                            **_fld(block.id, f"{path}/caption"),
                        )
                        if image.caption
                        else ""
                    )
                    figures.append(
                        _el(
                            "figure",
                            label + shown + caption,
                            class_="gal__item",
                            data_item=str(number),
                        )
                    )
                inner += _el(
                    "div",
                    "".join(figures),
                    class_=f"gal__grid gal--cols{columns} gal--rows{rows}",
                )
            if last > first:
                items = "".join(
                    _el(
                        "li",
                        _el("span", str(number + 1), class_="gal__no")
                        + _el(
                            "p",
                            _esc(content.questions[number]),
                            class_="gal__question",
                            **_fld(block.id, f"content/questions/{number}"),
                        )
                        + _speak(content.questions[number]),
                        class_="gal__q",
                    )
                    for number in range(first, last)
                )
                inner += _el("ol", items, class_="gal__questions")
            pages.append(_Page(inner, first, last - first))
        notes = [
            _note("Picture", f"{image.label + ': ' if image.label else ''}{image.alt}")
            for image in images
            if image.alt
        ]
        self._exercise_slides(section, block, pages, bar="", notes=notes)

    def _grid(self, section: Section, block: GridBlock) -> None:
        """A table the pupils fill in: editable cells, empty rows to write in, blanks to reveal."""
        content = block.content
        columns = len(content.header)
        rows = [list(row) for row in content.rows] + [[""] * columns] * content.blank_rows
        # a column is as wide as its texts suggest (a blank counts as its answer's chip), never
        # narrower than its longest word, so a long question beside empty columns gets the room
        answers = iter(content.answers)
        shown = [
            [_SLOT.sub(lambda _m: _chip_text(next(answers, "")), cell) for cell in row]
            for row in rows
        ]
        weights = [
            max(
                8.0, min(60.0, max([len(content.header[c]) * 1.5] + [len(row[c]) for row in shown]))
            )
            for c in range(columns)
        ]
        total = fit.CONTENT_WIDTH - 8
        floors = [
            max(
                len(word)
                for text in [content.header[c], *(row[c] for row in shown)]
                for word in (text.split() or [""])
            )
            * 24
            + 44
            for c in range(columns)
        ]
        sized = [max(weights[c] / sum(weights) * total, floors[c]) for c in range(columns)]
        shares = [width / sum(sized) for width in sized]
        widths = [share * total - 40 for share in shares]
        rows_text = shown
        heights = [
            max(
                64.0,
                max(fit.text_rows(cell, widths[c], 40) for c, cell in enumerate(row)) * 50 + 24,
            )
            for row in rows_text
        ]
        head_height = (
            max(fit.text_rows(h, widths[c], 40, bold=True) for c, h in enumerate(content.header))
            * 50
            + 30
        )
        blanks_before: list[int] = []
        seen = 0
        for row in rows:
            blanks_before.append(seen)
            seen += sum(len(_SLOT.findall(cell)) for cell in row)
        pages: list[_Page] = []
        for first, last in fit.paginate(
            len(rows),
            lambda i, j: head_height + sum(heights[i:j]),
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_GRID_ROWS,
        ):
            head = "".join(
                _el(
                    "th",
                    _el("span", _esc(text), **_fld(block.id, f"content/header/{n}")),
                    class_="grid__head",
                    scope="col",
                )
                for n, text in enumerate(content.header)
            )
            body = ""
            for number in range(first, last):
                cells = ""
                blank = blanks_before[number]
                for column, text in enumerate(rows[number]):
                    if number < len(content.rows) and _SLOT.search(text):
                        count = len(_SLOT.findall(text))
                        cells += _el(
                            "td",
                            _el(
                                "span",
                                _gap_markup(text, content.answers[blank : blank + count]),
                                class_="grid__text",
                            ),
                            class_="grid__cell",
                        )
                        blank += count
                    elif number < len(content.rows):
                        cells += _el(
                            "td",
                            _el(
                                "span",
                                _esc(text),
                                class_="grid__text",
                                data_placeholder="Write here",
                                **_fld(block.id, f"content/rows/{number}/{column}"),
                            ),
                            class_="grid__cell",
                        )
                    else:
                        cells += _el("td", _el("span", class_="grid__text"), class_="grid__cell")
                body += _el("tr", cells, class_="grid__row")
            cols = _el(
                "colgroup",
                "".join(_el("col", style=f"width:{share * 100:.1f}%") for share in shares),
            )
            table = _el(
                "table",
                cols + _el("thead", _el("tr", head)) + _el("tbody", body),
                class_="grid__table",
            )
            page_blanks = (blanks_before[last] if last < len(rows) else seen) - blanks_before[first]
            pages.append(_Page(table, blanks_before[first], page_blanks))
        bar = _ex_bar() if content.answers else ""
        self._exercise_slides(section, block, pages, bar=bar)

    def _self_check(self, section: Section, block: SelfCheckBlock) -> None:
        """The *Now I can* list: one line per item, tap one, two or three ticks."""
        content = block.content
        heights = [
            fit.text_rows(f"{n + 1}  {item}", 1150) * 57 + 36
            for n, item in enumerate(content.items)
        ]
        ranges = fit.paginate(
            len(heights),
            lambda i, j: sum(heights[i:j]) + 120,
            budget=fit.EX_BUDGET + 60,
            max_items=fit.MAX_SELF_CHECK_ITEMS,
        )
        for index, (first, last) in enumerate(ranges):
            rows = ""
            for number in range(first, last):
                buttons = "".join(
                    _el(
                        "button",
                        _esc(mark),
                        type="button",
                        class_="sc__btn",
                        data_level=str(level + 1),
                        aria_pressed="false",
                        aria_label=f"{mark}: {content.items[number]}",
                    )
                    for level, mark in enumerate(content.scale)
                )
                rows += _el(
                    "li",
                    _el(
                        "p",
                        _el("span", str(number + 1), class_="sc__no")
                        + _el(
                            "span",
                            _esc(content.items[number]),
                            class_="sc__text",
                            **_fld(block.id, f"content/items/{number}"),
                        ),
                        class_="sc__item",
                    )
                    + _el("div", buttons, class_="sc__btns", role="group"),
                    class_="sc__row",
                )
            head = _el(
                "h2",
                _esc(content.title),
                class_="sc__title slide-title",
                **_fld(block.id, "content/title"),
            )
            if len(ranges) > 1:
                head += _el("span", f"({index + 1}/{len(ranges)})", class_="sc__part ui")
            inner = _el("header", head, class_="sc__head") + _el("ol", rows, class_="sc__list")
            if content.instruction:
                inner += _el(
                    "p",
                    _esc(content.instruction),
                    class_="sc__sub",
                    **_fld(block.id, "content/instruction"),
                )
            body = _el("div", inner, class_="block sc", **_block_attrs(block))
            self._add(
                "exercise",
                section.phase.value,
                body,
                block=block,
                first=index == 0,
                extra={"data_exercise": block.kind},
            )

    def _sound_sort(self, section: Section, block: SoundSortBlock) -> None:
        content = block.content
        sounds = content.sounds
        pages: list[_Page] = []
        sorted_sounds = len({word.sound for word in content.words})
        which = [sounds.index(word.sound) for word in content.words]
        words = [word.word for word in content.words]
        for number, (first, last) in enumerate(
            fit.paginate(
                len(words),
                lambda i, j: fit.sort_words_height(words[i:j], sounds, which[i:j]),
                budget=fit.EX_BUDGET,
                max_items=fit.MAX_SORT_WORDS,
            )
        ):
            # the key lists the sounds, so the first slide of a long list carries it whole
            pages.append(
                _Page(_sort_words(content, first, last), 0, sorted_sounds if number == 0 else 0)
            )
        legend_height = 100
        heights = [fit.sentence_height(x.text, n + 1) for n, x in enumerate(content.sentences)]
        for start, end in fit.paginate(
            len(heights),
            lambda i, j: legend_height + sum(heights[i:j]),
            budget=fit.EX_BUDGET,
            max_items=fit.MAX_SORT_SENTENCES,
        ):
            chunk = content.sentences[start:end]
            rows = "".join(
                _sort_sentence(content, start + position, sentence)
                for position, sentence in enumerate(chunk)
            )
            marks = _sort_marks(instruction_of(block) or "", sounds)
            legend = _el(
                "ul",
                "".join(
                    _el(
                        "li",
                        _el(
                            "span",
                            _esc(slashed(sound)),
                            class_="sort__target is-sample",
                            data_s=str(n),
                            data_m=marks[n],
                        )
                        + _el("span", _esc(marks[n]), class_="sort__how"),
                        class_="sort__key",
                    )
                    for n, sound in enumerate(sounds)
                ),
                class_="sort__legend",
                aria_label="Sounds",
            )
            inner = _el(
                "div",
                legend + _el("ol", rows, class_="sort__sentences"),
                class_="sort sort--sentences",
            )
            texts = [sentence.text for sentence in chunk]
            pages.append(_Page(inner, sorted_sounds + start, len(chunk), _dense(texts, 260)))
        self._exercise_slides(section, block, pages, bar=_ex_bar(check=True))


# --- exercise fragments ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    """One slide's worth of an exercise: its markup and the items (by index) it holds."""

    markup: str
    start: int
    count: int
    dense: bool = False
    tight: bool = False


def _bubble_height(text: str, *, dense: bool) -> int:
    return fit.bubble_height(text, dense=dense)


def _dialogue_height(lines: Sequence[DialogueLine], *, dense: bool) -> int:
    return sum(_bubble_height(line.text, dense=dense) for line in lines)


def _dialogue_parts(
    lines: Sequence[DialogueLine], *, dense: bool, budget: float = DIALOGUE_BUDGET
) -> list[tuple[int, list[DialogueLine]]]:
    """The lines cut into slides that fit: ``(index of the first line, lines)`` per slide.

    A slide holds at most five lines and ``DIALOGUE_BUDGET`` pixels of bubbles. The cut is made
    once with the full budget to find how many slides are needed, then again with an even share
    of the height and of the lines so that the last slide is not a single orphan line.
    """

    def cut(budget: float, most: int) -> list[tuple[int, list[DialogueLine]]]:
        parts: list[tuple[int, list[DialogueLine]]] = []
        used = 0
        for index, line in enumerate(lines):
            height = _bubble_height(line.text, dense=dense)
            if not parts or used + height > budget or len(parts[-1][1]) >= most:
                parts.append((index, []))
                used = 0
            parts[-1][1].append(line)
            used += height
        return parts

    parts = cut(budget, fit.MAX_DIALOGUE_LINES)
    if len(parts) > 1:
        heights = [_bubble_height(line.text, dense=dense) for line in lines]
        even = cut(
            min(budget, max(sum(heights) / len(parts) * 1.15, max(heights))),
            -(-len(lines) // len(parts)),
        )
        if len(even) == len(parts):
            return even
    return parts


def _long_pattern(content: SentencePatternContent) -> bool:
    """A pattern with many or long examples: the examples are set in two smaller columns."""
    return sum(len(e) for e in content.examples) > 150 or len(content.pattern) > 40


def _dense(texts: Sequence[str], limit: int = DENSE_CHARS) -> bool:
    return sum(len(text) for text in texts) > limit


def _ex_head(block: Block, part: tuple[int, int] | None) -> str:
    """The exercise number, the textbook's instruction line and, when split, ``(1/2)``."""
    content = block.content
    instruction = instruction_of(block)
    number = getattr(content, "exercise_no", None)
    title = getattr(content, "title", "")
    heading = (title or instruction or exercise_title(block) or "") if title else instruction
    heading = heading or exercise_title(block) or ""
    # The heading is the title, else the instruction; a generated label ("Match 3") is not text.
    path = "content/title" if title else "content/instruction" if instruction else None
    inner = (
        _el("span", _esc(number), class_="ex__no", **_fld(block.id, "content/exercise_no"))
        if number
        else ""
    )
    inner += _el(
        "h2",
        _esc(heading),
        class_="ex__instruction slide-title",
        **(_fld(block.id, path) if path else {}),
    )
    if part:
        inner += _el("span", f"({part[0]}/{part[1]})", class_="ex__part ui")
    head = _el("header", inner, class_="ex__head")
    if title and instruction:
        head += _el(
            "p", _esc(instruction), class_="ex__sub", **_fld(block.id, "content/instruction")
        )
    return head


def _ex_button(label: str, act: str, extra: str) -> str:
    return _el("button", _esc(label), type="button", class_=f"ex__btn {extra} ui", data_act=act)


def _ex_bar(*, check: bool = False, show: str | None = "Show answers", more: bool = False) -> str:
    """The buttons under an exercise: Check (matching only), Show answers, Try again."""
    buttons = []
    if more:
        buttons.append(_ex_button("Next question", "next", "ex__next"))
    if check:
        buttons.append(_ex_button("Check", "check", "ex__check"))
    if show:
        buttons.append(_ex_button(show, "show", "ex__show"))
    buttons.append(_ex_button("Try again", "reset", "ex__reset"))
    buttons.append(_el("span", class_="ex__score ui", role="status", aria_live="polite"))
    return _el("div", "".join(buttons), class_="ex__bar ui")


def _sort_words(content: SoundSortContent, first: int = 0, last: int | None = None) -> str:
    """The word chips (each with its speaker button) and one column per sound to sort them into;
    ``first`` and ``last`` pick the words of a long list that this slide shows."""
    sounds = content.sounds
    last = len(content.words) if last is None else last
    answer = {i: sounds.index(content.words[i].sound) for i in range(first, last)}
    chips = "".join(
        _el(
            "li",
            _el(
                "button",
                _esc(word.word),
                type="button",
                class_="sort__chip",
                data_act="chip",
                aria_pressed="false",
                draggable="true",
            )
            + _speak(word.word),
            class_="sort__item",
            data_index=str(i),
        )
        for i, word in enumerate(content.words)
        if first <= i < last
    )
    columns = "".join(
        _el(
            "div",
            _el(
                "button",
                _esc(slashed(sound)),
                type="button",
                class_="sort__head",
                data_act="column",
                data_s=str(n),
            )
            + _el(
                "ul", class_="sort__drop", data_s=str(n), aria_label=f"Words with {slashed(sound)}"
            ),
            class_="sort__col",
        )
        for n, sound in enumerate(sounds)
    )
    return _el(
        "div",
        _el("ul", chips, class_="sort__pool", aria_label="Words")
        + _el("div", columns, class_="sort__cols"),
        class_="sort sort--words",
        data_answer=json.dumps(answer),
    )


_SORT_MARKS = ("underline", "circle", "tick")
_LENGTH_MARK = chr(0x2D0)  # the IPA long mark; instructions often type a colon instead


def _sort_marks(instruction: str, sounds: Sequence[str]) -> list[str]:
    """How each sound's words are marked: as the instruction says, else underline, circle, tick.

    "Underline the bold words with /æ/, circle ... /e/" gives the verb nearest before each sound.
    """
    found: list[str] = []
    text = instruction.replace(_LENGTH_MARK, ":")
    for sound in sounds:
        at = text.find(slashed(sound).replace(_LENGTH_MARK, ":"))
        verbs = re.findall(r"\b(underline|circle|tick)\b", text[: max(at, 0)], re.I)
        found.append(verbs[-1].lower() if at >= 0 and verbs else "")
    if all(found) and len(set(found)) == len(found):
        return found
    return [_SORT_MARKS[n] if n < len(_SORT_MARKS) else "mark" for n in range(len(sounds))]


def _target_spans(sentence: SoundSentence, sounds: Sequence[str]) -> list[tuple[int, int, int]]:
    """``(start, end, sound index)`` of each target word in the sentence, in text order."""
    found: list[tuple[int, int, int]] = []
    for target in sentence.targets:
        pattern = rf"(?<![\w'-]){re.escape(target.word)}(?![\w'-])"
        for match in re.finditer(pattern, sentence.text, re.IGNORECASE):
            if all(match.end() <= a or match.start() >= b for a, b, _n in found):
                found.append((match.start(), match.end(), sounds.index(target.sound)))
                break
    return sorted(found)


def _sort_sentence(content: SoundSortContent, number: int, sentence: SoundSentence) -> str:
    """A sentence whose target words are chips: a tap marks one with the next sound."""
    pieces: list[str] = []
    position = 0
    for start, end, sound in _target_spans(sentence, content.sounds):
        pieces.append(_esc(sentence.text[position:start]))
        pieces.append(
            _el(
                "button",
                _esc(sentence.text[start:end]),
                type="button",
                class_="sort__target",
                data_act="target",
                data_answer=str(sound),
            )
        )
        position = end
    pieces.append(_esc(sentence.text[position:]))
    return _el(
        "li",
        _el("span", str(number + 1), class_="sort__no")
        + _el("p", "".join(pieces), class_="sort__sentence")
        + _speak(sentence.text),
        class_="sort__row",
    )


def _match_item(
    side: str,
    index: int,
    label: str,
    text: str,
    field: Mapping[str, str | None],
    picture: str | None = None,
) -> str:
    """One tappable item of a matching exercise; a picture item shows its picture and keeps the
    description as the (screen-reader) text."""
    body = (
        picture + _el("span", _esc(text), class_="match__text sr-only", **field)
        if picture
        else _el("span", _esc(text), class_="match__text", **field)
    )
    button = _el(
        "button",
        _el("span", _esc(label), class_="match__label", aria_hidden="true")
        + body
        + _el("span", class_="match__tag", aria_hidden="true"),
        type="button",
        class_=f"match__item match__item--{side}" + (" match__item--picture" if picture else ""),
        data_act="pick",
        data_side=side,
        data_index=str(index),
        aria_pressed="false",
    )
    return _el("li", button)


def _gap_answers(block_id: str, number: int, item: GapItem) -> str:
    """The answers of one gap sentence as editable chips; shown only while editing the deck."""
    path = f"content/items/{number}/answer"
    single = isinstance(item.answer, str)
    chips = "".join(
        _el(
            "span",
            _esc(answer),
            class_="gap-item__answer",
            **_fld(block_id, path if single else f"{path}/{position}"),
        )
        for position, answer in enumerate(item.answers)
    )
    label = _el("span", "Answers:", class_="gap-item__answers-label ui")
    return _el("span", label + chips, class_="gap-item__answers edit-only")


def _chip_text(answer: str) -> str:
    """Stand-in text as wide as a revealed blank (its answer in a bordered chip)."""
    return "x" * max(3, round(len(answer) * 1.25) + 3)


def _gap_markup(text: str, answers: Sequence[str]) -> str:
    """The sentence with each ``___`` as a tap-to-reveal chip that holds its answer."""
    pieces: list[str] = []
    blank = 0
    for part in _SLOT.split(text):
        if not part:
            continue
        if not part.startswith("__"):
            pieces.append(_esc(part))
            continue
        answer = answers[blank] if blank < len(answers) else ""
        pieces.append(
            _el(
                "button",
                _el("span", _esc(answer), class_="gap__answer"),
                type="button",
                class_="gap",
                data_act="gap",
                data_answer=answer,
                aria_label=f"Blank {blank + 1}: tap to show the answer",
                style=f"--len:{max(len(answer), 3)}",
            )
        )
        blank += 1
    return "".join(pieces)


def _highlighted(text: str, highlight: Sequence[str], glossary: Mapping[str, str]) -> str:
    """The paragraph with highlighted words marked up; a word with a glossary entry is a button
    that shows its meaning. Glossary words that occur in the text are highlighted too."""
    lowered = text.lower()
    words = {word.lower(): word for word in highlight if word.strip()}
    words.update({word: word for word in glossary if word and word in lowered})
    if not words:
        return _esc(text)
    alternatives = "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))
    pattern = re.compile(rf"(?<!\w)({alternatives})(?!\w)", re.IGNORECASE)
    pieces: list[str] = []
    position = 0
    for found in pattern.finditer(text):
        pieces.append(_esc(text[position : found.start()]))
        word = found.group(1)
        meaning = glossary.get(word.lower())
        if meaning:
            pieces.append(
                _el(
                    "button",
                    _esc(word),
                    type="button",
                    class_="reading__word",
                    data_act="word",
                    data_meaning=meaning,
                    aria_pressed="false",
                )
            )
        else:
            pieces.append(_el("mark", _esc(word), class_="reading__word"))
        position = found.end()
    pieces.append(_esc(text[position:]))
    return "".join(pieces)


def _data_uri(data: bytes) -> str | None:
    """``data:image/...;base64,…`` for PNG, JPEG, GIF or WebP bytes; ``None`` otherwise."""
    media_type = next((mime for magic, mime in _IMAGE_SIGNATURES if data.startswith(magic)), None)
    if media_type is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        media_type = "image/webp"
    if media_type is None:
        return None
    return f"data:{media_type};base64,{base64.b64encode(data).decode('ascii')}"


# --- behaviour ---------------------------------------------------------------------------------

_DECK_SCRIPT = r"""
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

  // --- auto-fit: every slide body is measured against its own box (the safe area between the band,
  // the footer, the dots and the pencil) and, while something sticks out, the content blocks are
  // scaled down in 0.05 steps (--fit, applied with `zoom` by the structure layer) to 0.75 at most.
  // The page-side checker (tools/deck_check.py) holds the result to the same box.
  var FIT_STEPS = [1, 0.95, 0.9, 0.85, 0.8, 0.75];
  var fitTimer = null;

  function stageScale() {
    return stage.getBoundingClientRect().width / WIDTH || 1;
  }

  function ownText(el) {
    for (var n = 0; n < el.childNodes.length; n++) {
      var node = el.childNodes[n];
      if (node.nodeType === 3 && node.nodeValue.trim()) return true;
    }
    return false;
  }

  function overflows(slide) {
    var area = slide.querySelector('.slide-body');
    if (!area) return false;
    var box = area.getBoundingClientRect();
    var frame = slide.getBoundingClientRect();
    var tol = 0.75 * stageScale();
    // the pencil bar (editing), the source footer and the badge (teacher) must stay clear too
    var chrome = [];
    var parts = [document.getElementById('edit-toggle'), document.getElementById('edit-bar'),
      document.getElementById('edit-save')];
    each(slide.querySelectorAll('.footer, .badge'), function (el) { parts.push(el); });
    parts.forEach(function (el) {
      if (el && el.getClientRects().length && window.getComputedStyle(el).display !== 'none') {
        chrome.push(el.getBoundingClientRect());
      }
    });
    var stack = Array.prototype.map.call(area.children, function (child) {
      return { el: child, loose: false };
    });
    while (stack.length) {
      var item = stack.pop();
      var el = item.el;
      if (el.classList.contains('sr-only') || el.classList.contains('deco')) continue;
      var style = window.getComputedStyle(el);
      if (style.display === 'none' || style.opacity === '0') continue;
      if (el.getClientRects().length) {
        var r = el.getBoundingClientRect();
        // a tilted sticker or icon may poke out a little: its rotated box is not its layout
        var slack = style.transform !== 'none' ? 12 * stageScale() : tol;
        // an element placed on purpose (a watch in the corner) may bleed out of the body
        var floating = item.loose || style.position === 'absolute' || style.position === 'fixed';
        item.loose = floating;
        // a floating decoration may leave the body, not the stage
        var home = floating ? frame : box;
        var out = r.left < home.left - slack || r.right > home.right + slack
          || r.top < home.top - slack || r.bottom > home.bottom + slack;
        if (r.width > 0 && r.height > 0 && out) return true;
        if (r.width > 0 && r.height > 0 && (ownText(el) || el.tagName === 'IMG'
            || el.tagName === 'BUTTON')) {
          for (var c = 0; c < chrome.length; c++) {
            if (r.left < chrome[c].right - tol && r.right > chrome[c].left + tol
                && r.top < chrome[c].bottom - tol && r.bottom > chrome[c].top + tol) return true;
          }
        }
        if (el.clientWidth > 0 && el.clientHeight > 0) {
          var hides = style.overflowX !== 'visible' || style.overflowY !== 'visible';
          var wide = el.scrollWidth - el.clientWidth > 1;
          var tall = el.scrollHeight - el.clientHeight > 1;
          if (hides && (wide || tall)) return true;
          if (ownText(el)) {
            var range = document.createRange();
            range.selectNodeContents(el);
            var t = range.getBoundingClientRect();
            var room = 20 * stageScale();  // a trailing space may hang out of a pre-wrapped field
            if (t.right > r.right + room || t.left < r.left - room) return true;
          }
        }
      }
      for (var n = 0; n < el.children.length; n++) {
        stack.push({ el: el.children[n], loose: item.loose });
      }
    }
    return false;
  }

  function fitSlide(slide) {
    if (!slide || slide.classList.contains('teacher')) return;
    var step = 0;
    for (; step < FIT_STEPS.length; step++) {
      slide.style.setProperty('--fit', String(FIT_STEPS[step]));
      if (!overflows(slide)) return;
    }
    slide.style.setProperty('--fit', String(FIT_STEPS[FIT_STEPS.length - 1]));
  }

  function refit() {
    if (fitTimer) { clearTimeout(fitTimer); fitTimer = null; }
    body.removeAttribute('data-fit-pending');
    fitSlide(shown);
  }

  function refitSoon() {
    if (fitTimer) clearTimeout(fitTimer);
    body.setAttribute('data-fit-pending', '');
    fitTimer = setTimeout(refit, 150);
  }

  function renderDots(list, index) {
    while (dots.firstChild) dots.removeChild(dots.firstChild);
    // a long deck keeps its dots in two rows, clear of the slide body
    dots.classList.toggle('dots--many', list.length > 40);
    dots.classList.toggle('dots--crowded', list.length > 78);
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
    cancelSpeech();
    var entering = list[index] !== shown;
    shown = list[index];
    shown.classList.add('active');
    if (entering) resetExercises(shown);
    refit();
    notifyParent(index, list.length);
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
    var examples = el.querySelectorAll('.pattern__example');
    var slots = el.querySelectorAll('.pattern__slot');
    var line = el.querySelector('.pattern__line');
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

  // --- exercises: every control is a <button data-act>, so Tab / Enter / Space work as they do
  // for touch; each exercise keeps its state in classes and attributes and resets on re-entry.
  function each(list, fn) { Array.prototype.forEach.call(list, fn); }

  function setPressed(el, on) {
    el.classList.toggle('is-picked', on);
    el.setAttribute('aria-pressed', on ? 'true' : 'false');
  }

  function score(root, text) {
    var out = root.querySelector('.ex__score');
    if (out) out.textContent = text || '';
  }

  function matchState(match) {
    return JSON.parse(match.getAttribute('data-answer') || '{}');
  }

  function matchItem(match, side, index) {
    return match.querySelector(
      '.match__item[data-side="' + side + '"][data-index="' + index + '"]');
  }

  function matchClear(match) {
    each(match.querySelectorAll('.match__item'), function (item) {
      item.classList.remove('is-correct', 'is-wrong', 'is-revealed');
    });
  }

  function matchPair(match, left, right) {
    var used = {};
    each(match.querySelectorAll('.match__item--left[data-pair]'), function (item) {
      used[item.getAttribute('data-pair')] = true;
    });
    var number = 1;
    while (used[String(number)]) number++;
    [left, right].forEach(function (item) {
      item.setAttribute('data-pair', String(number));
      item.setAttribute('data-partner', item.getAttribute('data-side') === 'left'
        ? right.getAttribute('data-index') : left.getAttribute('data-index'));
      item.querySelector('.match__tag').textContent = String(number);
      setPressed(item, false);
    });
  }

  function matchUnpair(match, item) {
    var side = item.getAttribute('data-side') === 'left' ? 'right' : 'left';
    var other = matchItem(match, side, item.getAttribute('data-partner'));
    [item, other].forEach(function (one) {
      if (!one) return;
      one.removeAttribute('data-pair');
      one.removeAttribute('data-partner');
      one.querySelector('.match__tag').textContent = '';
    });
  }

  function matchPick(root, item) {
    var match = item.closest('.match');
    matchClear(match);
    score(root, '');
    if (item.hasAttribute('data-partner')) { matchUnpair(match, item); return; }
    var picked = match.querySelector('.match__item.is-picked');
    var side = item.getAttribute('data-side');
    if (picked && picked.getAttribute('data-side') !== side) {
      matchPair(match, side === 'right' ? picked : item, side === 'right' ? item : picked);
      return;
    }
    if (picked) setPressed(picked, false);
    if (picked !== item) setPressed(item, true);
  }

  function matchCheck(root) {
    var match = root.querySelector('.match');
    var answer = matchState(match);
    var left = match.querySelectorAll('.match__item--left');
    var right = 0;
    matchClear(match);
    each(left, function (item) {
      if (!item.hasAttribute('data-partner')) return;
      var wanted = String(answer[item.getAttribute('data-index')]);
      var ok = wanted === item.getAttribute('data-partner');
      var partner = matchItem(match, 'right', item.getAttribute('data-partner'));
      [item, partner].forEach(function (one) {
        one.classList.add(ok ? 'is-correct' : 'is-wrong');
      });
      if (ok) right++;
    });
    score(root, right === left.length ? 'Well done! ' + right + ' of ' + left.length
      : right + ' of ' + left.length + ' correct');
  }

  function matchShow(root) {
    var match = root.querySelector('.match');
    var answer = matchState(match);
    each(match.querySelectorAll('.match__item[data-pair]'), function (item) {
      if (item.getAttribute('data-side') === 'left') matchUnpair(match, item);
    });
    matchClear(match);
    each(match.querySelectorAll('.match__item--left'), function (item) {
      var index = item.getAttribute('data-index');
      var partner = matchItem(match, 'right', answer[index]);
      if (!partner) return;
      if (partner.hasAttribute('data-partner')) matchUnpair(match, partner);
      matchPair(match, item, partner);
      [item, partner].forEach(function (one) { one.classList.add('is-correct', 'is-revealed'); });
    });
    score(root, '');
  }

  // --- sound sorting: chips move between a pool and one column per sound; a sentence's target
  // words cycle through the sounds on tap.
  function sortAnswer(root) {
    var sort = root.querySelector('.sort');
    return JSON.parse((sort && sort.getAttribute('data-answer')) || '{}');
  }

  function sortClear(root) {
    each(root.querySelectorAll('.sort__chip, .sort__target'), function (el) {
      el.classList.remove('is-correct', 'is-wrong', 'is-revealed');
    });
  }

  function sortChip(root, chip) {
    var item = chip.closest('.sort__item');
    var pool = root.querySelector('.sort__pool');
    var picked = root.querySelector('.sort__chip.is-picked');
    sortClear(root);
    score(root, '');
    if (item.parentNode !== pool) { pool.appendChild(item); return; }
    if (picked) setPressed(picked, false);
    if (picked !== chip) setPressed(chip, true);
  }

  function sortColumn(root, head) {
    var picked = root.querySelector('.sort__chip.is-picked');
    if (!picked) return;
    sortClear(root);
    score(root, '');
    setPressed(picked, false);
    root.querySelector('.sort__drop[data-s="' + head.getAttribute('data-s') + '"]')
      .appendChild(picked.closest('.sort__item'));
  }

  function sortTarget(root, target) {
    var sounds = root.querySelectorAll('.sort__key').length;
    var now = target.hasAttribute('data-s') ? parseInt(target.getAttribute('data-s'), 10) : -1;
    sortClear(root);
    score(root, '');
    if (now + 1 >= sounds) sortMark(root, target, null);
    else sortMark(root, target, String(now + 1));
  }

  // a target word takes the sound index (data-s) and that sound's mark from the key (data-m)
  function sortMark(root, target, s) {
    if (s === null) { target.removeAttribute('data-s'); target.removeAttribute('data-m'); return; }
    var key = root.querySelector('.sort__key .sort__target[data-s="' + s + '"]');
    target.setAttribute('data-s', s);
    target.setAttribute('data-m', key ? key.getAttribute('data-m') : '');
  }

  function sortCheck(root) {
    var answer = sortAnswer(root);
    var total = 0, right = 0;
    sortClear(root);
    each(root.querySelectorAll('.sort__item'), function (item) {
      var drop = item.parentNode;
      total++;
      if (!drop.hasAttribute('data-s')) return;
      var ok = String(answer[item.getAttribute('data-index')]) === drop.getAttribute('data-s');
      item.querySelector('.sort__chip').classList.add(ok ? 'is-correct' : 'is-wrong');
      if (ok) right++;
    });
    each(root.querySelectorAll('.sort__sentence .sort__target'), function (target) {
      total++;
      if (!target.hasAttribute('data-s')) return;
      var ok = target.getAttribute('data-s') === target.getAttribute('data-answer');
      target.classList.add(ok ? 'is-correct' : 'is-wrong');
      if (ok) right++;
    });
    score(root, right === total ? 'Well done! ' + right + ' of ' + total
      : right + ' of ' + total + ' correct');
  }

  function sortShow(root) {
    var answer = sortAnswer(root);
    sortClear(root);
    each(root.querySelectorAll('.sort__item'), function (item) {
      var drop = root.querySelector(
        '.sort__drop[data-s="' + answer[item.getAttribute('data-index')] + '"]');
      if (!drop) return;
      drop.appendChild(item);
      item.querySelector('.sort__chip').classList.add('is-revealed');
    });
    each(root.querySelectorAll('.sort__sentence .sort__target'), function (target) {
      sortMark(root, target, target.getAttribute('data-answer'));
      target.classList.add('is-revealed');
    });
    score(root, '');
  }

  function sortReset(root) {
    var pool = root.querySelector('.sort__pool');
    if (pool) {
      var items = Array.prototype.slice.call(root.querySelectorAll('.sort__item'));
      items.sort(function (a, b) {
        return parseInt(a.getAttribute('data-index'), 10)
          - parseInt(b.getAttribute('data-index'), 10);
      });
      items.forEach(function (item) { pool.appendChild(item); });
    }
    each(root.querySelectorAll('.sort__sentence .sort__target'), function (target) {
      sortMark(root, target, null);
    });
  }

  function gapSet(root, gap, on) {
    var word = (gap.getAttribute('data-answer') || '').toLowerCase();
    var chip = null;
    gap.classList.toggle('is-revealed', on);
    gap.setAttribute('aria-label', on ? 'Blank: ' + gap.getAttribute('data-answer')
      : 'Blank: tap to show the answer');
    each(root.querySelectorAll('.wordbox__chip'), function (c) {
      var free = c.classList.contains('is-revealed') !== on;
      if (!chip && free && c.getAttribute('data-word') === word) chip = c;
    });
    if (chip) chip.classList.toggle('is-revealed', on);
  }

  function choose(root, button, kind) {
    var item = button.closest(kind === 'tf' ? '.tf' : '.choice');
    if (item.classList.contains('is-done')) return;
    var correct = kind === 'tf'
      ? button.getAttribute('data-value') === item.getAttribute('data-answer')
      : button.getAttribute('data-index') === item.getAttribute('data-answer');
    button.classList.add(correct ? 'is-correct' : 'is-wrong');
    if (correct) item.classList.add('is-done');
  }

  function showAnswers(root) {
    if (root.querySelector('.match')) { matchShow(root); return; }
    if (root.querySelector('.sort')) { sortShow(root); return; }
    each(root.querySelectorAll('.gap'), function (gap) {
      if (!gap.classList.contains('is-revealed')) gapSet(root, gap, true);
    });
    each(root.querySelectorAll('.choice, .tf'), function (item) {
      each(item.querySelectorAll('[data-correct]'), function (button) {
        button.classList.add('is-correct', 'is-revealed');
        button.classList.remove('is-wrong');
      });
      item.classList.add('is-done');
    });
    each(root.querySelectorAll('.qset__q'), function (q) { q.classList.add('is-revealed'); });
    nextState(root);
  }

  function nextState(root) {
    var more = root.querySelector('.qset__q:not(.is-revealed)');
    var next = root.querySelector('.ex__next');
    if (next) next.disabled = !more;
  }

  function nextQuestion(root) {
    var hidden = root.querySelector('.qset__q:not(.is-revealed)');
    if (hidden) hidden.classList.add('is-revealed');
    nextState(root);
  }

  function resetExercise(root) {
    sortReset(root);
    each(root.querySelectorAll('.is-correct, .is-wrong, .is-revealed, .is-picked, .is-done'),
      function (el) {
        el.classList.remove('is-correct', 'is-wrong', 'is-revealed', 'is-picked', 'is-done');
        if (el.hasAttribute('aria-pressed')) el.setAttribute('aria-pressed', 'false');
      });
    each(root.querySelectorAll('.match__item[data-pair]'), function (item) {
      item.removeAttribute('data-pair');
      item.removeAttribute('data-partner');
      item.querySelector('.match__tag').textContent = '';
    });
    each(root.querySelectorAll('.gap'), function (gap) {
      gap.setAttribute('aria-label', 'Blank: tap to show the answer');
    });
    each(root.querySelectorAll('.qset__q'), function (q, n) {
      if (n === 0) q.classList.add('is-revealed');
    });
    var tip = root.querySelector('.reading__tip');
    if (tip) tip.textContent = '';
    score(root, '');
    nextState(root);
  }

  function resetExercises(slide) {
    each(slide.querySelectorAll('.ex'), resetExercise);
  }

  function readingWord(root, button) {
    var on = !button.classList.contains('is-picked');
    each(root.querySelectorAll('.reading__word.is-picked'), function (word) {
      setPressed(word, false);
    });
    var tip = root.querySelector('.reading__tip');
    if (on) setPressed(button, true);
    if (tip) {
      tip.textContent = on ? button.textContent + ' = ' + button.getAttribute('data-meaning') : '';
      tip.classList.toggle('is-revealed', on);
    }
  }

  function exercise(button) {
    var root = button.closest('.ex');
    if (!root) return;
    switch (button.getAttribute('data-act')) {
      case 'pick': matchPick(root, button); break;
      case 'check':
        if (root.querySelector('.sort')) sortCheck(root); else matchCheck(root);
        break;
      case 'chip': sortChip(root, button); break;
      case 'column': sortColumn(root, button); break;
      case 'target': sortTarget(root, button); break;
      case 'gap': gapSet(root, button, !button.classList.contains('is-revealed')); break;
      case 'option': choose(root, button, 'choice'); break;
      case 'tf': choose(root, button, 'tf'); break;
      case 'word': readingWord(root, button); break;
      case 'show': showAnswers(root); break;
      case 'next': nextQuestion(root); break;
      case 'reset': resetExercise(root); break;
    }
  }

  // --- self-check: one of the ticks of a line is picked; a second tap on it clears the line
  function selfCheck(button) {
    var row = button.closest('.sc__row');
    var on = !button.classList.contains('is-picked');
    each(row.querySelectorAll('.sc__btn'), function (other) { setPressed(other, false); });
    if (on) setPressed(button, true);
  }

  function clock(seconds) {
    var minutes = Math.floor(seconds / 60), rest = seconds % 60;
    return minutes + ':' + (rest < 10 ? '0' : '') + rest;
  }

  function countdown(el) {
    var total = parseInt(el.getAttribute('data-seconds'), 10) || 0;
    var time = el.querySelector('.countdown__time');
    var hint = el.querySelector('.countdown__hint');
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
    // the notes panel belongs to teacher mode: leaving it must not leave the pupil view narrowed
    body.classList.toggle('notes-open', on);
    refresh();
  }

  // --- pronunciation: the browser's own speech synthesis, British English preferred
  var synth = window.speechSynthesis || null;
  var canSpeak = !!(synth && window.SpeechSynthesisUtterance);
  var voice = null;
  var voicesSeen = false;

  function isEnglish(candidate) { return /^en([-_]|$)/i.test(candidate.lang || ''); }

  function chooseVoice() {
    if (!canSpeak) { body.classList.add('no-speech'); return; }
    var voices = synth.getVoices() || [];
    if (voices.length) voicesSeen = true;
    var english = voices.filter(isEnglish);
    voice = english.filter(function (v) { return /^en[-_]GB$/i.test(v.lang); })[0]
      || english[0] || null;
    // Voices load late in some browsers: only give up once a list has arrived (or after a wait).
    body.classList.toggle('no-speech', !voice && voicesSeen);
  }

  function cancelSpeech() {
    if (canSpeak) synth.cancel();
  }

  function speak(text) {
    text = (text || '').replace(/\s+/g, ' ').trim();
    if (!canSpeak || !voice || !text) return;
    var utterance = new window.SpeechSynthesisUtterance(text);
    utterance.voice = voice;
    utterance.lang = voice.lang;
    utterance.rate = /\s/.test(text) ? 0.95 : 0.85;
    synth.cancel();
    synth.speak(utterance);
  }

  if (canSpeak) {
    chooseVoice();
    if (synth.addEventListener) synth.addEventListener('voiceschanged', chooseVoice);
    else synth.onvoiceschanged = chooseVoice;
    setTimeout(function () {
      voicesSeen = true;
      chooseVoice();
    }, 2000);
  } else {
    body.classList.add('no-speech');
  }

  // --- embedding: a gallery page can drive the deck and read its position
  function notifyParent(index, total) {
    if (window.parent && window.parent !== window) {
      try {
        window.parent.postMessage({ type: 'classdeck:slide', slide: index + 1, total: total }, '*');
      } catch (err) { /* the parent may refuse; the deck works on its own */ }
    }
  }

  window.addEventListener('message', function (event) {
    var data = event.data;
    if (!data || typeof data.type !== 'string') return;
    if (data.type === 'classdeck:go') {
      var number = parseInt(data.slide, 10);
      if (!isNaN(number)) show(number - 1);
    } else if (data.type === 'classdeck:next') {
      next();
    } else if (data.type === 'classdeck:prev') {
      prev();
    }
  });

  // Dragging a word chip onto a sound column (taps work too: pick a chip, then a column).
  var dragged = null;
  stage.addEventListener('dragstart', function (event) {
    var chip = event.target.closest && event.target.closest('.sort__chip');
    if (!chip || editing) return;
    dragged = chip.closest('.sort__item');
    try { event.dataTransfer.setData('text/plain', chip.textContent); } catch (err) { /* ok */ }
  });
  stage.addEventListener('dragover', function (event) {
    if (dragged && event.target.closest('.sort__col, .sort__pool')) event.preventDefault();
  });
  stage.addEventListener('drop', function (event) {
    var zone = dragged && event.target.closest('.sort__col, .sort__pool');
    if (!zone) return;
    event.preventDefault();
    var root = zone.closest('.ex');
    sortClear(root);
    score(root, '');
    var picked = root.querySelector('.sort__chip.is-picked');
    if (picked) setPressed(picked, false);
    (zone.classList.contains('sort__pool') ? zone : zone.querySelector('.sort__drop'))
      .appendChild(dragged);
    dragged = null;
  });
  stage.addEventListener('dragend', function () { dragged = null; });

  stage.addEventListener('click', function (event) {
    var speaker = event.target.closest('.speak');
    if (speaker) {
      event.stopPropagation();
      speak(speaker.getAttribute('data-say'));
      return;
    }
    if (editing) return;
    var tick = event.target.closest('.sc__btn');
    if (tick) { selfCheck(tick); return; }
    var control = event.target.closest('button[data-act]');
    if (control) { exercise(control); return; }
    if (event.target.closest('.flashcard__word')) {
      speak(event.target.closest('.flashcard__word').textContent);
    }
    var tap = event.target.closest('.tap');
    if (tap) { act(tap); return; }
    if (event.target.closest('button, a, .notes, .dots, .ex')) return;
    var rect = stage.getBoundingClientRect();
    var x = (event.clientX - rect.left) / rect.width;
    if (x >= 2 / 3) next();
    else if (x <= 1 / 3) prev();
  });

  var touch = null;
  stage.addEventListener('touchstart', function (event) {
    var point = event.changedTouches[0];
    touch = { x: point.clientX, y: point.clientY, field: !!(editing && fieldOf(event.target)) };
  }, { passive: true });
  stage.addEventListener('touchend', function (event) {
    if (!touch) return;
    var point = event.changedTouches[0];
    var dx = point.clientX - touch.x, dy = point.clientY - touch.y;
    var inField = touch.field;
    touch = null;
    if (inField) return;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy)) return;
    if (dx < 0) next(); else prev();
  }, { passive: true });

  document.addEventListener('keydown', function (event) {
    if (editKey(event)) return;
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
      case 'n': case 'N': if (teacherMode()) body.classList.toggle('notes-open'); break;
      case 'p': case 'P': window.print(); break;
    }
  });

  /*EDIT_SCRIPT*/

  var params = new URLSearchParams(window.location.search);
  if (params.get('teacher') === '1') {
    body.classList.add('teacher-mode');
    body.classList.add('notes-open');
  }
  var start = parseInt((window.location.hash || '').slice(1), 10);
  shown = slides[0] || null;
  show(isNaN(start) ? 0 : start - 1);
  fit();
  function onResize() {
    fit();
    refitSoon();
  }
  window.addEventListener('resize', onResize);
  window.addEventListener('orientationchange', onResize);
  // Fonts and pictures change the layout after the first paint; a mode switch (teacher, notes,
  // edit) changes the room at once; a reveal or an edit changes the text a moment later.
  window.addEventListener('load', refit);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(refit);
  stage.addEventListener('load', refitSoon, true);
  if (window.MutationObserver) {
    new MutationObserver(refit).observe(body, { attributes: true, attributeFilter: ['class'] });
    new MutationObserver(function (records) {
      var relevant = records.some(function (record) {
        var el = record.target.nodeType === 1 ? record.target : record.target.parentElement;
        return el && shown && shown.contains(el) && !el.closest('.countdown');
      });
      if (relevant) refitSoon();
    }).observe(stage, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ['class']
    });
  }
})();
""".strip()

EDIT_SCRIPT_NAME = "deck_edit.js"
"""The editing half of the script (pencil, edit mode, Save), kept in its own file."""


def _compose_script() -> str:
    editing = (
        resources.files("classdeck.export").joinpath(EDIT_SCRIPT_NAME).read_text(encoding="utf-8")
    )
    return _DECK_SCRIPT.replace("/*EDIT_SCRIPT*/", editing.strip())


SCRIPT = _compose_script()


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
    heading = title or unit_line(lesson)
    page_title = title or f"{lesson_label(lesson)} — {unit_line(lesson)}"
    slides = _DeckBuilder(lesson, sources, image_root, heading).build()
    return _substitute(
        _template(),
        {
            "lang": _esc(lesson.language),
            "title": _esc(page_title),
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
