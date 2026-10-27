from __future__ import annotations

from typing import Any

from classdeck.english.dependencies import infer
from classdeck.lesson import BlockAdapter, Lesson, Section
from tests.test_english_validators import activity, add_block, clean_lesson
from tests.test_lesson_schema import make_lesson


def edges(lesson: Lesson) -> dict[str, list[str]]:
    return {block_id: block.depends_on for block_id, block in lesson.blocks.items()}


def vocabulary(block_id: str, *words: str) -> dict[str, Any]:
    return {
        "id": block_id,
        "kind": "vocabulary",
        "content": {"items": [{"word": word} for word in words]},
    }


def dialogue(block_id: str) -> dict[str, Any]:
    return {
        "id": block_id,
        "kind": "dialogue",
        "content": {"lines": [{"speaker": "A", "text": "Hello, I'm Ha."}]},
    }


# --- activity -> vocabulary / sentence_pattern -------------------------------------------------


def test_activity_depends_on_the_vocabulary_and_pattern_it_uses() -> None:
    lesson = make_lesson()
    assert lesson.blocks["b_07"].depends_on == ["b_03"]
    inferred = infer(lesson)
    assert inferred.blocks["b_07"].depends_on == ["b_03", "b_02"]  # existing first, then new


def test_activity_pattern_edge_comes_from_the_slot_filled_match() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].depends_on = []
    inferred = infer(lesson)
    assert inferred.blocks["b_07"].depends_on == ["b_02", "b_03"]  # 'hello' and "Hello, I'm Duc."

    lesson = clean_lesson()
    lesson.blocks["b_07"].depends_on = []
    content: Any = lesson.blocks["b_07"].content
    content.student_steps = ["Say hello."]
    content.teacher_steps = ["Say hello."]
    assert infer(lesson).blocks["b_07"].depends_on == ["b_02"]  # no pattern match


def test_vocabulary_matches_whole_words_and_ignores_case() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].depends_on = []
    add_block(lesson, "s_presentation", vocabulary("b_04", "hi", "sun"))
    add_block(lesson, "s_presentation", vocabulary("b_05", "ball"))
    content: Any = lesson.blocks["b_07"].content
    content.instructions = "Which one? SUNNY days, BALL games."
    content.title = "Choose"
    content.teacher_steps = ["Choose."]
    content.student_steps = ["Choose."]
    content.materials = []
    # 'hi' is inside 'Which' and 'sun' inside 'SUNNY': neither is a whole word.
    assert infer(lesson).blocks["b_07"].depends_on == ["b_05"]


def test_multiword_vocabulary_items_are_matched() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].depends_on = []
    add_block(lesson, "s_presentation", vocabulary("b_04", "good morning"))
    lesson.blocks["b_07"].content.instructions = "Say Good  morning."
    assert "b_04" in infer(lesson).blocks["b_07"].depends_on  # case and spacing do not matter
    lesson.blocks["b_07"].content.instructions = "Say good evening."
    assert "b_04" not in infer(lesson).blocks["b_07"].depends_on


# --- activity -> dialogue ----------------------------------------------------------------------


def test_look_listen_repeat_and_role_play_depend_on_a_dialogue_in_the_same_section() -> None:
    lesson = clean_lesson()
    add_block(lesson, "s_presentation", dialogue("b_04"))
    add_block(lesson, "s_presentation", activity("b_10", type="look_listen_repeat"))
    add_block(lesson, "s_presentation", activity("b_11", type="role_play"))
    add_block(lesson, "s_presentation", activity("b_12", type="game"))
    add_block(lesson, "s_production", activity("b_13", type="role_play"))
    inferred = infer(lesson)
    assert "b_04" in inferred.blocks["b_10"].depends_on
    assert "b_04" in inferred.blocks["b_11"].depends_on
    assert "b_04" not in inferred.blocks["b_12"].depends_on  # other activity types
    assert "b_04" not in inferred.blocks["b_13"].depends_on  # a different section
    assert "b_04" not in inferred.blocks["b_07"].depends_on


def test_an_unplaced_role_play_has_no_section_and_gets_no_dialogue_edge() -> None:
    lesson = clean_lesson()
    add_block(lesson, "s_presentation", dialogue("b_04"))
    lesson.blocks["b_11"] = BlockAdapter.validate_python(activity("b_11", type="role_play"))
    assert "b_04" not in infer(lesson).blocks["b_11"].depends_on


# --- summary -----------------------------------------------------------------------------------


def test_summary_depends_on_vocabulary_patterns_and_phonics() -> None:
    lesson = clean_lesson()
    add_block(
        lesson,
        "s_presentation",
        {"id": "b_05", "kind": "phonics", "content": {"sound": "/b/", "letters": "b"}},
    )
    add_block(lesson, "s_presentation", dialogue("b_04"))
    lesson.blocks["b_08"].depends_on = ["b_07"]
    inferred = infer(lesson)
    # the existing edge stays first; the new ones follow, sorted; no dialogue, activity or objective
    assert inferred.blocks["b_08"].depends_on == ["b_07", "b_02", "b_03", "b_05"]


def test_summary_edges_do_not_duplicate_existing_ones() -> None:
    lesson = clean_lesson()
    assert lesson.blocks["b_08"].depends_on == ["b_02", "b_07"]
    assert infer(lesson).blocks["b_08"].depends_on == ["b_02", "b_07", "b_03"]


# --- invariants --------------------------------------------------------------------------------


def test_objectives_never_get_or_receive_edges() -> None:
    lesson = make_lesson()
    inferred = infer(lesson)
    assert inferred.blocks["b_01"].depends_on == []
    for block in inferred.blocks.values():
        assert "b_01" not in block.depends_on
    lesson.sections[0].block_ids.append("b_09")
    lesson.blocks["b_09"] = lesson.blocks["b_01"].model_copy(update={"id": "b_09"})
    inferred = infer(lesson)
    assert inferred.blocks["b_09"].depends_on == []
    assert "b_09" not in inferred.blocks["b_08"].depends_on


def test_no_self_edges_and_existing_edges_are_never_removed() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_08"].depends_on = ["b_08", "b_07", "b_07"]
    inferred = infer(lesson)
    assert inferred.blocks["b_08"].depends_on[:3] == ["b_08", "b_07", "b_07"]
    for block_id, depends_on in edges(inferred).items():
        assert depends_on.count(block_id) <= edges(lesson)[block_id].count(block_id)
    assert inferred.blocks["b_08"].depends_on.count("b_08") == 1


def test_infer_is_idempotent() -> None:
    once = infer(make_lesson())
    assert infer(once) == once
    lesson = clean_lesson()
    add_block(lesson, "s_presentation", dialogue("b_04"))
    add_block(lesson, "s_presentation", activity("b_10", type="role_play"))
    first = infer(lesson)
    assert infer(first) == first


def test_infer_returns_a_new_valid_lesson_and_leaves_the_input_alone() -> None:
    lesson = make_lesson()
    before = edges(lesson)
    inferred = infer(lesson)
    assert inferred is not lesson
    assert edges(lesson) == before
    assert inferred != lesson
    assert Lesson.model_validate(inferred.model_dump(mode="json")) == inferred


def test_infer_adds_nothing_when_nothing_matches() -> None:
    lesson = clean_lesson()
    lesson.sections.append(
        Section(id="s_extra", phase="practice", title="Extra", minutes=0, block_ids=[])
    )
    add_block(
        lesson,
        "s_extra",
        activity(
            "b_09",
            title="Point",
            instructions="Point to the door.",
            teacher_steps=["Point."],
            student_steps=["Point."],
        ),
    )
    inferred = infer(lesson)
    assert inferred.blocks["b_09"].depends_on == []
