"""Editing on the slide (``docs/output-formats.md``): the field map in the deck, the field setter,
the one-revision apply, and the deck's pencil / Save hooks. The endpoint is tested in
``test_app.py`` and ``import-edits`` in ``test_cli.py``."""

from __future__ import annotations

import json
from typing import Any

import pytest

from classdeck.editing import (
    EDITS_SCRIPT_ID,
    DeckEdit,
    FieldEditError,
    apply_deck_edits,
    editable_fields,
    read_embedded_edits,
    set_field,
    set_fields,
    undo,
)
from classdeck.export.html import SCRIPT, render_html
from classdeck.lesson import Block, BlockAdapter, Lesson, Provenance
from tests.exercise_fixtures import make_exercise_lesson
from tests.test_editing_context import CURRICULUM, SOURCES, make_edit_lesson
from tests.test_export import make_export_lesson
from tests.test_export_html import Node, parse


def make_noted_exercise_lesson() -> Lesson:
    """The exercise lesson with a teacher note on a reading and a prompt on the questions."""
    lesson = make_exercise_lesson()
    lesson.blocks["e_read"].teacher_note = "Check understanding.\n1. Read aloud.\n2. Ask who."
    lesson.blocks["e_q"].content.prompt = "Ask and answer in pairs."  # type: ignore[union-attr]
    return lesson


LESSONS = {
    "exercises": make_noted_exercise_lesson,
    "primary": make_export_lesson,
    "edit": make_edit_lesson,
}


def value_at(block: Block, path: str) -> Any:
    node: Any = block
    for segment in path.split("/"):
        if isinstance(node, list):
            node = node[int(segment)]
        elif isinstance(node, dict):
            node = node[segment]
        else:
            node = getattr(node, segment)
    return node


def deck_of(lesson: Lesson, **kwargs: Any) -> Node:
    return parse(render_html(lesson, theme="sunny", **kwargs))[0]


def field_elements(root: Node) -> list[Node]:
    return [n for n in root.walk() if "data-field" in n.attrs]


# --- the field map in the deck -----------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(LESSONS))
def test_every_field_element_names_a_path_the_setter_accepts(name: str) -> None:
    lesson = LESSONS[name]()
    elements = field_elements(deck_of(lesson))
    assert elements
    for element in elements:
        block = lesson.blocks[element.attrs["data-field-block"]]
        path = element.attrs["data-field"]
        assert path in editable_fields(block), (block.id, path)
        current = value_at(block, path)
        shown = element.attrs.get("data-raw", element.text())
        assert (shown or "") == (current or ""), (block.id, path)
        assert set_field(block, path, current or "") == block, (block.id, path)


@pytest.mark.parametrize("name", sorted(LESSONS))
def test_field_elements_are_not_block_carriers(name: str) -> None:
    """A field element never carries ``data-block-id`` (that stays one per block)."""
    for element in field_elements(deck_of(LESSONS[name]())):
        assert "data-block-id" not in element.attrs
        assert element.attrs["data-field-block"] and element.attrs["data-field"]


UNCOVERED_CLASSES = (
    "cover__title",  # the lesson title and the section titles are not block text
    "divider__title",
    "match__key",  # derived (the answer key)
    "tf__btn",  # True / False
    "bubble__who",  # the colon after the speaker's name
)
NUMBERING = ("__no", "__label", "__letter", "__number")


@pytest.mark.parametrize("name", sorted(LESSONS))
def test_every_pupil_visible_text_of_a_block_is_editable(name: str) -> None:
    """Text on a pupil slide is a field, interface chrome (``.ui``) or a number or a label."""
    root = deck_of(LESSONS[name]())
    missing: list[str] = []

    def walk(node: Node, covered: bool, exempt: bool, slide: str) -> None:
        for child in node.children:
            if isinstance(child, str):
                if child.strip() and not (covered or exempt):
                    missing.append(f"{slide}: {node.tag}.{' '.join(sorted(node.classes))}: {child}")
                continue
            classes = child.classes
            skip = (
                "ui" in classes
                or "teacher" in classes
                or child.attrs.get("aria-hidden") == "true"
                or any(c.startswith(UNCOVERED_CLASSES) for c in classes)
                or any(c.endswith(NUMBERING) for c in classes)
            )
            walk(child, covered or "data-field" in child.attrs, exempt or skip, slide)

    for slide in root.find("section", "slide"):
        if "teacher" not in slide.classes:
            walk(slide, False, False, slide.attrs["data-slide-type"])
    assert missing == []


def test_the_fields_of_each_slide_type_cover_the_documented_paths() -> None:
    seen: set[str] = set()
    for make in LESSONS.values():
        lesson = make()
        for element in field_elements(deck_of(lesson)):
            block = lesson.blocks[element.attrs["data-field-block"]]
            generic = "/".join(
                "N" if part.isdigit() else part for part in element.attrs["data-field"].split("/")
            )
            seen.add(f"{block.kind}:{generic}")
    expected = {
        "vocabulary:content/items/N/word",
        "vocabulary:content/items/N/ipa",
        "vocabulary:content/items/N/meaning_vi",
        "vocabulary:content/items/N/example",
        "sentence_pattern:content/pattern",
        "sentence_pattern:content/examples/N",
        "dialogue:content/lines/N/text",
        "dialogue:content/lines/N/speaker",
        "dialogue:content/context",
        "phonics:content/sound",
        "phonics:content/words/N",
        "phonics:content/chant_lines/N",
        "activity:content/title",
        "activity:content/instructions",
        "activity:content/student_steps/N",
        "activity:content/teacher_steps/N",
        "image_ref:content/caption",
        "objective:content/text",
        "teacher_note:content/text",
        "reading:content/title",
        "reading:content/paragraphs/N/text",
        "rule_box:content/points/N",
        "rule_box:content/examples/N",
        "match:content/left/N",
        "match:content/right/N",
        "gap_fill:content/word_box/N",
        "gap_fill:content/items/N/text",
        "gap_fill:content/items/N/answer",
        "choice:content/items/N/stem",
        "choice:content/items/N/options/N",
        "true_false:content/items/N/statement",
        "question_set:content/prompt",
        "question_set:content/questions/N/question",
        "question_set:content/questions/N/answer",
        "match:content/instruction",
        "gap_fill:content/exercise_no",
        "reading:teacher_note",
    }
    assert expected - seen == set()


def test_match_right_hand_items_use_their_own_index_when_the_slide_reorders_them() -> None:
    lesson = make_exercise_lesson()
    block = lesson.blocks["e_match"]
    root = deck_of(lesson)  # six right-hand items on a five-item slide: one unused, one extra
    rights = [
        e
        for e in field_elements(root)
        if e.attrs["data-field"].startswith("content/right/")
        and e.attrs["data-field-block"] == "e_match"
    ]
    assert {e.attrs["data-field"]: e.text() for e in rights} == {
        f"content/right/{i}": text for i, text in enumerate(block.content.right)
    }


def test_gap_sentences_carry_their_raw_text_and_answer_chips() -> None:
    lesson = make_exercise_lesson()
    root = deck_of(lesson)
    sentence = next(
        e for e in field_elements(root) if e.attrs["data-field"] == "content/items/0/text"
    )
    assert sentence.attrs["data-rebuild"] == "gap"
    assert "___" in sentence.attrs["data-raw"]
    row = next(n for n in root.walk() if "gap-item__answers" in n.classes)
    assert "edit-only" in row.classes
    chip = next(iter(row.find(class_="gap-item__answer")))
    assert chip.attrs["data-field"].endswith("/answer") or "/answer/" in chip.attrs["data-field"]


def test_split_titles_and_footers_are_not_editable() -> None:
    for make in (make_exercise_lesson, make_export_lesson):
        for node in deck_of(make()).walk():
            if node.classes & {"ex__part", "dialogue__part", "footer", "cover__title", "countdown"}:
                assert "data-field" not in node.attrs
                assert not any(
                    isinstance(child, Node) and "data-field" in child.attrs for child in node.walk()
                )


# --- the deck's own UI -------------------------------------------------------------------------


def test_the_deck_has_a_pencil_a_save_button_and_toasts_marked_as_interface() -> None:
    root = deck_of(make_export_lesson())
    toggle = root.one(id="edit-toggle")
    assert toggle.tag == "button" and "ui" in toggle.classes
    assert toggle.attrs["aria-pressed"] == "false"
    assert "(E)" in (toggle.attrs["title"] or "")
    save = root.one(id="edit-save")
    assert save.tag == "button" and "ui" in save.classes and "hidden" in save.attrs
    assert root.one(class_="edit-save__count")
    assert "ui" in root.one(id="edit-toasts").classes
    assert "ui" in root.one(id="edit-bar").classes


def test_the_deck_names_its_lesson_for_the_app() -> None:
    lesson = make_export_lesson()
    meta = [n for n in deck_of(lesson).walk() if n.attrs.get("name") == "classdeck-lesson"]
    assert [m.attrs["content"] for m in meta] == [lesson.id]


@pytest.mark.parametrize(
    "hook",
    [
        "contenteditable",
        "plaintext-only",
        "showSaveFilePicker",
        "beforeunload",
        "classdeck-edits",
        "/edits",
        "classdeck:saved",
        "createWritable",
        "'edit'",
        "editKey",
    ],
)
def test_the_script_has_the_edit_hooks(hook: str) -> None:
    assert hook in SCRIPT


def test_the_edit_script_matches_what_the_app_and_the_cli_expect() -> None:
    assert EDITS_SCRIPT_ID == "classdeck-edits"
    assert "id = 'classdeck-edits'" in SCRIPT
    assert "'Content-Type': 'application/json'" in SCRIPT
    assert "'/api/lessons/' + encodeURIComponent(lessonId) + '/edits'" in SCRIPT


# --- set_field ---------------------------------------------------------------------------------


def block_of(lesson_name: str, block_id: str) -> Block:
    return LESSONS[lesson_name]().blocks[block_id]


def test_set_field_changes_one_text_and_leaves_the_block_alone() -> None:
    block = block_of("edit", "b_02")
    updated = set_field(block, "content/items/1/word", "hey")
    assert updated.content.items[1].word == "hey"
    assert block.content.items[1].word == "hi"  # the input is not changed
    assert updated.content.items[0] == block.content.items[0]
    assert updated.id == block.id and updated.provenance is block.provenance


def test_set_field_normalises_whitespace_and_keeps_teacher_note_lines() -> None:
    block = block_of("edit", "b_07")
    assert set_field(block, "content/title", "  A   new\n title ").content.title == "A new title"
    noted = set_field(block, "teacher_note", "First line.\r\n\r\n  1.  Second   line.  \n")
    assert noted.teacher_note == "First line.\n\n1. Second line."


def test_set_field_turns_an_emptied_optional_text_into_none_and_refuses_a_required_one() -> None:
    block = block_of("edit", "b_02")
    assert set_field(block, "content/items/0/meaning_vi", "  ").content.items[0].meaning_vi is None
    with pytest.raises(FieldEditError, match="cannot be empty") as raised:
        set_field(block, "content/items/0/word", "")
    assert raised.value.block_id == "b_02"
    assert raised.value.field == "content/items/0/word"


@pytest.mark.parametrize(
    ("lesson_name", "block_id", "path"),
    [
        ("edit", "b_02", "content/items/0/nope"),  # unknown field
        ("edit", "b_02", "content/items/99/word"),  # no such item
        ("edit", "b_02", "content/items/x/word"),
        ("edit", "b_02", "content/items"),  # a list, not text
        ("edit", "b_02", "content"),
        ("edit", "b_02", ""),
        ("edit", "b_02", "/"),
        ("edit", "b_02", "id"),
        ("edit", "b_02", "kind"),
        ("edit", "b_02", "provenance"),
        ("edit", "b_02", "sources/0/quote"),
        ("edit", "b_02", "checks/0/detail"),
        ("edit", "b_02", "depends_on/0"),
        ("edit", "b_02", "content/items/0/image/quote"),
        ("edit", "b_02", "teacher_note/x"),
        ("edit", "b_02", "content/items/0/word/x"),  # through a plain text
        ("edit", "b_07", "content/type"),  # an enum
        ("edit", "b_07", "content/interaction"),
        ("edit", "b_07", "content/minutes"),  # a number
        ("edit", "b_07", "content/skills/0"),
        ("exercises", "e_tf", "content/items/0/answer"),  # a flag
        ("exercises", "e_match", "content/answer/0"),  # a number
        ("exercises", "e_match", "content/answer"),
        ("edit", "b_01", "content/outcome_ids/0"),
    ],
)
def test_set_field_rejects_paths_that_are_not_editable_text(
    lesson_name: str, block_id: str, path: str
) -> None:
    block = block_of(lesson_name, block_id)
    with pytest.raises(FieldEditError) as raised:
        set_field(block, path, "x")
    assert raised.value.block_id == block_id
    assert raised.value.field == path


def test_set_field_rejects_a_value_that_is_not_text() -> None:
    with pytest.raises(FieldEditError, match="must be text"):
        set_field(block_of("edit", "b_02"), "content/items/0/word", 5)  # type: ignore[arg-type]


def test_a_gap_sentence_must_keep_its_blanks() -> None:
    block = block_of("exercises", "e_gap")
    original = block.content.items[0].text
    assert original.count("___") == 1
    ok = set_field(block, "content/items/0/text", original.replace("___", "_____"))
    assert ok.content.items[0].text.count("_") == 5
    for bad in ("No blank at all.", original + " ___"):
        with pytest.raises(FieldEditError, match="must keep its 1 blank") as raised:
            set_field(block, "content/items/0/text", bad)
        assert raised.value.field == "content/items/0/text"


def test_a_gap_answer_must_stay_in_the_word_box_unless_both_change_together() -> None:
    block = block_of("exercises", "e_gap")
    answer = block.content.items[0].answers[0]
    with pytest.raises(FieldEditError, match="not in the word box"):
        set_field(block, "content/items/0/answer", "zzz")
    chip = next(i for i, w in enumerate(block.content.word_box) if w.lower() == answer.lower())
    both = set_fields(
        block, [("content/items/0/answer", "zzz"), (f"content/word_box/{chip}", "zzz")]
    )
    assert both.content.items[0].answers[0] == "zzz" and both.content.word_box[chip] == "zzz"
    with pytest.raises(FieldEditError, match="not in the word box"):
        set_field(block, f"content/word_box/{chip}", "zzz")


def two_blank_lesson(block: Block) -> dict[str, Any]:
    """The exercise lesson with ``block`` added to the first section."""
    data = make_exercise_lesson().model_dump(mode="json")
    data["blocks"][block.id] = block.model_dump(mode="json")
    data["sections"][0]["block_ids"].append(block.id)
    return data


def test_a_gap_with_two_blanks_edits_each_answer_by_its_own_path() -> None:
    block = BlockAdapter.validate_python(
        {
            "id": "g_two",
            "kind": "gap_fill",
            "content": {
                "items": [{"text": "She ___ to ___ every day.", "answer": ["goes", "school"]}],
                "word_box": ["goes", "school", "walks"],
            },
        }
    )
    path = "content/items/0/answer/1"
    assert path in editable_fields(block)
    assert set_field(block, path, "walks").content.items[0].answers == ["goes", "walks"]
    root = parse(render_html(Lesson.model_validate(two_blank_lesson(block)), theme="sunny"))[0]
    chips = [
        e.attrs["data-field"]
        for e in root.find(class_="gap-item__answer")
        if e.attrs["data-field-block"] == "g_two"
    ]
    assert chips == ["content/items/0/answer/0", "content/items/0/answer/1"]


def test_renaming_the_right_option_renames_the_answer_with_it() -> None:
    block = block_of("exercises", "e_choice")
    item = block.content.items[0]
    right = item.options.index(item.answer)
    updated = set_field(block, f"content/items/0/options/{right}", "a brand new option")
    assert updated.content.items[0].answer == "a brand new option"
    assert updated.content.items[0].options[right] == "a brand new option"
    wrong = next(i for i in range(len(item.options)) if i != right)
    kept = set_field(block, f"content/items/0/options/{wrong}", "another wrong one")
    assert kept.content.items[0].answer == item.answer
    with pytest.raises(FieldEditError, match="duplicate"):
        set_field(block, f"content/items/0/options/{wrong}", item.answer)


def test_editing_a_paragraph_keeps_only_the_highlights_that_remain() -> None:
    block = block_of("exercises", "e_read")
    assert block.content.paragraphs[0].highlight == ["busy", "crowded"]
    updated = set_field(
        block,
        "content/paragraphs/0/text",
        "Mai lives in a noisy city and the streets are crowded.",
    )
    assert updated.content.paragraphs[0].highlight == ["crowded"]


def test_the_match_text_can_change_without_touching_the_pairs() -> None:
    block = block_of("exercises", "e_match")
    updated = set_field(block, "content/left/0", "very busy")
    assert updated.content.answer == block.content.answer
    assert updated.content.left[0] == "very busy"


def test_a_batch_is_validated_once_at_the_end() -> None:
    block = block_of("edit", "b_02")
    updated = set_fields(
        block, [("content/items/0/word", "hello!"), ("content/items/0/word", "hallo")]
    )
    assert updated.content.items[0].word == "hallo"  # the last one wins


# --- apply_deck_edits --------------------------------------------------------------------------


def snapshot(lesson: Lesson) -> dict[str, Any]:
    data = lesson.model_dump(mode="json")
    return {key: data[key] for key in ("blocks", "sections", "checks")}


def apply(lesson: Lesson, *edits: tuple[str, str, str]):
    return apply_deck_edits(
        lesson,
        [DeckEdit(block_id=b, field=f, value=v) for b, f, v in edits],
        sources=SOURCES,
        curriculum=CURRICULUM,
    )


def test_several_edits_become_one_teacher_revision_that_undo_takes_back() -> None:
    lesson = make_edit_lesson()
    result = apply(
        lesson,
        ("b_02", "content/items/0/example", "Hello, I'm Ha."),
        ("b_02", "content/items/1/word", "hi"),
        ("b_07", "teacher_note", "Run three chains if the class is large."),
        ("b_03", "content/function", "Introduce yourself to a friend"),
    )
    assert result.changed and set(result.changed_block_ids) == {"b_07", "b_03"}
    updated = result.lesson
    assert len(updated.revisions) == len(lesson.revisions) + 1
    revision = updated.revisions[-1]
    assert revision.actor == "teacher"
    assert revision.instruction == "Edited on the slide"
    assert set(revision.target_block_ids) == {"b_07", "b_03"}
    assert updated.blocks["b_07"].teacher_note == "Run three chains if the class is large."
    assert updated.blocks["b_03"].content.function == "Introduce yourself to a friend"
    assert snapshot(updated) != snapshot(lesson)

    restored, undone = undo(updated)
    assert undone.id == revision.id
    assert snapshot(restored) == snapshot(lesson)
    assert len(restored.revisions) == len(lesson.revisions)


def test_an_edit_that_changes_nothing_makes_no_revision() -> None:
    lesson = make_edit_lesson()
    result = apply(lesson, ("b_02", "content/items/0/word", "hello"))
    assert not result.changed and result.notices == []
    assert result.lesson is lesson


def test_an_edit_that_drifts_from_the_source_downgrades_the_block_and_says_so() -> None:
    lesson = make_edit_lesson()
    assert lesson.blocks["b_02"].provenance is Provenance.EXTRACTED
    colours = ["red", "blue", "green", "yellow", "black", "white"]
    result = apply(
        lesson,
        *[("b_02", f"content/items/{n}/word", c) for n, c in enumerate(colours)],
        *[("b_02", f"content/items/{n}/example", f"A {c} pen.") for n, c in enumerate(colours)],
    )
    assert result.lesson.blocks["b_02"].provenance is Provenance.GENERATED
    kinds = {(n.block_id, n.kind) for n in result.notices}
    assert ("b_02", "provenance") in kinds
    assert any("generated" in n.message for n in result.notices if n.kind == "provenance")
    assert result.lesson.revisions[-1].inverse  # still one undoable revision


def test_a_faithful_edit_keeps_the_block_extracted() -> None:
    lesson = make_edit_lesson()
    result = apply(lesson, ("b_02", "content/items/0/meaning_vi", "xin chao"))
    assert result.lesson.blocks["b_02"].provenance is Provenance.EXTRACTED
    assert [n for n in result.notices if n.kind == "provenance"] == []


def test_a_bad_edit_names_the_block_and_the_field_and_changes_nothing() -> None:
    lesson = make_edit_lesson()
    before = snapshot(lesson)
    with pytest.raises(FieldEditError) as raised:
        apply(lesson, ("b_07", "teacher_note", "fine"), ("b_02", "content/items/0/word", ""))
    assert (raised.value.block_id, raised.value.field) == ("b_02", "content/items/0/word")
    with pytest.raises(FieldEditError, match="no block 'b_99'"):
        apply(lesson, ("b_99", "teacher_note", "x"))
    assert snapshot(lesson) == before


def test_the_last_edit_of_a_field_wins() -> None:
    lesson = make_edit_lesson()
    result = apply(
        lesson,
        ("b_03", "content/function", "First try"),
        ("b_03", "content/function", "Second try"),
    )
    assert result.lesson.blocks["b_03"].content.function == "Second try"


# --- the embedded edit list --------------------------------------------------------------------


def page_with(edits: list[dict[str, Any]], lesson_id: str = "l_x") -> str:
    payload = json.dumps({"lesson_id": lesson_id, "edits": edits}).replace("<", "\\u003c")
    return (
        '<!doctype html><html><head><meta name="classdeck-lesson" content="l_meta"></head><body>'
        f'<script type="application/json" id="{EDITS_SCRIPT_ID}">{payload}</script></body></html>'
    )


def test_the_embedded_list_is_read_with_its_lesson_id_and_ignores_extra_keys() -> None:
    page = page_with(
        [
            {"block_id": "b_02", "field": "content/items/0/word", "value": "hi", "original": "a"},
            {"block_id": "b_02", "field": "content/items/0/word", "value": "hey"},
        ]
    )
    lesson_id, edits = read_embedded_edits(page)
    assert lesson_id == "l_x"
    assert [(e.block_id, e.field, e.value) for e in edits] == [
        ("b_02", "content/items/0/word", "hey")
    ]


def test_a_page_without_a_list_or_with_a_broken_one_is_refused() -> None:
    with pytest.raises(ValueError, match="no embedded edits"):
        read_embedded_edits("<html><body>nothing</body></html>")
    broken = page_with([]).replace('{"lesson_id"', '{"lesson_id" oops')
    with pytest.raises(ValueError, match="not valid JSON"):
        read_embedded_edits(broken)
    with pytest.raises(FieldEditError, match="malformed"):
        read_embedded_edits(page_with([{"block_id": "b_02"}]))


def test_a_list_without_a_lesson_id_falls_back_to_the_decks_meta_tag() -> None:
    page = page_with([]).replace('"lesson_id": "l_x", ', "").replace('"lesson_id":"l_x",', "")
    lesson_id, edits = read_embedded_edits(page)
    assert lesson_id == "l_meta" and edits == []


def test_render_then_simulated_save_round_trips_through_the_lesson() -> None:
    """Render the deck, pick real fields from it, embed an edit list and import it."""
    lesson = make_edit_lesson()
    html = render_html(lesson, theme="sunny")
    root = parse(html)[0]
    word = next(
        e
        for e in field_elements(root)
        if e.attrs["data-field-block"] == "b_02" and e.attrs["data-field"] == "content/items/0/ipa"
    )
    edits = [
        {
            "block_id": word.attrs["data-field-block"],
            "field": word.attrs["data-field"],
            "value": "/helou/",
            "original": word.text(),
        },
        {
            "block_id": "b_07",
            "field": "teacher_note",
            "value": "Short note.",
            "original": lesson.blocks["b_07"].teacher_note,
        },
    ]
    edited = html.replace(
        "</body>",
        f'<script type="application/json" id="{EDITS_SCRIPT_ID}">'
        f"{json.dumps({'lesson_id': lesson.id, 'edits': edits}, ensure_ascii=False)}"
        "</script></body>",
    )
    lesson_id, parsed = read_embedded_edits(edited)
    assert lesson_id == lesson.id
    result = apply_deck_edits(lesson, parsed, sources=SOURCES, curriculum=CURRICULUM)
    assert result.lesson.blocks["b_02"].content.items[0].ipa == "/helou/"
    assert result.lesson.blocks["b_07"].teacher_note == "Short note."
    # The re-rendered deck shows the new text in the same field.
    again = field_elements(parse(render_html(result.lesson, theme="sunny"))[0])
    shown = {(e.attrs["data-field-block"], e.attrs["data-field"]): e for e in again}
    assert shown[("b_02", "content/items/0/ipa")].text() == "/helou/"
