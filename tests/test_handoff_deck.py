from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

import pytest

from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.english.validators import pupil_text_of
from classdeck.export import ExportBlocked
from classdeck.handoff import (
    ACTIVITY_EMOJI,
    NO_BLOCK,
    PHASE_STYLE,
    PICTURE_PLACEHOLDER_RULE,
    TEACHER_ONLY,
    DeckCheckResult,
    allowed_words,
    build_deck_pack,
    check_deck,
    listed_words,
    render_lesson_for_prompt,
)
from classdeck.handoff.deck_pack import BACKGROUND, INK
from classdeck.lesson import (
    ActivityType,
    Block,
    BlockAdapter,
    CheckResult,
    CheckStatus,
    Citation,
    DialogueBlock,
    Lesson,
    Phase,
    SourceDocument,
    VocabularyBlock,
)
from tests.test_export import CURRICULUM_ROOT, make_export_lesson, write_png
from tests.test_lesson_schema import DOC_ID, IPA_HELLO, make_source_document

PACK_LIMIT = 30_000
PHASE_HEX = {"#F9C74F", "#4D96FF", "#6BCB77", "#FF6B6B", "#9D4EDD"}


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


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 3)


@pytest.fixture
def pack(lesson: Lesson, sources: dict[str, SourceDocument], curriculum: Curriculum) -> str:
    return build_deck_pack(lesson, sources=sources, curriculum=curriculum)


# --- synthetic decks ---------------------------------------------------------------------------


def slide(block_id: str, body: str = "", *, item: int | None = None, teacher: str = "") -> str:
    attrs = f'data-block-id="{block_id}"' + (f' data-item="{item}"' if item is not None else "")
    notes = f'<div class="teacher"><p>{teacher}</p></div>' if teacher else ""
    return f"<section {attrs}>{body}{notes}</section>"


def pupil_body(block: Block) -> str:
    texts = list(pupil_text_of(block))
    if isinstance(block, VocabularyBlock):
        texts += [f"{item.word} {item.ipa} {item.meaning_vi}" for item in block.content.items]
    return "".join(f"<p>{html_lib.escape(text)}</p>" for text in texts)


def block_slides(lesson: Lesson) -> list[str]:
    """One element per block, shaped like the spec's slide types."""
    slides = []
    for block in lesson.blocks.values():
        if block.kind in ("objective", "teacher_note"):
            slides.append(
                f'<div class="teacher" data-block-id="{block.id}">'
                f"{pupil_body(block)}Photosynthesis</div>"
            )
            continue
        body = pupil_body(block)
        if isinstance(block, DialogueBlock):
            avatars = "".join(
                f'<span class="avatar">{line.speaker[:1]}</span>' for line in block.content.lines
            )
            body = avatars + body
        slides.append(slide(block.id, body, teacher="Model the chain; Source: p. 6, ¶3"))
    return slides


def deck_html(
    lesson: Lesson,
    slides: list[str] | None = None,
    *,
    doctype: bool = True,
    head: str = "",
    tail: str = "",
) -> str:
    cover = (
        f"<section class='cover'><h1>{lesson.title}</h1><p>Grade {lesson.grade}</p>"
        "<button class='ui'>Start</button></section>"
    )
    dividers = "".join(
        f"<section class='divider'><h2>{s.title}</h2><p>{s.minutes} min</p>"
        "<span class='ui'>Tap to start</span></section>"
        for s in lesson.sections
    )
    body = "".join(block_slides(lesson) if slides is None else slides)
    return (
        ("<!doctype html>\n" if doctype else "")
        + "<html><head><meta charset='utf-8'><title>Deck</title>"
        "<style>body { font: 32px system-ui; }</style>"
        f"{head}</head><body>{cover}{dividers}{body}{tail}"
        "<script>document.body.dataset.ready = 'photosynthesis';</script></body></html>"
    )


def placeholder(block_id: str, item: int, alt: str, src: str | None = None) -> str:
    source = f' src="{src}"' if src is not None else ""
    return f'<img class="pic" data-block-id="{block_id}" data-item="{item}" alt="{alt}"{source}>'


# --- allowed words -----------------------------------------------------------------------------


def test_allowed_words_union_grade_list_vocabulary_and_pupil_text(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    words = allowed_words(lesson, curriculum)
    assert words == sorted(words)
    assert len(words) == len(set(words))
    assert {"hello", "hi", "apple", "red"} <= set(words)  # grade list and vocabulary
    assert {"pass", "ball", "catch", "point"} <= set(words)  # pupil text of activities
    assert "photosynthesis" not in words
    assert "model" not in words  # teacher steps are not pupil text


def test_allowed_words_without_a_curriculum_still_hold_the_lesson_words(lesson: Lesson) -> None:
    words = allowed_words(lesson)
    assert "hello" in words
    assert "apple" not in words


def test_allowed_words_fall_back_to_the_grade_list_for_an_unknown_unit(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    lesson.curriculum.unit_id = "ta3.u99"
    assert "apple" in allowed_words(lesson, curriculum)


# --- the rendered lesson -----------------------------------------------------------------------


def test_render_lists_every_block_under_its_section(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    text = render_lesson_for_prompt(lesson, sources=sources)
    for block in lesson.blocks.values():
        assert f"### {block.id} · {block.kind} · " in text
    for section in lesson.sections:
        assert f"· {section.title} · {section.minutes} min" in text
    assert text.index("### b_04") < text.index("### b_01") < text.index("### b_08")


def test_render_block_lines(lesson: Lesson, sources: dict[str, SourceDocument]) -> None:
    text = render_lesson_for_prompt(lesson, sources=sources)
    assert f"- hello {IPA_HELLO} — xin chào (picture: data-item 0, p. 6, ¶4)" in text
    assert "- hi /haɪ/ — chào\n" in text  # noqa: RUF001
    assert "- Ha: Hello, I'm Ha." in text
    assert "- pattern: Hello, I'm ___." in text
    assert "- function: Introduce yourself" in text
    assert "- sound: /h/ · letters: h" in text
    assert "- picture: data-item 0, p. 6, ¶4 · alt: Two children waving" in text
    assert "- caption: Ha and Tien say hello." in text
    assert "### b_02 · vocabulary · Source · p. 6, ¶3" in text
    assert "### b_07 · activity · AI-added · Derived from b_03" in text
    assert "### b_08 · summary · Derived · Derived from b_02, b_07" in text


def test_render_keeps_teacher_material_under_the_marker(lesson: Lesson) -> None:
    text = render_lesson_for_prompt(lesson)
    activity = text[text.index("### b_07") : text.index("### b_11")]
    pupil, teacher = activity.split(f"- {TEACHER_ONLY}")
    assert "1. Catch the ball, say 'Hello, I'm (name).'" in pupil
    assert "Model the chain with two pupils." not in pupil
    assert "1. Model the chain with two pupils." in teacher
    assert "materials: a soft ball" in teacher
    assert "note: If the class is large, run two chains in parallel." in teacher
    objective = text[text.index("### b_01") : text.index("### b_02")]
    assert f"- {TEACHER_ONLY}\n  - objective: Pupils can greet" in objective
    note = text[text.index("### b_11") : text.index("## Section 5")]
    assert f"- {TEACHER_ONLY}\n  - note: Praise effort" in note


def test_render_includes_unplaced_blocks_and_warnings(lesson: Lesson) -> None:
    lesson.blocks["b_12"] = BlockAdapter.validate_python(
        {"id": "b_12", "kind": "summary", "content": {"bullets": ["bye"]}}
    )
    lesson.blocks["b_02"].checks.append(
        CheckResult(name="english.vocab_level", status=CheckStatus.WARN, detail="1 off-list word")
    )
    text = render_lesson_for_prompt(lesson)
    assert "## Blocks not placed in a section\n\n### b_12 · summary" in text
    assert "## Warnings" in text
    assert "- b_02 — english.vocab_level: 1 off-list word" in text


def test_render_flags_a_picture_citation_that_is_not_an_image_span(lesson: Lesson) -> None:
    document = make_source_document()
    document.pages[0].spans[3].kind = "paragraph"  # type: ignore[assignment]
    text = render_lesson_for_prompt(lesson, sources={DOC_ID: document})
    assert "(picture: cited at p. 6, ¶4 but no image span found)" in text


# --- the deck pack -----------------------------------------------------------------------------


def test_pack_has_the_five_parts_in_order(pack: str) -> None:
    headings = [line for line in pack.splitlines() if re.match(r"## \d\. ", line)]
    assert headings == [
        "## 1. Role and audience",
        "## 2. Design brief",
        "## 3. The lesson",
        "## 4. Allowed words",
        "## 5. Hard rules",
    ]
    assert "grade 3" in pack
    assert "35-minute period" in pack
    assert "aged 8 to 10" in pack


def test_pack_contains_every_block_id_and_formatted_citations(pack: str, lesson: Lesson) -> None:
    for block_id in lesson.blocks:
        assert f"### {block_id} · " in pack
    assert "p. 6, ¶3" in pack
    assert "p. 6, ¶6" in pack
    assert TEACHER_ONLY in pack


def test_pack_design_brief_carries_colours_sizes_icons_and_slide_types(pack: str) -> None:
    for colour in PHASE_HEX | {BACKGROUND, INK}:
        assert colour in pack
    assert {colour for _, colour in PHASE_STYLE.values()} == PHASE_HEX
    for size in ("24 px", "32 px", "96 px", "56 px", "44 px"):
        assert size in pack
    for kind in ActivityType:
        assert f"{ACTIVITY_EMOJI[kind]} {kind.value}" in pack
    assert "| vocabulary | one flashcard per item" in pack
    assert "| dialogue | chat bubbles" in pack
    assert 'class="teacher"' in pack
    assert "data-block-id" in pack and "data-kind" in pack and "data-provenance" in pack
    assert PICTURE_PLACEHOLDER_RULE in pack
    assert "arrow keys" in pack
    assert "System font stack" in pack
    assert "all CSS and JavaScript inline" in pack
    for phase in Phase:
        assert f"({phase.value})" in pack


def test_pack_allowed_words_line_and_rule(
    pack: str, lesson: Lesson, curriculum: Curriculum
) -> None:
    part = pack[pack.index("## 4. Allowed words") : pack.index("## 5. Hard rules")]
    line = next(line for line in part.splitlines() if ", " in line and "hello" in line)
    assert line == ", ".join(listed_words(lesson, curriculum))
    assert set(line.split(", ")) == set(curriculum.word_list(lesson.curriculum.unit_id)) | {
        item.word.lower() for item in lesson.blocks["b_02"].content.items
    }
    assert "classroom language" in part
    assert "pupil-facing text in part 3" in part
    assert "English only on slides; use no other words except names and numbers." in part


def test_pack_hard_rules(pack: str) -> None:
    rules = pack[pack.index("## 5. Hard rules") :]
    assert "exactly once as data-block-id" in rules
    assert "One slide per block" in rules
    assert 'class="teacher"' in rules
    assert "<!doctype html>" in rules
    assert "ONE complete HTML file" in rules
    assert "English only on slides" in rules


def test_pack_is_short_and_deterministic(
    pack: str, lesson: Lesson, sources: dict[str, SourceDocument], curriculum: Curriculum
) -> None:
    assert len(pack) < PACK_LIMIT
    assert pack == build_deck_pack(lesson, sources=sources, curriculum=curriculum)
    assert pack.startswith("# Deck pack — ")
    assert "## 3. The lesson" in build_deck_pack(lesson)


def test_pack_appends_theme_notes_and_curriculum_period(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    pack = build_deck_pack(lesson, curriculum=curriculum, theme_notes="  Use lots of animals.  ")
    assert "Teacher's notes on look and feel: Use lots of animals." in pack
    assert "Teacher's notes on look and feel" not in build_deck_pack(lesson, theme_notes="  ")
    assert f"one {curriculum.period_minutes}-minute period" in pack


# --- check_deck: block ids ---------------------------------------------------------------------


def test_clean_deck_passes(lesson: Lesson, curriculum: Curriculum) -> None:
    result = check_deck(lesson, deck_html(lesson), curriculum=curriculum)
    assert isinstance(result, DeckCheckResult)
    assert result.missing_block_ids == []
    assert result.extra_block_ids == []
    assert result.duplicate_block_ids == []
    assert result.out_of_list_words == {}
    assert result.external_refs == []
    assert result.images_unresolved == []
    assert result.images_embedded == 0
    assert result.has_doctype is True
    assert result.ok is True
    assert result.report() == "OK"
    assert result.html == deck_html(lesson)


def test_missing_block_id_is_reported_in_lesson_order(lesson: Lesson) -> None:
    slides = [s for s in block_slides(lesson) if 'data-block-id="b_03"' not in s]
    slides = [s for s in slides if 'data-block-id="b_01"' not in s]
    result = check_deck(lesson, deck_html(lesson, slides))
    assert result.missing_block_ids == ["b_01", "b_03"]
    assert result.ok is False
    assert "Missing block ids: b_01, b_03" in result.report()


def test_unknown_block_id_is_extra(lesson: Lesson) -> None:
    result = check_deck(
        lesson, deck_html(lesson, [*block_slides(lesson), slide("b_99", "<p>hi</p>")])
    )
    assert result.extra_block_ids == ["b_99"]
    assert result.missing_block_ids == []
    assert "Unknown block ids: b_99" in result.report()


def test_repeated_block_id_is_a_duplicate(lesson: Lesson) -> None:
    result = check_deck(lesson, deck_html(lesson, [*block_slides(lesson), slide("b_02")]))
    assert result.duplicate_block_ids == ["b_02"]
    assert "Duplicate block ids: b_02" in result.report()


def test_flashcards_with_distinct_items_are_not_duplicates(lesson: Lesson) -> None:
    slides = [s for s in block_slides(lesson) if 'data-block-id="b_02"' not in s]
    slides += [slide("b_02", "<p>hello</p>", item=0), slide("b_02", "<p>hi</p>", item=1)]
    assert check_deck(lesson, deck_html(lesson, slides)).duplicate_block_ids == []
    slides.append(slide("b_02", "<p>hello</p>", item=0))
    assert check_deck(lesson, deck_html(lesson, slides)).duplicate_block_ids == ["b_02"]


def test_img_placeholders_do_not_count_as_block_occurrences(lesson: Lesson) -> None:
    slides = [*block_slides(lesson), placeholder("b_02", 0, "hello"), placeholder("b_77", 0, "x")]
    result = check_deck(lesson, deck_html(lesson, slides))
    assert result.duplicate_block_ids == []
    assert result.extra_block_ids == []
    assert any(entry.startswith("b_77 item 0: unknown block") for entry in result.images_unresolved)


def test_unplaced_blocks_are_expected_too(lesson: Lesson) -> None:
    lesson.blocks["b_12"] = BlockAdapter.validate_python(
        {"id": "b_12", "kind": "summary", "content": {"bullets": ["bye"]}}
    )
    result = check_deck(lesson, deck_html(lesson, block_slides(make_export_lesson())))
    assert result.missing_block_ids == ["b_12"]


# --- check_deck: words -------------------------------------------------------------------------


def test_out_of_list_word_is_reported_under_its_block(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    slides = [
        slide("b_05", "<p>Hello, Ha. Photosynthesis is <b>fun</b>.</p>")
        if 'data-block-id="b_05"' in s
        else s
        for s in block_slides(lesson)
    ]
    result = check_deck(lesson, deck_html(lesson, slides), curriculum=curriculum)
    assert result.out_of_list_words == {"b_05": ["fun", "photosynthesis"]}
    assert "Out-of-list words in b_05: fun, photosynthesis" in result.report()


def test_words_inside_teacher_elements_are_not_checked(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    teacher = (
        '<div class="notes teacher"><ul><li>Photosynthesis <em>chlorophyll</em></li></ul></div>'
    )
    slides = [
        slide("b_05", f"<p>Hello.</p>{teacher}") if 'data-block-id="b_05"' in s else s
        for s in block_slides(lesson)
    ]
    assert (
        check_deck(lesson, deck_html(lesson, slides), curriculum=curriculum).out_of_list_words == {}
    )


def test_names_numbers_function_words_and_lemmas_are_allowed(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    body = "<p>Hello, Mai and Duc. We have 10 balls. They are passing it.</p><p>Ha</p>"
    slides = [
        slide("b_05", body) if 'data-block-id="b_05"' in s else s for s in block_slides(lesson)
    ]
    assert (
        check_deck(lesson, deck_html(lesson, slides), curriculum=curriculum).out_of_list_words == {}
    )


def test_text_outside_any_block_is_reported_under_no_block(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    deck = deck_html(lesson, tail="<footer><p>Press any key for photosynthesis</p></footer>")
    result = check_deck(lesson, deck, curriculum=curriculum)
    # "any" and "for" are function words, which the level checks never report either
    assert result.out_of_list_words == {NO_BLOCK: ["key", "photosynthesis", "press"]}


def test_without_a_curriculum_the_lesson_words_are_still_allowed(lesson: Lesson) -> None:
    result = check_deck(lesson, deck_html(lesson))
    assert result.out_of_list_words == {}


def test_nested_block_ids_group_text_by_the_nearest_block(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    nested = (
        '<section data-block-id="b_03"><p>Hello</p>'
        '<div data-block-id="b_05"><p>photosynthesis</p></div><p>chlorophyll</p></section>'
    )
    slides = [
        s
        for s in block_slides(lesson)
        if 'data-block-id="b_03"' not in s and 'data-block-id="b_05"' not in s
    ]
    result = check_deck(lesson, deck_html(lesson, [*slides, nested]), curriculum=curriculum)
    assert result.out_of_list_words == {"b_05": ["photosynthesis"], "b_03": ["chlorophyll"]}


# --- check_deck: external references -----------------------------------------------------------


def test_external_src_and_href_are_flagged(lesson: Lesson) -> None:
    head = (
        '<link rel="stylesheet" href="https://fonts.example/css">'
        '<script src="//cdn.example/x.js"></script>'
    )
    tail = (
        '<img src="https://pics.example/hello.png" alt="hello">'
        '<a href="http://example.com/page">x</a>'
        '<img src="data:image/png;base64,AAAA" alt="ok"><a href="#slide-2">next</a>'
        '<div style="background: url(https://bg.example/a.png)"></div>'
        "<style>@import 'https://fonts.example/f.css'; "
        '.a { background: url("https://b.example/c.png"); }</style>'
    )
    result = check_deck(lesson, deck_html(lesson, head=head, tail=tail))
    assert result.external_refs == [
        "https://fonts.example/css",
        "//cdn.example/x.js",
        "https://pics.example/hello.png",
        "http://example.com/page",
        "https://bg.example/a.png",
        "https://fonts.example/f.css",
        "https://b.example/c.png",
    ]
    assert "External references: https://fonts.example/css" in result.report()


# --- check_deck: pictures ----------------------------------------------------------------------


def test_placeholder_is_replaced_by_a_data_uri(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    tag = placeholder("b_02", 0, "hello")
    deck = deck_html(lesson, [*block_slides(lesson), tag])
    result = check_deck(lesson, deck, sources=sources, image_root=image_root)
    assert result.images_embedded == 1
    assert result.images_unresolved == []
    assert tag not in result.html
    match = re.search(
        r'<img class="pic" data-block-id="b_02" data-item="0" alt="hello" src="([^"]+)">',
        result.html,
    )
    assert match is not None
    assert match.group(1).startswith("data:image/png;base64,")
    assert result.html.replace(match.group(0), tag) == deck
    assert result.report() == "OK (pictures embedded: 1)"
    assert result.ok is True


def test_placeholder_with_a_relative_or_external_src_is_replaced_and_data_src_is_kept(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    relative = placeholder("b_02", 0, "hello", src="hello.png")
    external = placeholder("b_09", 0, "waving", src="https://pics.example/wave.png")
    kept = placeholder("b_02", 0, "hello", src="data:image/gif;base64,R0lGODlh")
    deck = deck_html(lesson, [*block_slides(lesson), relative, external, kept])
    result = check_deck(lesson, deck, sources=sources, image_root=image_root)
    assert result.images_embedded == 2
    assert result.html.count("data:image/png;base64,") == 2
    assert kept in result.html
    assert 'src="hello.png"' not in result.html
    assert "https://pics.example/wave.png" not in result.html
    assert result.external_refs == ["https://pics.example/wave.png"]


def test_self_closing_and_single_quoted_placeholders(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    tags = [
        "<img data-block-id='b_02' data-item='0' alt='hello' />",
        '<img alt="wave" data-item="0" data-block-id="b_09"/>',
    ]
    result = check_deck(
        lesson,
        deck_html(lesson, [*block_slides(lesson), *tags]),
        sources=sources,
        image_root=image_root,
    )
    assert result.images_embedded == 2
    assert re.search(
        r'<img data-block-id="b_02" data-item="0" alt="hello" src="data:image/png;base64,[^"]+" />',
        result.html,
    )
    assert re.search(
        r'<img alt="wave" data-item="0" data-block-id="b_09" src="data:image/png;base64,[^"]+" />',
        result.html,
    )


def test_unresolvable_placeholders_are_listed(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path, tmp_path: Path
) -> None:
    tags = [
        placeholder("b_02", 1, "hi"),
        placeholder("b_02", 5, "x"),
        placeholder("b_03", 0, "pattern"),
        '<img data-block-id="b_02" data-item="one" alt="hello">',
        '<img data-block-id="b_09" alt="wave">',
    ]
    deck = deck_html(lesson, [*block_slides(lesson), *tags])
    result = check_deck(lesson, deck, sources=sources, image_root=image_root)
    assert result.images_embedded == 1  # b_09 without data-item defaults to item 0
    assert result.images_unresolved == [
        "b_02 item 1: the item cites no picture",
        "b_02 item 5: the block has no item 5",
        "b_03 item 0: sentence_pattern blocks carry no picture",
        "b_02 item one: data-item 'one' is not an index",
    ]
    assert "Pictures not embedded: b_02 item 1: the item cites no picture;" in result.report()
    assert result.ok is False

    without_root = check_deck(
        lesson, deck_html(lesson, [placeholder("b_02", 0, "hello")]), sources=sources
    )
    assert without_root.images_unresolved == [
        "b_02 item 0: sources and image_root are needed to embed pictures"
    ]
    missing_file = check_deck(
        lesson,
        deck_html(lesson, [placeholder("b_02", 0, "hello")]),
        sources=sources,
        image_root=tmp_path / "empty",
    )
    assert missing_file.images_embedded == 0
    assert "not found" in missing_file.images_unresolved[0]


def test_image_path_cannot_escape_image_root(
    lesson: Lesson, image_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.png"
    outside.write_bytes(write_png(tmp_path / "scratch").read_bytes())
    document = make_source_document()
    document.pages[0].spans[3].image_path = "../outside.png"
    deck = deck_html(lesson, [placeholder("b_02", 0, "hello")])
    result = check_deck(lesson, deck, sources={DOC_ID: document}, image_root=image_root)
    assert result.images_embedded == 0
    assert result.images_unresolved == [
        "b_02 item 0: image path '../outside.png' escapes image_root"
    ]
    assert "data:image" not in result.html


# --- check_deck: robustness and report ---------------------------------------------------------


def test_malformed_html_does_not_raise(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    deck = (
        "<!DOCTYPE html><html><body><section data-block-id='b_02'><div class=teacher><p>Model it"
        "<section data-block-id=b_03><p>Hello, I'm <b>Ha</section></p></div>"
        + placeholder("b_02", 0, "hello")
        + "<p>photosynthesis</span></em>< not a tag <section data-block-id='b_05'><p>Hi"
    )
    result = check_deck(lesson, deck, sources=sources, image_root=image_root)
    assert result.has_doctype is True
    assert "b_02" not in result.missing_block_ids
    assert "b_03" not in result.missing_block_ids
    assert "b_05" not in result.missing_block_ids
    assert result.images_embedded == 1
    # "</section>" closed b_03, so the stray text belongs to the still-open b_02 section
    assert result.out_of_list_words == {"b_02": ["not", "photosynthesis", "tag"]}


def test_empty_and_non_html_input(lesson: Lesson) -> None:
    for text in ("", "just some words", "<<<>>>", "<!doctype html>"):
        result = check_deck(lesson, text)
        assert result.missing_block_ids == [block.id for block in lesson.ordered_blocks()]
        assert result.html == text


def test_missing_doctype_is_an_issue(lesson: Lesson) -> None:
    result = check_deck(lesson, deck_html(lesson, doctype=False))
    assert result.has_doctype is False
    assert result.ok is False
    assert result.report() == "No <!doctype html> declaration"


def test_report_lists_every_issue(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path, curriculum: Curriculum
) -> None:
    slides = [s for s in block_slides(lesson) if 'data-block-id="b_03"' not in s]
    slides += [
        slide("b_99", "<p>photosynthesis</p>"),
        slide("b_02"),
        placeholder("b_02", 0, "hello"),
        placeholder("b_02", 1, "hi"),
        '<a href="https://example.com">hello</a>',
    ]
    result = check_deck(
        lesson,
        deck_html(lesson, slides, doctype=False),
        sources=sources,
        image_root=image_root,
        curriculum=curriculum,
    )
    report = result.report()
    assert report.splitlines() == [
        "Missing block ids: b_03",
        "Unknown block ids: b_99",
        "Duplicate block ids: b_02",
        "Out-of-list words in b_99: photosynthesis",
        "External references: https://example.com",
        "Pictures not embedded: b_02 item 1: the item cites no picture",
        "No <!doctype html> declaration",
        "Pictures embedded: 1",
    ]
    assert result.ok is False


# --- review findings ---------------------------------------------------------------------------


def test_pack_is_refused_for_a_lesson_with_a_failed_check(lesson: Lesson) -> None:
    failure = CheckResult(name="english.steps_present", status=CheckStatus.FAIL, detail="no steps")
    lesson.blocks["b_07"].checks.append(failure)
    with pytest.raises(ExportBlocked) as raised:
        build_deck_pack(lesson)
    assert raised.value.failures == [("b_07", failure)]


def test_pack_lists_warnings_but_is_not_blocked_by_them(lesson: Lesson) -> None:
    lesson.blocks["b_02"].checks.append(
        CheckResult(name="english.vocab_level", status=CheckStatus.WARN, detail="1 off-list word")
    )
    assert "- b_02 — english.vocab_level: 1 off-list word" in build_deck_pack(lesson)


def test_pack_asks_for_class_ui_and_explains_the_allowed_words(pack: str) -> None:
    assert 'class="ui"' in pack
    assert 'Mark your own interface text with class="ui"' in pack
    assert "the classroom language" in pack
    assert "is allowed as well" in pack


def test_a_multi_line_teacher_note_stays_in_the_teacher_group(lesson: Lesson) -> None:
    lesson.blocks["b_07"].teacher_note = "Drill chorally.\n### b_99 · activity · Source · p. 1"
    lesson.blocks["b_11"].content.text = "Praise effort.\nSecond line of the note."
    text = render_lesson_for_prompt(lesson)
    assert "\n### b_99" not in text
    assert "\nSecond line" not in text
    assert "  - note: Praise effort. Second line of the note.\n" in text
    assert "  - note: Drill chorally. ### b_99 · activity · Source · p. 1\n" in text


def test_activity_metadata_beyond_the_type_is_teacher_only(lesson: Lesson) -> None:
    text = render_lesson_for_prompt(lesson)
    activity = text[text.index("### b_07") : text.index("### b_11")]
    pupil, teacher = activity.split(f"- {TEACHER_ONLY}")
    assert "- type: " in pupil
    assert "interaction:" not in pupil and "minutes:" not in pupil
    assert "interaction:" in teacher and "minutes:" in teacher


def test_text_inside_ui_and_teacher_elements_is_not_checked(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    chrome = (
        '<div class="hud ui">Tap to reveal <button class="ui">Start now</button></div>'
        '<aside class="teacher"><p>Photosynthesis notes</p></aside>'
    )
    deck = deck_html(lesson, tail=chrome)
    assert check_deck(lesson, deck, curriculum=curriculum).out_of_list_words == {}
    # the same words outside those classes are reported
    plain = deck_html(lesson, tail="<footer><p>Tap to reveal photosynthesis</p></footer>")
    assert check_deck(lesson, plain, curriculum=curriculum).out_of_list_words == {
        NO_BLOCK: ["photosynthesis", "reveal"]
    }


def test_the_cover_grade_line_is_not_an_out_of_list_word(
    lesson: Lesson, curriculum: Curriculum
) -> None:
    deck = deck_html(lesson, tail="<p>Grade 3</p><p>Unit 1 Lesson 1 Period 1</p>")
    assert check_deck(lesson, deck, curriculum=curriculum).out_of_list_words == {}


def test_single_letter_avatars_are_accepted(lesson: Lesson, curriculum: Curriculum) -> None:
    slides = [
        slide(
            "b_05",
            '<span class="avatar">M</span><span class="avatar">L</span>'
            '<span class="avatar">Z</span><p>Hello.</p>',
        )
        if 'data-block-id="b_05"' in s
        else s
        for s in block_slides(lesson)
    ]
    result = check_deck(lesson, deck_html(lesson, slides), curriculum=curriculum)
    assert result.out_of_list_words == {}


def test_every_kind_of_external_reference_is_flagged(lesson: Lesson) -> None:
    tail = (
        '<video poster="https://cdn.example/poster.png"></video>'
        '<img srcset="https://cdn.example/a.png 2x, //cdn.example/b.png 3x" alt="x">'
        '<object data="https://cdn.example/o.swf"></object>'
        '<svg xmlns="http://www.w3.org/2000/svg"><use xlink:href="https://cdn.example/s.svg"/></svg>'
        '<meta http-equiv="refresh" content="0;url=https://evil.example/go">'
        "<script>fetch('https://api.example/x'); var s = '//cdn.example/y.js';</script>"
        "<script>var ns = 'http://www.w3.org/2000/svg'; // a comment</script>"
        '<a href="data:text/plain;base64,//8AAA==">ok</a><a href="images//x.png">ok</a>'
    )
    result = check_deck(lesson, deck_html(lesson, tail=tail))
    assert result.external_refs == [
        "https://cdn.example/poster.png",
        "https://cdn.example/a.png",
        "//cdn.example/b.png",
        "https://cdn.example/o.swf",
        "https://cdn.example/s.svg",
        "https://evil.example/go",
        "https://api.example/x",
        "//cdn.example/y.js",
    ]


def test_image_ref_picture_is_resolved_from_the_first_image_citation(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    lesson.blocks["b_09"].sources = [
        Citation(doc_id=DOC_ID, page=6, span_id="p6.s3"),
        Citation(doc_id=DOC_ID, page=6, span_id="p6.s4"),
    ]
    tag = placeholder("b_09", 0, "Two children waving")
    result = check_deck(
        lesson,
        deck_html(lesson, [*block_slides(lesson), tag]),
        sources=sources,
        image_root=image_root,
    )
    assert result.images_embedded == 1
    assert result.images_unresolved == []
    text = render_lesson_for_prompt(lesson, sources=sources)
    assert "- picture: data-item 0, p. 6, ¶4 · alt: Two children waving" in text


def test_embedding_keeps_an_alt_text_that_contains_src(
    lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> None:
    tag = '<img data-block-id="b_02" data-item="0" alt="the src=b here">'
    result = check_deck(
        lesson,
        deck_html(lesson, [*block_slides(lesson), tag]),
        sources=sources,
        image_root=image_root,
    )
    assert 'alt="the src=b here"' in result.html
    assert re.search(r'alt="the src=b here" src="data:image/png;base64,[^"]+">', result.html)
