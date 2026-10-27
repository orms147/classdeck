"""Check a hand-off deck against its lesson and embed the lesson's pictures in it.

``docs/output-formats.md``, "Prompt packs": the teacher saves a chat model's reply as ``.html``;
this module verifies that every block id is present exactly once and none was invented, lists the
pupil-visible words outside the allowed list, flags external references and replaces the
``<img data-block-id data-item>`` placeholders with the real pictures as data URIs. The standard
library parser is enough, and the checker never raises on a malformed deck: it reports what it
could read.
"""

from __future__ import annotations

import base64
import contextlib
import html
import mimetypes
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from classdeck.curriculum import Curriculum
from classdeck.english.validators import FUNCTION_WORDS, tokenize
from classdeck.export.source_map import format_citation
from classdeck.handoff.common import (
    PHASE_LABEL,
    Sources,
    allowed_words,
    lemma,
    picture_citation,
)
from classdeck.lesson import (
    ActivityBlock,
    Citation,
    DialogueBlock,
    ImageRefBlock,
    Lesson,
    PhonicsBlock,
    SentencePatternBlock,
    SpanKind,
    VocabularyBlock,
)

NO_BLOCK = "(no block)"
"""Key of ``out_of_list_words`` for pupil text outside any ``data-block-id`` element."""

_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()  # noqa: SIM905
)
_HIDDEN = frozenset({"script", "style", "title", "template", "noscript"})
_NOT_CHECKED = frozenset({"teacher", "ui"})
"""Classes whose elements (and descendants) are not pupil-visible lesson content."""
_URL_ATTRIBUTES = frozenset(
    [
        "src",
        "srcset",
        "href",
        "xlink:href",
        "poster",
        "data",
        "action",
        "formaction",
        "background",
        "content",
        "cite",
        "manifest",
        "ping",
        "style",
    ]
)
_NAMESPACE_HOST = "http://www.w3.org/"
_URL = re.compile(
    r"""https?://[^\s"'`<>)]+|(?<![\w:/.-])//[\w-]+(?:\.[\w-]+)+[^\s"'`<>)]*""", re.IGNORECASE
)


def _external_urls(text: str) -> list[str]:
    """Absolute and protocol-relative URLs (``http://``, ``https://``, ``//host.tld/``).

    XML namespace identifiers such as ``http://www.w3.org/2000/svg`` are not requests.
    """
    return [url for url in _URL.findall(text) if not url.lower().startswith(_NAMESPACE_HOST)]


class DeckCheckResult(BaseModel):
    """What ``check_deck`` found, and the deck with the pictures embedded."""

    model_config = ConfigDict(extra="forbid")

    missing_block_ids: list[str] = []
    extra_block_ids: list[str] = []
    duplicate_block_ids: list[str] = []
    out_of_list_words: dict[str, list[str]] = {}
    external_refs: list[str] = []
    images_embedded: int = 0
    images_unresolved: list[str] = []
    has_doctype: bool = False
    html: str

    @property
    def ok(self) -> bool:
        return (
            not (
                self.missing_block_ids
                or self.extra_block_ids
                or self.duplicate_block_ids
                or self.out_of_list_words
                or self.external_refs
                or self.images_unresolved
            )
            and self.has_doctype
        )

    def report(self) -> str:
        """One line per issue; ``OK`` when there is none."""
        lines: list[str] = []
        if self.missing_block_ids:
            lines.append("Missing block ids: " + ", ".join(self.missing_block_ids))
        if self.extra_block_ids:
            lines.append("Unknown block ids: " + ", ".join(self.extra_block_ids))
        if self.duplicate_block_ids:
            lines.append("Duplicate block ids: " + ", ".join(self.duplicate_block_ids))
        for block_id, words in self.out_of_list_words.items():
            lines.append(f"Out-of-list words in {block_id}: {', '.join(words)}")
        if self.external_refs:
            lines.append("External references: " + ", ".join(self.external_refs))
        if self.images_unresolved:
            lines.append("Pictures not embedded: " + "; ".join(self.images_unresolved))
        if not self.has_doctype:
            lines.append("No <!doctype html> declaration")
        embedded = f"Pictures embedded: {self.images_embedded}" if self.images_embedded else ""
        if not lines:
            return f"OK ({embedded.lower()})" if embedded else "OK"
        if embedded:
            lines.append(embedded)
        return "\n".join(lines)


# --- parsing -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Frame:
    tag: str
    block_id: str | None
    hidden: bool


@dataclass
class _Placeholder:
    block_id: str
    item: str | None
    src: str | None
    raw: str
    start: int
    attrs: list[tuple[str, str | None]]
    """Offset of the tag in the source text, ``-1`` when it could not be located."""


class _DeckParser(HTMLParser):
    """Collects block ids, pupil-visible text, external references and picture placeholders."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self._line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
        self._search_from = 0
        self.stack: list[_Frame] = []
        self.occurrences: list[tuple[str, str | None]] = []
        self.texts: dict[str | None, list[str]] = {}
        self.external: list[str] = []
        self.placeholders: list[_Placeholder] = []
        self.has_doctype = False

    def handle_decl(self, decl: str) -> None:
        if decl.strip().lower().startswith("doctype"):
            self.has_doctype = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        for name, value in attributes.items():
            if name in _URL_ATTRIBUTES and not value.strip().lower().startswith("data:"):
                self.external.extend(_external_urls(value))

        block_id = attributes.get("data-block-id", "").strip() or None
        if tag == "img" and block_id:
            self.placeholders.append(
                _Placeholder(
                    block_id,
                    attributes.get("data-item"),
                    attributes.get("src"),
                    *self._locate(),
                    attrs,
                )
            )
        elif block_id:
            self.occurrences.append((block_id, attributes.get("data-item")))

        parent = self.stack[-1] if self.stack else None
        hidden = (
            (parent.hidden if parent else False)
            or tag in _HIDDEN
            or bool(_NOT_CHECKED.intersection(attributes.get("class", "").split()))
        )
        nearest = block_id if block_id and tag != "img" else (parent.block_id if parent else None)
        if tag not in _VOID:
            self.stack.append(_Frame(tag, nearest, hidden))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        frame = self.stack[-1] if self.stack else None
        if frame is not None and frame.tag in ("style", "script"):
            self.external.extend(_external_urls(data))
        if (frame is not None and frame.hidden) or not data.strip():
            return
        self.texts.setdefault(frame.block_id if frame else None, []).append(data)

    def _locate(self) -> tuple[str, int]:
        """The raw text of the tag being opened and its offset in the source."""
        raw = self.get_starttag_text() or ""
        line, column = self.getpos()
        start = self._line_starts[line - 1] + column if 0 < line <= len(self._line_starts) else -1
        if not raw or self.source[start : start + len(raw)] != raw:
            start = self.source.find(raw, self._search_from) if raw else -1
        if start >= 0:
            self._search_from = start + len(raw)
        return raw, start


# --- words -------------------------------------------------------------------------------------


def _slide_texts(lesson: Lesson, curriculum: Curriculum | None) -> list[str]:
    """Pupil-visible strings the slide types show that the level checks leave out.

    The cover and the dividers show the lesson and section titles with their phase and minutes;
    an activity slide shows its title; a flashcard its IPA and (on tap) ``meaning_vi``; a pattern
    slide its ``function``; a phonics slide its sound and letters; a dialogue its speakers; a
    picture slide its caption. The cover words (grade, unit, lesson, period) are allowed even
    outside ``class="ui"``.
    """
    texts = [
        lesson.title,
        *(section.title for section in lesson.sections),
        *PHASE_LABEL.values(),
        "min minutes",
        f"Grade {lesson.grade}",
        "Unit Lesson Period",
    ]
    if curriculum is not None:
        with contextlib.suppress(KeyError):
            texts.append(curriculum.unit(lesson.curriculum.unit_id).title)
    for block in lesson.blocks.values():
        match block:
            case ActivityBlock():
                texts.append(block.content.title)
            case VocabularyBlock():
                for item in block.content.items:
                    texts.extend(text for text in (item.meaning_vi, item.ipa) if text)
            case SentencePatternBlock():
                if block.content.function:
                    texts.append(block.content.function)
            case PhonicsBlock():
                texts.extend([block.content.sound, block.content.letters])
            case DialogueBlock():
                texts.extend(line.speaker for line in block.content.lines)
            case ImageRefBlock():
                texts.append(block.content.caption)
    return [text for text in texts if text]


def _allowed_forms(lesson: Lesson, curriculum: Curriculum | None) -> set[str]:
    forms: set[str] = set()
    for entry in [*allowed_words(lesson, curriculum), *_slide_texts(lesson, curriculum)]:
        for token in tokenize(entry):
            forms.add(token.surface)
            forms.add(lemma(token.surface))
    return forms


def _offenders(chunks: list[str], allowed: set[str]) -> list[str]:
    found: set[str] = set()
    for chunk in chunks:
        for token in tokenize(chunk):
            if len(token.surface) == 1:  # a lone letter is an avatar initial, not a word
                continue
            if token.proper or token.surface in FUNCTION_WORDS or token.surface in allowed:
                continue
            if lemma(token.surface) in allowed:
                continue
            found.add(token.surface)
    return sorted(found)


# --- pictures ----------------------------------------------------------------------------------


def _picture_citation(
    lesson: Lesson, placeholder: _Placeholder, sources: Sources | None
) -> tuple[Citation | None, str]:
    """The citation behind a placeholder, or the reason there is none."""
    block = lesson.blocks.get(placeholder.block_id)
    if block is None:
        return None, "unknown block"
    item_text = (placeholder.item or "0").strip()
    if not item_text.isdigit():
        return None, f"data-item {item_text!r} is not an index"
    item = int(item_text)
    match block:
        case VocabularyBlock():
            items = block.content.items
            if item >= len(items):
                return None, f"the block has no item {item}"
            if items[item].image is None:
                return None, "the item cites no picture"
            return items[item].image, ""
        case ImageRefBlock():
            citation = picture_citation(block, sources)
            if citation is None:
                return None, "the block cites no source"
            return citation, ""
        case _:
            return None, f"{block.kind} blocks carry no picture"


def _picture_file(
    citation: Citation, sources: Sources | None, image_root: Path | None
) -> tuple[Path | None, str]:
    """The image file behind a citation, only if it is an image span inside ``image_root``."""
    if sources is None or image_root is None:
        return None, "sources and image_root are needed to embed pictures"
    document = sources.get(citation.doc_id)
    span = document.find_span(citation.page, citation.span_id) if document else None
    if span is None:
        return None, f"cited span {format_citation(citation)} not found"
    if span.kind is not SpanKind.IMAGE or not span.image_path:
        return None, f"cited span {format_citation(citation)} is not an image"
    root = Path(image_root).resolve()
    path = (root / span.image_path).resolve()
    if not path.is_relative_to(root):
        return None, f"image path {span.image_path!r} escapes image_root"
    if not path.is_file():
        return None, f"image file {span.image_path!r} not found"
    return path, ""


def _media_type(data: bytes, path: Path) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed if guessed and guessed.startswith("image/") else "image/png"


def _data_uri(path: Path) -> str:
    data = path.read_bytes()
    return f"data:{_media_type(data, path)};base64,{base64.b64encode(data).decode('ascii')}"


def _with_src(placeholder: _Placeholder, uri: str) -> str:
    """The placeholder tag rebuilt with ``src`` set to ``uri``, its other attributes kept."""
    name = re.match(r"<\s*([^\s/>]+)", placeholder.raw)
    parts = [f"<{name.group(1) if name else 'img'}"]
    for key, value in placeholder.attrs:
        if key.lower() == "src":
            continue
        parts.append(key if value is None else f'{key}="{html.escape(value, quote=True)}"')
    parts.append(f'src="{uri}"')
    return " ".join(parts) + (" />" if placeholder.raw.rstrip().endswith("/>") else ">")


# --- the check ---------------------------------------------------------------------------------


def check_deck(
    lesson: Lesson,
    deck_html: str,
    *,
    sources: Sources | None = None,
    image_root: Path | None = None,
    curriculum: Curriculum | None = None,
) -> DeckCheckResult:
    """Hold a deck to its lesson and return the findings with the pictures embedded.

    Block ids are read from ``data-block-id`` on every element but ``<img>``, whose
    ``data-block-id`` marks a picture placeholder; an id is a duplicate when it appears twice with
    the same ``data-item`` (or twice without one). Pupil-visible text is every text node outside
    ``<script>``, ``<style>`` and elements with class ``teacher``, including their descendants.
    Pictures are embedded only when ``sources`` and ``image_root`` are given.
    """
    parser = _DeckParser(deck_html)
    with contextlib.suppress(Exception):
        parser.feed(deck_html)
        parser.close()

    ordered = [block.id for block in lesson.ordered_blocks()]
    expected = [*ordered, *(key for key in lesson.blocks if key not in set(ordered))]
    found = {block_id for block_id, _ in parser.occurrences}
    counts = Counter(parser.occurrences)
    missing = [block_id for block_id in expected if block_id not in found]
    extra = list(dict.fromkeys(b for b, _ in parser.occurrences if b not in lesson.blocks))
    duplicates = list(dict.fromkeys(b for (b, item), n in counts.items() if n > 1))

    allowed = _allowed_forms(lesson, curriculum)
    out_of_list: dict[str, list[str]] = {}
    for block_id, chunks in parser.texts.items():
        offenders = _offenders(chunks, allowed)
        if offenders:
            out_of_list[block_id or NO_BLOCK] = offenders

    embedded = 0
    unresolved: list[str] = []
    replacements: list[tuple[int, int, str]] = []
    for placeholder in parser.placeholders:
        if (placeholder.src or "").strip().lower().startswith("data:"):
            continue
        label = f"{placeholder.block_id} item {placeholder.item or '0'}"
        citation, problem = _picture_citation(lesson, placeholder, sources)
        path = None
        if citation is not None:
            path, problem = _picture_file(citation, sources, image_root)
        if path is None:
            unresolved.append(f"{label}: {problem}")
        elif placeholder.start < 0:
            unresolved.append(f"{label}: the <img> tag could not be located in the HTML text")
        else:
            end = placeholder.start + len(placeholder.raw)
            fixed_tag = _with_src(placeholder, _data_uri(path))
            replacements.append((placeholder.start, end, fixed_tag))
            embedded += 1

    fixed = deck_html
    for start, end, replacement in sorted(replacements, reverse=True):
        fixed = fixed[:start] + replacement + fixed[end:]

    return DeckCheckResult(
        missing_block_ids=missing,
        extra_block_ids=extra,
        duplicate_block_ids=duplicates,
        out_of_list_words=out_of_list,
        external_refs=list(dict.fromkeys(parser.external)),
        images_embedded=embedded,
        images_unresolved=unresolved,
        has_doctype=parser.has_doctype,
        html=fixed,
    )
