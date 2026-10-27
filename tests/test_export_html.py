from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from classdeck.export import AI_ADDED_BADGE, AI_ADDED_NOTE, ExportBlocked, export_html
from classdeck.export.html import (
    available_themes,
    parse_manifest,
    render_html,
    slot_fills,
    theme_ids,
)
from classdeck.lesson import Lesson, Provenance, SourceDocument
from tests.test_export import FAILURE, WARNING, make_export_lesson, write_png
from tests.test_lesson_schema import DOC_ID, IPA_HELLO, IPA_HI, make_source_document

EMOJI = re.compile(r"[\U0001F000-\U0001FAFF☀-➿]")
VOID_TAGS = {"img", "meta", "br", "hr", "link", "input"}
EXPECTED_SLIDES = [
    ("cover", "lesson"),
    ("divider", "warm_up"),
    ("activity", "warm_up"),
    ("divider", "presentation"),
    ("flashcard", "presentation"),
    ("flashcard", "presentation"),
    ("pattern", "presentation"),
    ("dialogue", "presentation"),
    ("image", "presentation"),
    ("divider", "practice"),
    ("phonics", "practice"),
    ("activity", "practice"),
    ("divider", "production"),
    ("activity", "production"),
    ("divider", "wrap_up"),
    ("summary", "wrap_up"),
    ("source-map", "teacher"),
]


# --- a small DOM over html.parser --------------------------------------------------------------


class Node:
    def __init__(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tag = tag
        self.attrs: dict[str, str | None] = dict(attrs)
        self.children: list[Node | str] = []

    @property
    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def text(self) -> str:
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self) -> Iterator[Node]:
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()

    def find(self, tag: str | None = None, class_: str | None = None, **attrs: str) -> list[Node]:
        """Descendants with this tag, this class and these ``data_x`` → ``data-x`` attributes."""
        wanted = {key.replace("_", "-"): value for key, value in attrs.items()}
        return [
            node
            for node in self.walk()
            if (tag is None or node.tag == tag)
            and (class_ is None or class_ in node.classes)
            and all(node.attrs.get(key) == value for key, value in wanted.items())
        ]

    def one(self, tag: str | None = None, class_: str | None = None, **attrs: str) -> Node:
        found = self.find(tag, class_, **attrs)
        assert len(found) == 1, f"{len(found)} matches for {tag} {class_} {attrs}"
        return found[0]


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document", [])
        self.stack = [self.root]
        self.doctype: str | None = None

    def handle_decl(self, decl: str) -> None:
        self.doctype = decl

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def parse(text: str) -> tuple[Node, str | None]:
    parser = TreeParser()
    parser.feed(text)
    parser.close()
    return parser.root, parser.doctype


@dataclass
class Deck:
    path: Path
    text: str
    root: Node
    doctype: str | None

    def slides(self) -> list[Node]:
        return self.root.find("section", "slide")

    def slide_of(self, block_id: str, index: int = 0) -> Node:
        """The ``index``-th slide carrying an element with this block id."""
        return [s for s in self.slides() if s.find(data_block_id=block_id)][index]

    def slides_typed(self, slide_type: str) -> list[Node]:
        return self.root.find("section", "slide", data_slide_type=slide_type)


def load(path: Path) -> Deck:
    text = path.read_text(encoding="utf-8")
    root, doctype = parse(text)
    return Deck(path, text, root, doctype)


# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture
def lesson() -> Lesson:
    return make_export_lesson()


@pytest.fixture
def sources() -> dict[str, SourceDocument]:
    return {DOC_ID: make_source_document()}


@pytest.fixture
def image_root(tmp_path: Path) -> Path:
    root = tmp_path / "workdir"
    write_png(root)
    return root


@pytest.fixture
def deck(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> Deck:
    # The design-language tests below describe the kids theme; the default is covered separately.
    path = export_html(
        lesson, tmp_path / "deck.html", sources=sources, image_root=image_root, theme="kids"
    )
    return load(path)


# --- document shell ----------------------------------------------------------------------------


def test_document_shell(deck: Deck, lesson: Lesson) -> None:
    assert deck.text.lower().startswith("<!doctype html>")
    assert deck.doctype is not None
    assert deck.doctype.lower() == "doctype html"
    assert deck.root.one("html").attrs["lang"] == "en"
    assert deck.root.one("title").text() == lesson.title
    assert deck.root.one("meta", name="viewport").attrs["content"]
    assert len(deck.root.find("style")) == 2
    assert len(deck.root.find("script")) == 1
    assert deck.root.find("link") == []
    assert deck.root.one("body").attrs["data-theme"] == "kids"


def test_no_external_references(deck: Deck) -> None:
    lowered = deck.text.lower()
    assert "http" not in lowered
    assert "xmlns" not in lowered
    assert "url(" not in lowered
    for image in deck.root.find("img"):
        assert (image.attrs.get("src") or "").startswith("data:image/")


def test_slides_follow_the_lesson_order(deck: Deck) -> None:
    slides = deck.slides()
    assert [(s.attrs["data-slide-type"], s.attrs["data-phase"]) for s in slides] == EXPECTED_SLIDES
    assert [s.attrs["aria-label"] for s in slides[:2]] == ["Slide 1 of 17", "Slide 2 of 17"]


def test_every_block_is_rendered_with_its_kind_and_provenance(deck: Deck, lesson: Lesson) -> None:
    for block in lesson.blocks.values():
        carriers = [n for n in deck.root.find(data_block_id=block.id) if "data-kind" in n.attrs]
        assert carriers, block.id
        assert {n.attrs["data-kind"] for n in carriers} == {block.kind}
        assert {n.attrs["data-provenance"] for n in carriers} == {block.provenance.value}
    referenced = {n.attrs["data-block-id"] for n in deck.root.find() if "data-block-id" in n.attrs}
    assert referenced == set(lesson.blocks)


# --- cover and dividers ------------------------------------------------------------------------


def test_cover_slide(deck: Deck, lesson: Lesson) -> None:
    cover = deck.slides()[0]
    assert cover.one("h1").text() == lesson.title
    assert cover.one(class_="cover__meta").text() == "Grade 3 · ta3.u1 · Lesson 1 · Period 1"
    strip = cover.one("ol", "phase-strip").find("li")
    assert [li.attrs["data-phase"] for li in strip] == [s.phase.value for s in lesson.sections]
    assert [li.one(class_="phase-strip__minutes").text() for li in strip] == [
        f"{s.minutes} min" for s in lesson.sections
    ]
    assert cover.one(class_="cover__total").text() == "35 min"
    assert cover.one(class_="icon", data_icon="warm_up").text() == ""


def test_cover_meta_skips_missing_parts(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    lesson.curriculum.lesson_no = None
    lesson.curriculum.period = None
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    assert deck.slides()[0].one(class_="cover__meta").text() == "Grade 3 · ta3.u1"


def test_divider_has_icon_title_minutes_and_a_countdown(deck: Deck, lesson: Lesson) -> None:
    dividers = deck.slides_typed("divider")
    assert len(dividers) == len(lesson.sections)
    for divider, section in zip(dividers, lesson.sections, strict=True):
        assert divider.attrs["data-section-id"] == section.id
        assert divider.one("h2", "slide-title").text() == section.title
        assert divider.one(class_="divider__minutes").text() == f"{section.minutes} min"
        assert divider.one(class_="icon").attrs["data-icon"] == section.phase.value
        countdown = divider.one(class_="countdown")
        assert countdown.attrs["data-seconds"] == str(section.minutes * 60)
        assert "tap" in countdown.classes
        assert countdown.one(class_="countdown__time").text() == f"{section.minutes}:00"


# --- vocabulary --------------------------------------------------------------------------------


def test_one_flashcard_per_item_with_the_picture_dominant(deck: Deck) -> None:
    cards = deck.root.find(class_="flashcard", data_block_id="b_02")
    assert len(cards) == 2
    first, second = cards
    assert first.attrs["data-kind"] == "vocabulary"
    assert first.attrs["data-item"] == "0"
    assert {"tap", "reveal", "has-picture"} <= first.classes
    assert first.attrs["role"] == "button"
    assert first.attrs["tabindex"] == "0"
    assert first.one(class_="flashcard__word").text() == "hello"
    assert first.one(class_="flashcard__ipa").text() == IPA_HELLO
    meaning = first.one(class_="flashcard__meaning")
    assert meaning.text() == "xin chào"
    assert meaning.attrs["lang"] == "vi"

    image = first.one("img")
    assert image.attrs["data-block-id"] == "b_02"
    assert image.attrs["data-item"] == "0"
    assert image.attrs["alt"] == "hello"
    assert (image.attrs["src"] or "").startswith("data:image/png;base64,")

    assert second.one(class_="flashcard__word").text() == "hi"
    assert second.one(class_="flashcard__ipa").text() == IPA_HI
    assert second.find("img") == []
    assert "has-picture" not in second.classes
    assert deck.root.find("img", data_block_id="b_02", data_item="1") == []


def test_flashcards_sit_on_consecutive_slides_of_the_section_phase(deck: Deck) -> None:
    first, second = deck.slide_of("b_02"), deck.slide_of("b_02", 1)
    slides = deck.slides()
    assert slides.index(second) == slides.index(first) + 1
    assert first.attrs["data-phase"] == second.attrs["data-phase"] == "presentation"
    assert "Example: Hello, I'm Ha." in first.one(class_="notes").text()


def test_pictures_need_sources_and_a_file_under_image_root(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    def images(**kwargs: Any) -> int:
        deck = load(export_html(lesson, tmp_path / "x.html", **kwargs))
        return len(deck.root.find("img"))

    assert images(sources=sources, image_root=image_root) == 3  # flashcard, summary, picture
    assert images(sources=sources) == 0
    assert images(image_root=image_root) == 0
    assert images(sources=sources, image_root=tmp_path / "empty") == 0


def test_image_path_cannot_escape_image_root(
    tmp_path: Path, lesson: Lesson, image_root: Path
) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(write_png(tmp_path / "scratch").read_bytes())
    document = make_source_document()
    document.pages[0].spans[3].image_path = "../outside.png"
    deck = load(
        export_html(lesson, tmp_path / "x.html", sources={DOC_ID: document}, image_root=image_root)
    )
    assert deck.root.find("img") == []


def test_unknown_image_bytes_are_not_embedded(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    write_png(image_root).write_bytes(b"not an image at all")
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources, image_root=image_root))
    assert deck.root.find("img") == []


# --- dialogue, pattern, phonics ----------------------------------------------------------------


def test_dialogue_bubbles_alternate_by_speaker_and_show_the_first_line(
    deck: Deck, lesson: Lesson
) -> None:
    slide = deck.slide_of("b_05")
    chat = slide.one(class_="dialogue")
    assert {"tap", "stepper"} <= chat.classes
    bubbles = chat.find(class_="bubble")
    assert len(bubbles) == len(lesson.blocks["b_05"].content.lines)  # type: ignore[union-attr]
    assert [b.attrs["data-speaker"] for b in bubbles] == ["Ha", "Tien"]
    assert ["bubble--left" in b.classes for b in bubbles] == [True, False]
    assert ["bubble--right" in b.classes for b in bubbles] == [False, True]
    assert ["pending" in b.classes for b in bubbles] == [False, True]
    assert "complete" not in chat.classes
    assert [b.one(class_="bubble__avatar").text() for b in bubbles] == ["H", "T"]
    assert bubbles[0].one(class_="bubble__text").text() == "Ha: Hello, I'm Ha."
    assert slide.one(class_="dialogue__context").text() == "Ha meets Tien."


def test_pattern_slide_has_a_dashed_slot_and_the_examples(deck: Deck) -> None:
    pattern = deck.slide_of("b_03").one(class_="pattern")
    assert {"tap", "cycle"} <= pattern.classes
    text = pattern.one(class_="pattern__text")
    assert text.text() == "Hello, I'm ___."
    slot = text.one(class_="pattern__slot")
    assert slot.text() == "___"
    assert slot.attrs["data-empty"] == "___"
    examples = pattern.find(class_="pattern__example")
    assert [e.text() for e in examples] == ["Hello, I'm Ha."]
    assert examples[0].attrs["data-fills"] == '["Ha"]'
    assert pattern.one(class_="pattern__function").text() == "Introduce yourself"
    assert pattern.one(class_="pattern__line").text() == ""


def test_pattern_examples_that_do_not_align_carry_no_fills(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    content = lesson.blocks["b_03"].content
    content.examples = ["Hello, I'm Tien.", "Hi there, Ha!"]  # type: ignore[union-attr]
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    examples = deck.slide_of("b_03").find(class_="pattern__example")
    assert [e.attrs.get("data-fills") for e in examples] == ['["Tien"]', None]


def test_slot_fills() -> None:
    assert slot_fills("Hello, I'm ___.", "Hello, I'm Ha.") == ["Ha"]
    assert slot_fills("hello, I'm ___.", "Hello, I'm Ha.") == ["Ha"]
    assert slot_fills("___ is a cat.", "Tom is a cat.") == ["Tom"]
    assert slot_fills("What's your name? ___", "What's your name? I'm Tien.") == ["I'm Tien."]
    assert slot_fills("I'm ___. I'm ___.", "I'm Ha. I'm nine.") == ["Ha", "nine"]
    assert slot_fills("Hello, I'm ___.", "Hi, I'm Ha.") is None
    assert slot_fills("Hello, I'm ___.", "Hello, I'm Ha!") is None
    assert slot_fills("Hello, I'm ___.", "Hello, I'm .") is None
    assert slot_fills("___ ___", "Ha Tien") == ["Ha", "Tien"]
    assert slot_fills("___ ___", "Ha") is None
    assert slot_fills("No slot here.", "No slot here.") is None


def test_phonics_slide_marks_the_target_letters(deck: Deck) -> None:
    phonics = deck.slide_of("b_06").one(class_="phonics")
    assert phonics.one(class_="phonics__sound").text() == "/h/"
    assert phonics.one(class_="phonics__letters").text() == "h"
    words = phonics.one(class_="phonics__words").find("li")
    assert [w.text() for w in words] == ["hello", "hi"]
    assert [w.one("mark").text() for w in words] == ["h", "h"]
    assert [line.text() for line in phonics.one(class_="phonics__chant").find("li")] == [
        "H, h, hello!"
    ]


# --- activity, summary, picture ----------------------------------------------------------------


def test_activity_slide(deck: Deck) -> None:
    slide = deck.slide_of("b_07")
    activity = slide.one(class_="activity")
    assert activity.one(class_="icon").attrs["data-icon"] == "game"
    assert activity.one("h2").text() == "Name chain"
    assert activity.one(class_="activity__instructions").text() == (
        "Pass the ball. Say 'Hello, I'm ...' and your name."
    )
    steps = activity.one("ol", "activity__steps").find("li")
    assert [s.one(class_="step__number").text() for s in steps] == ["1", "2"]
    assert [s.one(class_="step__text").text() for s in steps] == [
        "Catch the ball, say 'Hello, I'm (name).'",
        "Pass it on.",
    ]
    countdown = activity.one(class_="countdown")
    assert countdown.attrs["data-seconds"] == "300"
    assert countdown.one(class_="countdown__time").text() == "5:00"

    notes = slide.one(class_="notes")
    assert "teacher" in notes.classes
    text = notes.text()
    for expected in (
        "Model the chain with two pupils.",
        "Start the ball.",
        "Materials: a soft ball",
        "Interaction: whole class",
        "5 min",
        "If the class is large, run two chains in parallel.",
        "Competences: communication, cooperation",
    ):
        assert expected in text
    assert "Model the chain with two pupils." not in activity.text()


def test_summary_slide_shows_the_lessons_words_with_pictures_and_the_pattern(deck: Deck) -> None:
    summary = deck.slide_of("b_08").one(class_="summary")
    cards = summary.one(class_="summary-grid").find(class_="summary-card")
    assert [c.one("figcaption").text() for c in cards] == ["hello", "hi"]
    image = cards[0].one("img")
    assert image.attrs["data-block-id"] == "b_02"
    assert image.attrs["data-item"] == "0"
    assert image.attrs["alt"] == "hello"
    assert cards[1].find("img") == []
    pattern = summary.one(class_="summary__pattern-text")
    assert pattern.text() == "Hello, I'm ___."
    assert pattern.one(class_="pattern__slot")
    assert summary.find(class_="summary__bullets") == []
    assert "hello, hi" in deck.slide_of("b_08").one(class_="notes").text()


def test_summary_falls_back_to_its_bullets_without_words_or_patterns(
    tmp_path: Path, lesson: Lesson
) -> None:
    for section in lesson.sections:
        section.block_ids = [b for b in section.block_ids if b not in ("b_02", "b_03")]
    deck = load(export_html(lesson, tmp_path / "x.html"))
    summary = deck.slide_of("b_08").one(class_="summary")
    assert summary.find(class_="summary-grid") == []
    assert [li.text() for li in summary.one(class_="summary__bullets").find("li")] == [
        "hello, hi",
        "Hello, I'm ___.",
    ]


def test_image_ref_slide_shows_the_picture_and_caption(deck: Deck) -> None:
    figure = deck.slide_of("b_09").one(class_="image-ref")
    image = figure.one("img")
    assert image.attrs["data-block-id"] == "b_09"
    assert image.attrs["alt"] == "Two children waving"
    assert (image.attrs["src"] or "").startswith("data:image/png;base64,")
    assert figure.one("figcaption").text() == "Ha and Tien say hello."


def test_image_ref_slide_without_the_file_shows_the_alt_text(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    figure = deck.slide_of("b_09").one(class_="image-ref")
    assert figure.find("img") == []
    assert figure.one(class_="image-ref__placeholder").text() == "Picture: Two children waving"


# --- teacher layer -----------------------------------------------------------------------------


def test_objective_and_teacher_note_live_in_teacher_only_notes(deck: Deck) -> None:
    objective = deck.root.one(data_block_id="b_01")
    assert "teacher" in objective.classes
    assert objective.attrs["data-kind"] == "objective"
    assert deck.slides()[0].find(data_block_id="b_01") == [objective]
    assert "Pupils can greet each other and say their names." in objective.text()
    assert "Derived from ta3.u1.o1" in objective.text()

    note = deck.root.one(data_block_id="b_11")
    assert "teacher" in note.classes
    assert deck.slide_of("b_07").find(data_block_id="b_11") == [note]
    assert "Praise effort, not only accuracy." in note.text()
    assert f"Teacher note ({AI_ADDED_BADGE})" in note.text()


def test_teacher_only_elements_carry_the_teacher_class(deck: Deck) -> None:
    for class_ in ("notes", "footer", "badge"):
        found = deck.root.find(class_=class_)
        assert found, class_
        assert all("teacher" in node.classes for node in found)
    for slide in deck.slides_typed("notes") + deck.slides_typed("source-map"):
        assert "teacher" in slide.classes
    assert ".teacher-mode" in deck.text


def test_source_footers(deck: Deck) -> None:
    for index in (0, 1):
        footer = deck.slide_of("b_02", index).one("footer")
        assert "teacher" in footer.classes
        assert footer.text() == "b_02 · Source: p. 6, ¶3"
    assert deck.slide_of("b_03").one("footer").text() == "b_03 · Source: p. 6, ¶6"
    assert deck.slide_of("b_08").one("footer").text() == "b_08 · Derived from b_02, b_07"
    assert deck.slide_of("b_10").one("footer").text() == "b_10 · Derived from b_02"
    assert deck.slide_of("b_07").one("footer").text() == "b_07"
    assert deck.slides()[0].find("footer") == []


def test_generated_blocks_are_badged(deck: Deck) -> None:
    slide = deck.slide_of("b_07")
    badge = slide.one(class_="badge")
    assert badge.text() == AI_ADDED_BADGE
    assert "teacher" in badge.classes
    assert "badged" in slide.classes
    assert AI_ADDED_NOTE in slide.one(class_="notes").text()
    for block_id in ("b_02", "b_03", "b_05", "b_08"):
        assert deck.slide_of(block_id).find(class_="badge") == []


def test_every_flashcard_of_a_generated_block_is_badged(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    lesson.blocks["b_02"].provenance = Provenance.GENERATED
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    for index in (0, 1):
        assert deck.slide_of("b_02", index).one(class_="badge").text() == AI_ADDED_BADGE


def test_source_map_slide_is_last_and_teacher_only(deck: Deck, lesson: Lesson) -> None:
    slide = deck.slides()[-1]
    assert slide.attrs["data-slide-type"] == "source-map"
    assert "teacher" in slide.classes
    table = slide.one("table")
    header = [th.text() for th in table.one("thead").find("th")]
    assert header == ["Block", "Kind", "Provenance", "Citation", "Quote"]
    rows = [[td.text() for td in tr.find("td")] for tr in table.one("tbody").find("tr")]
    assert [row[0] for row in rows] == [block.id for block in lesson.ordered_blocks()]
    by_block = {row[0]: row for row in rows}
    assert by_block["b_02"] == ["b_02", "vocabulary", "Source", "p. 6, ¶3", "Hello, I'm Ha."]
    assert by_block["b_03"][4] == "Hello, I'm ___. Hi, ___. I'm ___."
    assert by_block["b_07"][2:] == ["AI-added", "—", ""]
    assert by_block["b_08"][2:4] == ["Derived", "Derived from b_02, b_07"]


def test_notes_slide_appears_only_when_there_are_warnings(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], deck: Deck
) -> None:
    assert deck.slides_typed("notes") == []

    lesson.blocks["b_02"].checks.append(WARNING)
    lesson.checks.append(WARNING.model_copy(update={"name": "english.timing", "detail": "38 min"}))
    warned = load(export_html(lesson, tmp_path / "warn.html", sources=sources))
    notes = warned.slides_typed("notes")
    assert len(notes) == 1
    assert "teacher" in notes[0].classes
    types = [s.attrs["data-slide-type"] for s in warned.slides()]
    assert types[-2:] == ["notes", "source-map"]
    items = [li.text() for li in notes[0].one("ul", "warnings").find("li")]
    assert items == [
        "b_02 — english.vocab_level: 1 off-list word",
        "Lesson — english.timing: 38 min",
    ]


# --- gate, themes, determinism -----------------------------------------------------------------


def test_export_refuses_a_lesson_with_a_failed_check(tmp_path: Path, lesson: Lesson) -> None:
    lesson.blocks["b_07"].checks.append(FAILURE)
    out = tmp_path / "out" / "deck.html"
    with pytest.raises(ExportBlocked) as raised:
        export_html(lesson, out)
    assert raised.value.failures == [("b_07", FAILURE)]
    assert not out.exists()
    assert not out.parent.exists()
    with pytest.raises(ExportBlocked):
        render_html(lesson)


def test_available_themes() -> None:
    themes = available_themes()
    ids = [theme.id for theme in themes]
    assert ids == sorted(ids)
    assert {"kids", "plain"} <= set(ids)
    for theme in themes:
        assert theme.label and theme.description and theme.suits
    assert next(t for t in themes if t.id == "kids").label == "Kids"
    assert theme_ids() == ids


def test_kids_theme_has_the_phase_colours_and_emoji(deck: Deck) -> None:
    lowered = deck.text.lower()
    for colour in ("#f9c74f", "#4d96ff", "#6bcb77", "#ff6b6b", "#9d4edd", "#fffdf7", "#1f2937"):
        assert colour in lowered
    assert EMOJI.search(deck.text)
    assert "prefers-reduced-motion" in deck.text
    assert "@media print" in deck.text


def test_plain_theme_is_monochrome_without_emoji(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path, deck: Deck
) -> None:
    path = export_html(
        lesson, tmp_path / "plain.html", sources=sources, image_root=image_root, theme="plain"
    )
    plain = load(path)
    assert plain.text != deck.text
    assert not EMOJI.search(plain.text)
    assert "#f9c74f" not in plain.text.lower()
    assert plain.root.one("body").attrs["data-theme"] == "plain"
    # same structure: the slides and the blocks are identical, only the CSS differs
    assert [s.attrs["data-slide-type"] for s in plain.slides()] == [
        s.attrs["data-slide-type"] for s in deck.slides()
    ]
    assert plain.root.one("script").text() == deck.root.one("script").text()
    assert len(plain.root.find(class_="icon")) == len(deck.root.find(class_="icon"))


def test_unknown_theme_is_rejected_before_anything_is_written(
    tmp_path: Path, lesson: Lesson
) -> None:
    out = tmp_path / "out" / "deck.html"
    with pytest.raises(ValueError, match="kids, plain"):
        export_html(lesson, out, theme="neon")
    assert not out.parent.exists()


def test_render_matches_the_file_and_is_deterministic(
    deck: Deck, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    rendered = render_html(lesson, sources=sources, image_root=image_root, theme="kids")
    assert rendered == deck.text
    again = render_html(make_export_lesson(), sources=sources, image_root=image_root, theme="kids")
    assert again == rendered
    assert deck.path.stat().st_size < 5 * 1024 * 1024


def test_title_override(tmp_path: Path, lesson: Lesson) -> None:
    deck = load(export_html(lesson, tmp_path / "x.html", title="Hello <everyone>"))
    assert deck.root.one("title").text() == "Hello <everyone>"
    assert deck.slides()[0].one("h1").text() == "Hello <everyone>"
    assert "&lt;everyone&gt;" in deck.text


def test_navigation_and_mode_keys_are_wired(deck: Deck) -> None:
    script = deck.root.one("script").text()
    for needle in (
        "'ArrowRight'",
        "'ArrowLeft'",
        "'ArrowUp'",
        "'ArrowDown'",
        "'Home'",
        "'End'",
        "'touchstart'",
        "requestFullscreen",
        "teacher-mode",
        "notes-open",
        "window.print()",
        "get('teacher')",
    ):
        assert needle in script


# --- deck chrome (class="ui") ------------------------------------------------------------------


def text_nodes(node: Node, trail: tuple[Node, ...] = ()) -> Iterator[tuple[str, tuple[Node, ...]]]:
    """Every non-blank text node under ``node`` with its chain of ancestors, skipping the
    ``<script>``/``<style>`` source and the ``.teacher`` layer (pupils never see it)."""
    for child in node.children:
        if isinstance(child, str):
            if child.strip():
                yield child, trail
        elif child.tag not in {"script", "style", "head"} and "teacher" not in child.classes:
            yield from text_nodes(child, (*trail, child))


def test_chrome_text_is_marked_ui_and_lesson_content_is_not(deck: Deck, lesson: Lesson) -> None:
    pupil_text = list(text_nodes(deck.root))

    def carriers(needle: str) -> list[tuple[Node, ...]]:
        return [trail for text, trail in pupil_text if needle.lower() in text.lower()]

    for chrome in ("Tap", "Start", "Grade"):
        found = carriers(chrome)
        assert found, chrome
        for trail in found:
            assert any("ui" in node.classes for node in trail), (chrome, trail[-1].classes)

    first_word = lesson.blocks["b_02"].content.items[0].word  # type: ignore[union-attr]
    for content in (first_word, "Hello, I'm Ha.", "Name chain", "Catch the ball"):
        found = carriers(content)
        assert found, content
        for trail in found:
            assert not any("ui" in node.classes for node in trail), (content, trail[-1].classes)


def test_interactive_chrome_elements_carry_ui(deck: Deck) -> None:
    for countdown in deck.root.find(class_="countdown"):
        assert "ui" in countdown.classes
        assert "ui" in countdown.one(class_="hint").classes
    assert "ui" in deck.slides()[0].one(class_="cover__meta").classes
    assert "ui" in deck.root.one(class_="dots").classes
    assert "className = 'dot ui'" in deck.root.one("script").text()
    for node in deck.root.find(class_="flashcard__word") + deck.root.find(class_="bubble__text"):
        assert "ui" not in node.classes


# --- contrast, sizes, hit targets --------------------------------------------------------------


def theme_tokens(css: str) -> dict[str, str]:
    root = re.search(r":root\s*\{(.*?)\}", css, re.DOTALL)
    assert root
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9A-Fa-f]{6})\s*;", root.group(1)))


def luminance(colour: str) -> float:
    channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# (foreground, background) pairs, each a theme token or a literal colour, for text on the stage.
TEXT_PAIRS = [
    ("--ink", "--bg"),
    ("--ink", "--card"),
    ("--ink", "--bubble-left"),
    ("--ink", "--bubble-right"),
    ("--ink", "--mark"),
    ("--muted", "--bg"),
    ("--muted", "--card"),
    ("--accent-ink", "--bg"),
    ("--accent-ink", "--card"),
    ("--accent-ink", "--bubble-left"),
    ("--badge", "--bg"),
    ("--badge", "--card"),
    ("#FFFFFF", "--accent-ink"),
    ("#FFFFFF", "--accent-2-ink"),
    ("#FFFFFF", "--badge"),
]


@pytest.mark.parametrize("theme", ["kids", "plain"])
def test_text_contrast_is_at_least_4_5_to_1(tmp_path: Path, lesson: Lesson, theme: str) -> None:
    deck = load(export_html(lesson, tmp_path / f"{theme}.html", theme=theme))
    tokens = theme_tokens(deck.root.find("style")[1].text())
    for foreground, background in TEXT_PAIRS:
        ratio = contrast(tokens.get(foreground, foreground), tokens[background])
        assert ratio >= 4.5, f"{theme}: {foreground} on {background} is {ratio:.2f}:1"


def test_accent_colours_are_not_used_as_text(deck: Deck) -> None:
    """The bright phase accents are for bands and borders; text uses the darker ``-ink`` token."""
    css = deck.root.find("style")[0].text() + deck.root.find("style")[1].text()
    for selector in (
        ".flashcard__word",
        ".phonics__sound",
        ".pattern__slot",
        ".countdown.running .countdown__time",
    ):
        rules = re.findall(re.escape(selector) + r"[^{]*\{([^}]*)\}", css)
        colours = [
            match.group(1)
            for rule in rules
            if (match := re.search(r"(?<![-\w])color:\s*(var\(--[\w-]+\))", rule))
        ]
        assert colours == ["var(--accent-ink)"], selector


def test_progress_dots_are_at_least_32px_hit_targets(deck: Deck) -> None:
    css = deck.root.find("style")[0].text()
    rule = re.search(r"\n\.dot \{([^}]*)\}", css)
    assert rule
    for dimension in ("width", "height"):
        size = re.search(rf"{dimension}:\s*(\d+)px", rule.group(1))
        assert size and int(size.group(1)) >= 32, dimension


def test_kids_theme_sizes_phase_bands_and_icons_follow_the_design_language(deck: Deck) -> None:
    css = "\n".join(style.text() for style in deck.root.find("style"))
    sizes = {name: int(px) for name, px in re.findall(r"--size-([\w-]+):\s*(\d+)px", css)}
    assert sizes["word"] == 96
    assert sizes["pattern"] == 56
    assert sizes["dialogue"] == 44
    assert sizes["body"] >= 32
    assert sizes["min"] >= 32
    assert "font-size: var(--size-word, 96px)" in css
    assert "background: var(--phase-color)" in css  # the band
    for phase in ("warm_up", "presentation", "practice", "production", "wrap_up"):
        assert re.search(
            rf'\.slide\[data-phase="{phase}"\][^{{]*\{{\s*--phase-color: var\(--phase-{phase}\)',
            css,
        )
    icons = set(re.findall(r'\.icon\[data-icon="(\w+)"\]::before', css))
    assert {"listen_and_do", "look_listen_repeat", "tpr", "other", "wrap_up"} <= icons
    assert len(icons) == 5 + 14


# --- robustness --------------------------------------------------------------------------------


def test_vocabulary_block_without_items_still_renders_a_carrier(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    block = lesson.blocks["b_02"]
    block.content.items = []  # type: ignore[union-attr]
    block.teacher_note = "Remember to drill."
    block.provenance = Provenance.GENERATED
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    carriers = deck.root.find(data_block_id="b_02")
    assert len(carriers) == 1
    assert carriers[0].attrs["data-kind"] == "vocabulary"
    slide = deck.slide_of("b_02")
    assert slide.one("footer").text().startswith("b_02")
    assert slide.one(class_="badge").text() == AI_ADDED_BADGE
    assert "Remember to drill." in slide.one(class_="notes").text()


def test_generated_objective_is_marked_and_keeps_its_teacher_note(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    block = lesson.blocks["b_01"]
    block.provenance = Provenance.GENERATED
    block.teacher_note = "Check the objective with the class."
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources))
    objective = deck.root.one(data_block_id="b_01")
    assert objective.attrs["data-provenance"] == "generated"
    assert f"Objective ({AI_ADDED_BADGE})" in objective.text()
    assert AI_ADDED_NOTE in objective.text()
    assert "Check the objective with the class." in objective.text()


def test_user_text_is_escaped_in_text_nodes_and_attributes(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    speaker = '"><script>alert(1)</script>'
    word = "<img src=x onerror=alert(1)>"
    pattern = 'Say "___" <now> & go.'
    example = 'Say "stop" <now> & go.'
    step = 'Click <b>here</b> & "there"'
    caption = "<i>Hello</i> & 'bye'"
    alt = 'alt "quoted" <tag>'

    item = lesson.blocks["b_02"].content.items[0]  # type: ignore[union-attr]
    item.word = word
    item.meaning_vi = "<u>x</u>"
    line = lesson.blocks["b_05"].content.lines[0]  # type: ignore[union-attr]
    line.speaker = speaker
    line.text = "<b>Hi</b> & bye"
    lesson.blocks["b_03"].content.pattern = pattern  # type: ignore[union-attr]
    lesson.blocks["b_03"].content.examples = [example]  # type: ignore[union-attr]
    lesson.blocks["b_07"].content.student_steps = [step]  # type: ignore[union-attr]
    lesson.blocks["b_09"].content.caption = caption  # type: ignore[union-attr]
    lesson.blocks["b_09"].content.alt = alt  # type: ignore[union-attr]

    path = export_html(lesson, tmp_path / "x.html", sources=sources, image_root=image_root)
    deck = load(path)

    assert len(deck.root.find("script")) == 1
    for raw in ("<img src=x", "<b>", "<u>", "<i>"):
        assert raw not in deck.text
    assert deck.root.find("b") == [] and deck.root.find("u") == [] and deck.root.find("i") == []

    flashcard = deck.root.find(class_="flashcard", data_block_id="b_02")[0]
    assert flashcard.one(class_="flashcard__word").text() == word
    assert flashcard.one("img").attrs["alt"] == word
    assert flashcard.one(class_="flashcard__meaning").text() == "<u>x</u>"

    bubble = deck.slide_of("b_05").find(class_="bubble")[0]
    assert bubble.attrs["data-speaker"] == speaker
    assert bubble.one(class_="bubble__text").text() == f"{speaker}: <b>Hi</b> & bye"

    pattern_card = deck.slide_of("b_03").one(class_="pattern")
    assert pattern_card.one(class_="pattern__text").text() == pattern
    assert pattern_card.one(class_="pattern__slot").attrs["data-empty"] == "___"
    assert pattern_card.one(class_="pattern__example").text() == example
    assert json.loads(pattern_card.one(class_="pattern__example").attrs["data-fills"] or "") == [
        "stop"
    ]

    assert [s.text() for s in deck.slide_of("b_07").find(class_="step__text")] == [step]

    figure = deck.slide_of("b_09").one(class_="image-ref")
    assert figure.one("figcaption").text() == caption
    assert figure.one("img").attrs["alt"] == alt


def test_dialogue_shows_the_first_line_and_hides_the_hint_when_nothing_is_left(
    tmp_path: Path, lesson: Lesson
) -> None:
    lines = lesson.blocks["b_05"].content.lines  # type: ignore[union-attr]
    lines.append(lines[0].model_copy(update={"text": "Bye!"}))
    deck = load(export_html(lesson, tmp_path / "x.html"))
    chat = deck.slide_of("b_05").one(class_="dialogue")
    bubbles = chat.find(class_="bubble")
    assert ["pending" in b.classes for b in bubbles] == [False, True, True]
    css = deck.root.find("style")[0].text()
    # pending hides the text only: both speakers' avatars stay on screen
    assert re.search(r"\.bubble\.pending \.bubble__text \{[^}]*opacity: 0", css)
    assert re.search(r"\.bubble\.pending \.bubble__text,[^{]*\{ visibility: hidden", css)
    assert not re.search(r"\.bubble\.pending \{[^}]*opacity: 0", css)
    assert ".stepper.complete .dialogue__hint { visibility: hidden; }" in css
    script = deck.root.one("script").text()
    assert "if (n > 0) item.classList.add('pending')" in script

    lines.pop()
    lines.pop()
    single = load(export_html(lesson, tmp_path / "y.html"))
    only = single.slide_of("b_05").one(class_="dialogue")
    assert "complete" in only.classes
    assert [("pending" in b.classes) for b in only.find(class_="bubble")] == [False]


def test_flashcard_without_meaning_has_no_reveal_and_a_bare_card_is_centred(
    tmp_path: Path, lesson: Lesson
) -> None:
    items = lesson.blocks["b_02"].content.items  # type: ignore[union-attr]
    for item in items:
        item.meaning_vi = None
    deck = load(export_html(lesson, tmp_path / "x.html"))
    cards = deck.root.find(class_="flashcard", data_block_id="b_02")
    assert len(cards) == 2
    for card in cards:
        assert {"tap", "reveal"}.isdisjoint(card.classes)
        assert "bare" in card.classes
        assert card.find(class_="hint") == [] and card.find(class_="flashcard__meaning") == []
        assert "role" not in card.attrs and "tabindex" not in card.attrs
    css = "\n".join(style.text() for style in deck.root.find("style"))
    assert re.search(
        r"\.flashcard\.bare \.flashcard__word \{[^}]*var\(--size-word-bare, 120px\)", css
    )
    assert re.search(r"\.flashcard \{[^}]*justify-content: center", css)


def test_flashcard_with_a_picture_or_a_meaning_is_not_bare(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    items = lesson.blocks["b_02"].content.items  # type: ignore[union-attr]
    items[0].meaning_vi = None  # picture only
    items[1].meaning_vi = "chào"  # meaning only
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources, image_root=image_root))
    first, second = deck.root.find(class_="flashcard", data_block_id="b_02")
    assert "bare" not in first.classes and "has-picture" in first.classes
    assert first.find(class_="hint") == []
    assert "bare" not in second.classes and "reveal" in second.classes


def test_generated_picture_is_embedded_with_a_teacher_caption(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    from classdeck.illustrate import illustrate
    from classdeck.llm.images import MockImageProvider

    report = illustrate(lesson, provider=MockImageProvider(), image_root=image_root)
    path = export_html(report.lesson, tmp_path / "gen.html", sources=sources, image_root=image_root)
    deck = load(path)
    second = deck.slide_of("b_02", 1)
    image = second.one("img")
    assert image.attrs["data-block-id"] == "b_02"
    assert image.attrs["data-item"] == "1"
    assert image.attrs["data-generated"] == "true"
    assert (image.attrs["src"] or "").startswith("data:image/png;base64,")
    caption = second.one(class_="flashcard__ai")
    assert "teacher" in caption.classes
    assert caption.text() == "AI picture"
    # The cited textbook picture is not marked as generated.
    first = deck.slide_of("b_02")
    assert "data-generated" not in first.one("img").attrs
    assert first.find(class_="flashcard__ai") == []
    assert "http://" not in path.read_text(encoding="utf-8")


def test_generated_picture_outside_image_root_is_ignored(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    block = lesson.blocks["b_02"]
    block.content.items[1].generated_picture = "../outside.png"
    (tmp_path / "outside.png").write_bytes(write_png(tmp_path / "scratch").read_bytes())
    deck = load(export_html(lesson, tmp_path / "x.html", sources=sources, image_root=image_root))
    assert deck.slide_of("b_02", 1).find("img") == []


# --- theme hooks -------------------------------------------------------------------------------


def test_every_slide_has_type_class_phase_kind_and_three_decorations(deck: Deck) -> None:
    for slide in deck.slides():
        slide_type = slide.attrs["data-slide-type"]
        assert {"slide", f"slide--{slide_type}"} <= slide.classes
        assert slide.attrs["data-phase"]
        assert slide.attrs["data-kind"]
        decorations = slide.find("div", "deco")
        assert [d.attrs["class"] for d in decorations] == [
            "deco deco--1",
            "deco deco--2",
            "deco deco--3",
        ]
        for decoration in decorations:
            assert decoration.attrs["aria-hidden"] == "true"
            assert decoration.children == []
    kinds = {s.attrs["data-slide-type"]: s.attrs["data-kind"] for s in deck.slides()}
    assert kinds["flashcard"] == "vocabulary"
    assert kinds["pattern"] == "sentence_pattern"
    assert kinds["activity"] == "activity"
    assert kinds["cover"] == "cover"


THEMES_README = Path(__file__).parents[1] / "src" / "classdeck" / "export" / "themes" / "README.md"


def test_every_class_the_markup_emits_is_documented_in_the_themes_readme(deck: Deck) -> None:
    readme = THEMES_README.read_text(encoding="utf-8")
    emitted: set[str] = set()
    for node in deck.root.walk():
        emitted |= node.classes
    emitted |= {"dot", "dots", "current", "active", "pending"}  # made or toggled by the script
    undocumented = sorted(c for c in emitted if f".{c}" not in readme)
    assert undocumented == []


# --- manifests and theme files -----------------------------------------------------------------


def test_parse_manifest() -> None:
    info = parse_manifest(
        "/* theme: sunny-2 | Sunny | Bold and flat | suits: grades 3-4 */\nbody{}"
    )
    assert (info.id, info.label, info.description, info.suits) == (
        "sunny-2",
        "Sunny",
        "Bold and flat",
        "grades 3-4",
    )
    for bad in (
        "body {}",
        "/* theme: x | only two | parts */",
        "/* a comment */\n/* theme: x | X | y | suits: z */",
        "/* theme: Bad Id | X | y | suits: z */",
    ):
        with pytest.raises(ValueError, match="manifest"):
            parse_manifest(bad)


REQUIRED_TOKENS = [
    "--bg", "--ink", "--muted", "--card", "--line", "--accent", "--accent-2", "--accent-ink",
    "--accent-2-ink", "--badge", "--mark", "--bubble-left", "--bubble-right", "--slot-border",
    "--teacher-bg", "--pulse", "--phase-warm_up", "--phase-presentation", "--phase-practice",
    "--phase-production", "--phase-wrap_up", "--phase-lesson", "--phase-teacher", "--radius",
    "--shadow",
]  # fmt: skip


@pytest.mark.parametrize("theme", theme_ids())
def test_every_theme_follows_the_rules(theme: str, tmp_path: Path, lesson: Lesson) -> None:
    deck = load(export_html(lesson, tmp_path / "x.html", theme=theme))
    css = deck.root.find("style")[-1].text()
    assert re.match(rf"\s*/\* --- theme: {theme} -*\s*\*/", css)
    assert "url(" not in css.lower()
    assert "@import" not in css.lower()
    assert "http" not in css.lower()
    declared = set(re.findall(r"(--[\w-]+)\s*:", css))
    assert [t for t in REQUIRED_TOKENS if t not in declared] == []
    sizes = {name: int(px) for name, px in re.findall(r"--size-([\w-]+):\s*(\d+)px", css)}
    for name, size in sizes.items():
        assert size >= 32, f"--size-{name} is {size}px"
    tokens = theme_tokens(css)
    for foreground, background in TEXT_PAIRS:
        ratio = contrast(tokens.get(foreground, foreground), tokens[background])
        assert ratio >= 4.5, f"{theme}: {foreground} on {background} is {ratio:.2f}:1"


# --- pronunciation audio -----------------------------------------------------------------------


def says(node: Node) -> list[str]:
    return [b.attrs["data-say"] or "" for b in node.find("button", "speak")]


def test_speak_buttons_are_chrome_with_label_and_no_text(deck: Deck) -> None:
    buttons = deck.root.find("button", "speak")
    assert buttons
    for button in buttons:
        assert {"speak", "ui"} <= button.classes
        assert button.attrs["aria-label"] == "Listen"
        assert button.attrs["type"] == "button"
        assert button.attrs["data-say"]
        assert button.text() == ""  # the glyph is drawn by CSS
    assert "\\1F50A" in deck.text


def test_flashcard_buttons_say_the_word_and_the_example(deck: Deck) -> None:
    first, second = deck.root.find(class_="flashcard", data_block_id="b_02")
    line = first.one(class_="flashcard__wordline")
    assert says(line) == ["hello"]
    assert line.one(class_="flashcard__word").text() == "hello"
    assert says(first.one(class_="flashcard__example")) == ["Hello, I'm Ha."]
    assert says(second) == ["hi", "Hi, Ha."]
    assert {"has-example"} <= first.classes & second.classes


def test_pattern_buttons_say_the_first_example_and_each_example(deck: Deck) -> None:
    pattern = deck.slide_of("b_03").one(class_="pattern")
    assert says(pattern.one(class_="pattern__head")) == ["Hello, I'm Ha."]
    assert says(pattern.one(class_="pattern__examples")) == ["Hello, I'm Ha."]


def test_pattern_without_examples_says_the_pattern_without_its_slot(
    tmp_path: Path, lesson: Lesson
) -> None:
    lesson.blocks["b_03"].content.examples = []  # type: ignore[union-attr]
    lesson.blocks["b_03"].content.pattern = "My name's ___, nice to meet you."  # type: ignore[union-attr]
    deck = load(export_html(lesson, tmp_path / "x.html"))
    pattern = deck.slide_of("b_03").one(class_="pattern")
    assert says(pattern) == ["My name's , nice to meet you."]


def test_dialogue_phonics_and_summary_buttons(deck: Deck) -> None:
    bubbles = deck.slide_of("b_05").find(class_="bubble")
    assert [says(b) for b in bubbles] == [["Hello, I'm Ha."], ["Hi, Ha. I'm Tien."]]
    words = deck.slide_of("b_06").one(class_="phonics__words").find("li")
    assert [says(w) for w in words] == [["hello"], ["hi"]]
    summary = deck.slide_of("b_08")
    assert [says(c) for c in summary.find(class_="summary-card")] == [["hello"], ["hi"]]
    assert says(summary.one(class_="summary__pattern")) == ["Hello, I'm Ha."]


def test_speak_buttons_never_sit_in_teacher_only_elements(deck: Deck) -> None:
    def walk(node: Node, inside_teacher: bool) -> Iterator[tuple[Node, bool]]:
        for child in node.children:
            if isinstance(child, Node):
                teacher = inside_teacher or "teacher" in child.classes
                yield child, teacher
                yield from walk(child, teacher)

    buttons = [(n, t) for n, t in walk(deck.root, False) if "speak" in n.classes]
    assert buttons
    assert not any(teacher for _, teacher in buttons)


def test_speech_script_prefers_british_english_and_hides_the_buttons_without_a_voice(
    deck: Deck,
) -> None:
    script = deck.root.one("script").text()
    for needle in (
        "speechSynthesis",
        "SpeechSynthesisUtterance",
        "voiceschanged",
        "getVoices",
        r"/^en[-_]GB$/i",
        r"/^en([-_]|$)/i",
        "no-speech",
        "0.85",
        "0.95",
        "synth.cancel()",
        ".flashcard__word",
        "event.stopPropagation()",
    ):
        assert needle in script, needle
    # a click on a speak button returns before the tap/reveal/navigation handling
    click = script[script.index("stage.addEventListener('click'") :]
    assert click.index("closest('.speak')") < click.index("closest('.tap')") < click.index("next()")
    css = deck.root.find("style")[0].text()
    assert ".no-speech .speak { display: none !important; }" in css
    assert re.search(r"@media print[\s\S]*\.speak", css)


def test_deck_listens_for_gallery_messages(deck: Deck) -> None:
    script = deck.root.one("script").text()
    for needle in ("'classdeck:go'", "'classdeck:next'", "'classdeck:prev'", "postMessage"):
        assert needle in script, needle


def test_default_theme_is_sunny(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    from classdeck.export.html import DEFAULT_THEME

    assert DEFAULT_THEME == "sunny"
    path = export_html(lesson, tmp_path / "default.html", sources=sources, image_root=image_root)
    assert "theme: sunny" in path.read_text(encoding="utf-8")
