from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.english import validators as v
from classdeck.lesson import (
    BlockAdapter,
    CheckResult,
    CheckStatus,
    Citation,
    Lesson,
    Phase,
    Section,
)
from tests.test_lesson_schema import DOC_ID, IPA_HELLO, make_lesson, make_source_document

ROOT = Path(__file__).resolve().parents[1] / "curriculum"
CURRICULUM = load_curriculum(ROOT, "gdpt-2018", 3)
PASS, WARN, FAIL, STALE = (
    CheckStatus.PASS,
    CheckStatus.WARN,
    CheckStatus.FAIL,
    CheckStatus.STALE,
)


# --- helpers -----------------------------------------------------------------------------------


def clean_lesson() -> Lesson:
    """The example lesson, adjusted so that every check passes."""
    lesson = make_lesson()
    lesson.sections[0].minutes = 17
    lesson.sections[1].minutes = 13
    lesson.sections[2].minutes = 5
    content: Any = lesson.blocks["b_07"].content
    content.title = "Say hello"
    content.instructions = "Say hello. Look at the ball."
    content.teacher_steps = ["Say hello to a friend.", "Point to the ball."]
    content.student_steps = ["Say 'Hello, I'm Duc.'", "Say hello to a friend."]
    content.materials = ["a ball"]
    return lesson


def activity(block_id: str, **content: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "type": "game",
        "title": "Say hello",
        "skills": ["speaking"],
        "interaction": "pairs",
        "minutes": 5,
        "instructions": "Say hello.",
        "teacher_steps": ["Say hello to a friend."],
        "student_steps": ["Say hello."],
    }
    return {"id": block_id, "kind": "activity", "content": {**base, **content}}


def add_block(lesson: Lesson, section_id: str, data: dict[str, Any]) -> Lesson:
    lesson.blocks[data["id"]] = BlockAdapter.validate_python(data)
    section = next(s for s in lesson.sections if s.id == section_id)
    section.block_ids.append(data["id"])
    return lesson


def add_practice_section(lesson: Lesson) -> Lesson:
    section = Section(id="s_practice", phase="practice", title="Practice", minutes=12, block_ids=[])
    lesson.sections.insert(1, section)
    return lesson


def by_block(outcomes: list[v.CheckOutcome]) -> dict[str | None, CheckResult]:
    found = {block_id: result for block_id, result in outcomes}
    assert len(found) == len(outcomes), "one outcome per block expected"
    return found


def names(checks: list[CheckResult]) -> list[str]:
    return [check.name for check in checks]


def with_curriculum(change: Any) -> Curriculum:
    file = CURRICULUM.file.model_copy(deep=True)
    change(file)
    return Curriculum(file)


# --- the example lesson ------------------------------------------------------------------------


def test_clean_lesson_has_no_warnings_and_no_failures() -> None:
    done = v.run_all(clean_lesson(), CURRICULUM)
    assert v.warnings(done) == []
    assert not v.has_failures(done)
    assert {c.name for c in done.checks} == {
        "english.skill_coverage",
        "english.timing",
        "curriculum.outcome_covered",
    }


def test_the_unedited_example_lesson_is_flagged_where_it_should_be() -> None:
    done = v.run_all(make_lesson(), CURRICULUM)
    flagged = {(block_id, check.name) for block_id, check in v.warnings(done)}
    assert (None, "english.timing") in flagged  # 10 + 6 + 2 minutes
    # 'catch' and 'pass' are classroom language; 'chain', 'model', 'pupils' are teacher-facing.
    assert ("b_07", "english.vocab_level") not in flagged
    detail = next(c.detail for c in done.blocks["b_07"].checks if c.name == "english.vocab_level")
    assert detail.endswith("words in the grade word list or cited spans")
    assert not v.has_failures(done)


def test_all_validators_run_in_the_documented_order() -> None:
    assert [validator.__name__ for validator in v.ALL_VALIDATORS] == [
        "vocab_level",
        "grammar_scope",
        "skill_coverage",
        "ipa_present",
        "gloss_present",
        "wording_kept",
        "pattern_used",
        "steps_present",
        "timing",
        "outcome_covered",
    ]
    assert {
        "english.vocab_level",
        "english.grammar_scope",
        "english.skill_coverage",
        "english.ipa_present",
        "english.gloss_present",
        "english.wording_kept",
        "english.pattern_used",
        "english.steps_present",
        "english.timing",
        "curriculum.outcome_covered",
    } == v.OWNED_CHECKS


# --- text extraction ---------------------------------------------------------------------------


def test_english_text_of_each_kind() -> None:
    lesson = clean_lesson()
    add_block(
        lesson,
        "s_presentation",
        {
            "id": "b_04",
            "kind": "dialogue",
            "content": {
                "context": "Two pupils meet.",
                "lines": [
                    {"speaker": "Ha", "text": "Hello, I'm Ha."},
                    {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
                ],
            },
        },
    )
    add_block(
        lesson,
        "s_presentation",
        {
            "id": "b_05",
            "kind": "phonics",
            "content": {
                "sound": "/b/",
                "letters": "b",
                "words": ["Bill", "bye"],
                "chant_lines": ["B, b, bye!"],
            },
        },
    )
    add_block(
        lesson, "s_presentation", {"id": "b_06", "kind": "image_ref", "content": {"alt": "x"}}
    )
    add_block(
        lesson, "s_presentation", {"id": "b_10", "kind": "teacher_note", "content": {"text": "Hi."}}
    )
    text = {block_id: v.english_text_of(block) for block_id, block in lesson.blocks.items()}
    assert text["b_01"] == []
    assert text["b_02"] == ["hello", "hi", "Hello, I'm Ha.", "Hi, Ha."]  # no meaning_vi
    assert text["b_03"] == ["Hello, I'm.", "Hello, I'm Ha."]  # the slot is removed
    assert text["b_04"] == ["Hello, I'm Ha.", "Hi, Ha. I'm Tien."]
    assert text["b_05"] == ["Bill", "bye", "B, b, bye!"]
    assert text["b_06"] == []
    assert text["b_10"] == []
    assert text["b_07"] == [
        "Say hello",
        "Say hello. Look at the ball.",
        "Say hello to a friend.",
        "Point to the ball.",
        "Say 'Hello, I'm Duc.'",
        "Say hello to a friend.",
        "a ball",
    ]
    assert text["b_08"] == ["hello, hi", "Hello, I'm ___."]


def test_pupil_text_of_keeps_only_what_pupils_see_or_hear() -> None:
    lesson = clean_lesson()
    task: Any = lesson.blocks["b_07"].content
    task.materials = ["a soft ball"]
    task.competency_tags = ["cooperation"]
    lesson.blocks["b_07"].teacher_note = "Run two chains in parallel."
    assert v.pupil_text_of(lesson.blocks["b_07"]) == [
        "Say hello. Look at the ball.",
        "Say 'Hello, I'm Duc.'",
        "Say hello to a friend.",
    ]
    for block_id in ("b_01", "b_02", "b_03", "b_08"):
        block = lesson.blocks[block_id]
        assert v.pupil_text_of(block) == v.english_text_of(block)
    assert "xin" not in " ".join(v.pupil_text_of(lesson.blocks["b_02"]))


# --- tokenisation ------------------------------------------------------------------------------


def surfaces(text: str) -> list[str]:
    return [token.surface for token in v.tokenize(text)]


def test_tokenize_splits_contractions_and_drops_numbers_and_slots() -> None:
    assert surfaces("I'm Duc. Isn't it? Don't touch! Let's go") == [
        "i",
        "duc",
        "is",
        "it",
        "do",
        "touch",
        "let",
        "go",
    ]
    assert surfaces("Can't, won't and What's") == ["can", "will", "and", "what"]
    assert surfaces("Say 10 and 3rd, then ___.") == ["say", "and", "then"]
    assert surfaces("Hello, I\N{RIGHT SINGLE QUOTATION MARK}m Duc.") == ["hello", "i", "duc"]
    assert surfaces("") == []


def test_tokenize_marks_capitalised_words_inside_a_sentence_as_proper() -> None:
    tokens = v.tokenize("Duc says hello to Tien. Tien is happy. Say 'Hello' to Mai.")
    proper = [token.surface for token in tokens if token.proper]
    assert proper == ["tien", "mai"]  # sentence- and quote-initial words are not names
    assert not any(token.proper for token in v.tokenize("I am here"))


# --- english.vocab_level -----------------------------------------------------------------------


def test_vocab_level_passes_for_words_in_the_grade_list() -> None:
    results = by_block(v.vocab_level(clean_lesson(), CURRICULUM))
    assert set(results) == {"b_02", "b_03", "b_07", "b_08"}  # the five kinds that carry English
    for result in results.values():
        assert result.name == "english.vocab_level"
        assert result.status is PASS


def test_vocab_level_lists_offenders_sorted() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.instructions = "Say zebra and photosynthesis."
    result = by_block(v.vocab_level(lesson, CURRICULUM))["b_07"]
    assert result.status is WARN
    assert result.detail is not None
    assert result.detail.endswith("photosynthesis, zebra")
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_02"].status is PASS


def test_vocab_level_checks_vocabulary_words_themselves() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_02"].content.items[1].word = "giraffe"
    result = by_block(v.vocab_level(lesson, CURRICULUM))["b_02"]
    assert result.status is WARN
    assert "giraffe" in (result.detail or "")


def test_vocab_level_accepts_inflections_names_numbers_and_function_words() -> None:
    lesson = clean_lesson()
    lesson.blocks[
        "b_07"
    ].content.instructions = (
        "She is playing and singing. Draw two pencils. Say 10 and 20. OK, let's go to Duc's desk."
    )
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is PASS


def test_vocab_level_flags_sentence_initial_names_unless_the_source_has_them() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.instructions = "Tien says hello."
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is WARN

    lesson.blocks["b_07"].sources = [Citation(doc_id=DOC_ID, page=6, span_id="p6.s3")]
    sources = {DOC_ID: make_source_document()}
    assert by_block(v.vocab_level(lesson, CURRICULUM, sources=sources))["b_07"].status is PASS
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is WARN


def test_vocab_level_allows_only_the_spans_a_block_cites() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.instructions = "Tien says hello."
    sources = {DOC_ID: make_source_document()}
    assert by_block(v.vocab_level(lesson, CURRICULUM, sources=sources))["b_07"].status is WARN
    lesson.blocks["b_07"].sources = [Citation(doc_id=DOC_ID, page=6, span_id="p6.s4")]  # an image
    assert by_block(v.vocab_level(lesson, CURRICULUM, sources=sources))["b_07"].status is WARN
    lesson.blocks["b_07"].sources = [Citation(doc_id="d_other", page=1, span_id="p1.s1")]
    assert by_block(v.vocab_level(lesson, CURRICULUM, sources=sources))["b_07"].status is WARN


def test_vocab_level_reads_the_unit_vocabulary() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.instructions = "Play badminton."
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is WARN  # unit 1
    lesson.curriculum.unit_id = "ta3.u5"
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is PASS


def test_vocab_level_falls_back_to_the_grade_list_for_an_unknown_unit() -> None:
    lesson = clean_lesson()
    lesson.curriculum.unit_id = "ta3.nope"
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is PASS


def test_vocab_level_ignores_vietnamese_glosses_and_teacher_notes() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_02"].teacher_note = "Giải thích từ mới bằng hình ảnh."
    results = by_block(v.vocab_level(lesson, CURRICULUM))
    assert results["b_02"].status is PASS
    assert results["b_07"].status is PASS  # its teacher_note mentions 'chains'


@pytest.mark.parametrize("field", ["instructions", "student_steps"])
def test_vocab_level_warns_for_an_unknown_word_in_pupil_facing_activity_text(field: str) -> None:
    lesson = clean_lesson()
    value: Any = "Say zebra." if field == "instructions" else ["Say zebra."]
    setattr(lesson.blocks["b_07"].content, field, value)
    result = by_block(v.vocab_level(lesson, CURRICULUM))["b_07"]
    assert result.status is WARN
    assert (result.detail or "").endswith("zebra")


@pytest.mark.parametrize("field", ["teacher_steps", "title", "materials"])
def test_vocab_level_ignores_teacher_facing_activity_text(field: str) -> None:
    lesson = clean_lesson()
    value: Any = ["Model the zebra chain."] if field != "title" else "Zebra chain"
    setattr(lesson.blocks["b_07"].content, field, value)
    lesson.blocks["b_07"].content.competency_tags = ["cooperation"]
    lesson.blocks["b_07"].teacher_note = "Zebras are fun."
    assert by_block(v.vocab_level(lesson, CURRICULUM))["b_07"].status is PASS


# --- english.grammar_scope ---------------------------------------------------------------------


def test_grammar_scope_passes_clean_text() -> None:
    results = by_block(v.grammar_scope(clean_lesson(), CURRICULUM))
    assert set(results) == {"b_03", "b_07"}
    assert all(r.status is PASS and r.name == "english.grammar_scope" for r in results.values())


def test_grammar_scope_warns_with_rule_id_and_snippet() -> None:
    lesson = add_block(
        clean_lesson(), "s_production", activity("b_09", instructions="She has eaten.")
    )
    result = by_block(v.grammar_scope(lesson, CURRICULUM))["b_09"]
    assert result.status is WARN
    assert result.detail is not None
    assert "perfect: 'has eaten'" in result.detail
    assert by_block(v.grammar_scope(lesson, CURRICULUM))["b_07"].status is PASS


def test_grammar_scope_checks_instructions_and_student_steps_not_teacher_text() -> None:
    lesson = clean_lesson()
    task: Any = lesson.blocks["b_07"].content
    task.teacher_steps = ["Say: She has eaten."]
    task.title = "She has eaten"
    task.materials = ["She has eaten"]
    lesson.blocks["b_07"].teacher_note = "She has eaten."
    assert by_block(v.grammar_scope(lesson, CURRICULUM))["b_07"].status is PASS

    task.instructions = "She has eaten."
    result = by_block(v.grammar_scope(lesson, CURRICULUM))["b_07"]
    assert result.status is WARN
    assert "perfect: 'has eaten'" in (result.detail or "")

    task.instructions = "Say hello."
    task.student_steps = ["She has eaten."]
    assert by_block(v.grammar_scope(lesson, CURRICULUM))["b_07"].status is WARN


def test_grammar_scope_checks_dialogues_and_patterns() -> None:
    lesson = clean_lesson()
    add_block(
        lesson,
        "s_presentation",
        {
            "id": "b_04",
            "kind": "dialogue",
            "content": {"lines": [{"speaker": "A", "text": "I went home."}]},
        },
    )
    lesson.blocks["b_03"].content.examples = ["It was eaten by the cat."]
    results = by_block(v.grammar_scope(lesson, CURRICULUM))
    assert results["b_04"].status is WARN
    assert "past: 'went'" in (results["b_04"].detail or "")
    assert results["b_03"].status is WARN
    assert "passive" in (results["b_03"].detail or "")


# --- english.skill_coverage --------------------------------------------------------------------


def test_skill_coverage_passes_when_every_outcome_skill_has_an_activity() -> None:
    results = by_block(v.skill_coverage(clean_lesson(), CURRICULUM))
    assert list(results) == [None]
    assert results[None].name == "english.skill_coverage"
    assert results[None].status is PASS
    assert results[None].detail == "covered: listening, speaking"


def test_skill_coverage_names_the_missing_skills() -> None:
    lesson = clean_lesson()
    lesson.curriculum.outcome_ids = ["ta3.u1.o1", "ta3.u2.o3"]  # + reading, writing
    result = by_block(v.skill_coverage(lesson, CURRICULUM))[None]
    assert result.status is WARN
    assert result.detail == "no activity practises: reading, writing"


def test_skill_coverage_without_activities_or_known_outcomes() -> None:
    lesson = clean_lesson()
    del lesson.blocks["b_07"]
    lesson.sections[1].block_ids.remove("b_07")
    lesson.blocks["b_08"].depends_on = ["b_02"]
    assert by_block(v.skill_coverage(lesson, CURRICULUM))[None].status is WARN
    lesson.curriculum.outcome_ids = ["ta3.unknown"]
    assert by_block(v.skill_coverage(lesson, CURRICULUM))[None].status is PASS


# --- english.ipa_present and fill_ipa ----------------------------------------------------------


def test_ipa_present_passes_when_every_item_has_ipa() -> None:
    results = by_block(v.ipa_present(clean_lesson(), CURRICULUM))
    assert list(results) == ["b_02"]
    assert results["b_02"].name == "english.ipa_present"
    assert results["b_02"].status is PASS


def test_ipa_present_reports_items_that_are_still_missing_and_does_not_fill() -> None:
    lesson = clean_lesson()
    items = lesson.blocks["b_02"].content.items
    items[0].ipa = None
    items[1].word = "zebra"
    items[1].ipa = "  "
    result = by_block(v.ipa_present(lesson, CURRICULUM))["b_02"]
    assert result.status is WARN
    assert result.detail == "no IPA for: hello, zebra"
    assert items[0].ipa is None


def test_fill_ipa_fills_known_words_and_leaves_the_input_alone() -> None:
    lesson = clean_lesson()
    items = lesson.blocks["b_02"].content.items
    items[0].ipa = None
    items[1].ipa = "/custom/"
    filled = v.fill_ipa(lesson)
    assert filled is not lesson
    assert filled.blocks["b_02"].content.items[0].ipa == IPA_HELLO
    assert filled.blocks["b_02"].content.items[1].ipa == "/custom/"  # existing IPA is kept
    assert items[0].ipa is None
    assert by_block(v.ipa_present(filled, CURRICULUM))["b_02"].status is PASS


def test_fill_ipa_leaves_unknown_words_missing() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_02"].content.items[0].word = "zebra"
    lesson.blocks["b_02"].content.items[0].ipa = None
    filled = v.fill_ipa(lesson)
    assert filled.blocks["b_02"].content.items[0].ipa is None
    assert by_block(v.ipa_present(filled, CURRICULUM))["b_02"].status is WARN


def test_lookup_ipa_handles_case_and_phrases() -> None:
    assert v.lookup_ipa("Hello") == IPA_HELLO
    assert v.lookup_ipa("  HELLO ") == IPA_HELLO
    phrase = v.lookup_ipa("good morning")
    assert phrase is not None
    assert phrase.startswith("/") and phrase.endswith("/")
    assert " " in phrase
    assert "/" not in phrase[1:-1]
    assert v.lookup_ipa("good zebra") is None
    assert v.lookup_ipa("zebra") is None


def test_the_ipa_list_covers_every_grade_word() -> None:
    data = yaml.safe_load((ROOT / "gdpt-2018" / "tieng-anh" / "lop-3.yaml").read_text("utf-8"))
    words = {str(word).lower() for word in data["vocabulary"]["words"]}
    words |= {str(word).lower() for unit in data["units"] for word in unit["vocabulary"]}
    dictionary = v.ipa_dictionary()
    assert sorted(words - set(dictionary)) == []
    assert all(ipa.startswith("/") and ipa.endswith("/") for ipa in dictionary.values())
    assert all(key == key.lower() and not key.startswith("_") for key in dictionary)


def test_the_ipa_file_keeps_its_note() -> None:
    path = Path(v.__file__).parent / "data" / "ipa_core.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["_note"].startswith("Seed pronunciation list")
    assert "_note" not in v.ipa_dictionary()


# --- english.pattern_used ----------------------------------------------------------------------


def test_pattern_used_passes_and_names_the_pattern_block() -> None:
    results = by_block(v.pattern_used(clean_lesson(), CURRICULUM))
    assert list(results) == ["b_07"]  # production; the vocabulary and pattern blocks are not tested
    assert results["b_07"].status is PASS
    assert results["b_07"].name == "english.pattern_used"
    assert results["b_07"].detail == "uses pattern from b_03: Hello, I'm ___."


def test_pattern_used_warns_for_a_practice_activity_without_the_pattern() -> None:
    lesson = add_practice_section(clean_lesson())
    add_block(lesson, "s_practice", activity("b_09", instructions="Point to the ball."))
    result = by_block(v.pattern_used(lesson, CURRICULUM))["b_09"]
    assert result.status is WARN
    assert result.detail == "does not use any sentence pattern of the lesson"


def test_pattern_used_passes_when_the_activity_text_fills_the_slot() -> None:
    lesson = add_practice_section(clean_lesson())
    add_block(
        lesson,
        "s_practice",
        activity("b_09", student_steps=["Say 'Hello, I'm Duc.' to a friend."]),
    )
    result = by_block(v.pattern_used(lesson, CURRICULUM))["b_09"]
    assert result.status is PASS
    assert "b_03" in (result.detail or "")


def test_pattern_used_skips_other_phases_and_unplaced_activities() -> None:
    lesson = clean_lesson()
    lesson.sections.insert(
        0, Section(id="s_warm", phase="warm_up", title="Warm-up", minutes=5, block_ids=[])
    )
    add_block(lesson, "s_warm", activity("b_09", instructions="Point to the ball."))
    lesson.blocks["b_10"] = BlockAdapter.validate_python(activity("b_10"))
    assert set(by_block(v.pattern_used(lesson, CURRICULUM))) == {"b_07"}


def test_pattern_used_warns_when_the_lesson_has_no_pattern() -> None:
    lesson = clean_lesson()
    lesson.sections[0].block_ids.remove("b_03")
    del lesson.blocks["b_03"]
    lesson.blocks["b_07"].depends_on = []
    result = by_block(v.pattern_used(lesson, CURRICULUM))["b_07"]
    assert result.status is WARN
    assert "no sentence_pattern block" in (result.detail or "")


def test_pattern_regex_builds_a_slot_filled_match() -> None:
    regex = v.pattern_regex("Hello, I'm ___.")
    assert regex is not None
    assert regex.search("Say hello, i'm Duc.")
    assert regex.search("Hello,   I\N{RIGHT SINGLE QUOTATION MARK}m Tien")  # curly, extra spaces
    assert not regex.search("Hello, you are Duc.")
    assert not regex.search("Hello, I'm")  # nothing in the slot


def test_pattern_regex_handles_alternatives_and_slot_only_patterns() -> None:
    regex = v.pattern_regex("Yes, I do. / No, I don't.")
    assert regex is not None
    assert regex.search("yes, i do")
    assert regex.search("No, I don't.")
    assert not regex.search("Yes, I don")  # whole words only
    assert v.pattern_regex("___") is None
    assert v.pattern_regex("") is None
    question = v.pattern_regex("Is that ___?")
    assert question is not None
    assert question.search("Is that your pen?")


# --- english.steps_present ---------------------------------------------------------------------


def test_steps_present_passes_for_a_complete_activity() -> None:
    results = by_block(v.steps_present(clean_lesson(), CURRICULUM))
    assert list(results) == ["b_07"]
    assert results["b_07"].status is PASS
    assert results["b_07"].name == "english.steps_present"


@pytest.mark.parametrize(
    ("change", "missing"),
    [
        ({"student_steps": []}, "student_steps"),
        ({"teacher_steps": []}, "teacher_steps"),
        ({"skills": []}, "skills"),
        ({"student_steps": ["  "]}, "student_steps"),
        ({"skills": [], "teacher_steps": []}, "skills, teacher_steps"),
    ],
)
def test_steps_present_fails_when_something_is_missing(
    change: dict[str, Any], missing: str
) -> None:
    lesson = add_block(clean_lesson(), "s_production", activity("b_09", **change))
    results = by_block(v.steps_present(lesson, CURRICULUM))
    assert results["b_09"].status is FAIL
    assert results["b_09"].detail == f"missing: {missing}"
    assert results["b_07"].status is PASS


# --- english.timing ----------------------------------------------------------------------------


@pytest.mark.parametrize(("total", "status"), [(33, PASS), (35, PASS), (37, PASS)])
def test_timing_passes_within_the_tolerance(total: int, status: CheckStatus) -> None:
    lesson = make_lesson()
    lesson.sections[0].minutes = total - 8
    result = by_block(v.timing(lesson, CURRICULUM))[None]
    assert result.name == "english.timing"
    assert result.status is status
    assert result.detail == f"{total} min"


@pytest.mark.parametrize("total", [18, 32, 38, 50])
def test_timing_warns_outside_the_tolerance(total: int) -> None:
    lesson = make_lesson()
    lesson.sections[0].minutes = total - 8
    result = by_block(v.timing(lesson, CURRICULUM))[None]
    assert result.status is WARN
    assert (result.detail or "").startswith(f"{total} min")


def test_timing_reads_the_period_length_from_the_curriculum() -> None:
    forty = with_curriculum(lambda file: setattr(file, "period_minutes", 40))
    assert by_block(v.timing(clean_lesson(), forty))[None].status is WARN


# --- curriculum.outcome_covered ----------------------------------------------------------------


def test_outcome_covered_passes_for_the_example_lesson() -> None:
    result = by_block(v.outcome_covered(clean_lesson(), CURRICULUM))[None]
    assert result.name == "curriculum.outcome_covered"
    assert result.status is PASS


def test_outcome_covered_warns_when_the_objective_is_dropped() -> None:
    lesson = clean_lesson()
    lesson.sections[0].block_ids.remove("b_01")
    del lesson.blocks["b_01"]
    result = by_block(v.outcome_covered(lesson, CURRICULUM))[None]
    assert result.status is WARN
    assert result.detail == "not covered: ta3.u1.o1 (no objective)"


def test_outcome_covered_warns_when_no_activity_practises_the_skills() -> None:
    lesson = clean_lesson()
    lesson.curriculum.outcome_ids = ["ta3.u1.o1", "ta3.u2.o3"]  # reading, writing
    lesson.blocks["b_01"].content.outcome_ids = ["ta3.u1.o1", "ta3.u2.o3"]
    result = by_block(v.outcome_covered(lesson, CURRICULUM))[None]
    assert result.status is WARN
    assert result.detail == "not covered: ta3.u2.o3 (no activity)"


def test_outcome_covered_lets_any_activity_cover_an_outcome_without_skills() -> None:
    def drop_skills(file: Any) -> None:
        file.units[0].outcomes[0].skills = []

    curriculum = with_curriculum(drop_skills)
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.skills = []
    assert by_block(v.outcome_covered(lesson, curriculum))[None].status is PASS
    del lesson.blocks["b_07"]
    lesson.sections[1].block_ids.remove("b_07")
    lesson.blocks["b_08"].depends_on = []
    assert by_block(v.outcome_covered(lesson, curriculum))[None].status is WARN


def test_outcome_covered_reports_unknown_outcomes() -> None:
    lesson = clean_lesson()
    lesson.curriculum.outcome_ids = ["ta3.u1.o1", "ta3.u9.o1"]
    result = by_block(v.outcome_covered(lesson, CURRICULUM))[None]
    assert result.status is WARN
    assert result.detail == "not covered: ta3.u9.o1 (unknown outcome)"


# --- running and applying ----------------------------------------------------------------------


def test_apply_outcomes_replaces_a_result_of_the_same_name_and_appends_new_ones() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_02"].checks.append(CheckResult(name="english.ipa_present", status=FAIL))
    outcomes: list[v.CheckOutcome] = [
        ("b_02", CheckResult(name="english.ipa_present", status=WARN, detail="new")),
        ("b_02", CheckResult(name="english.vocab_level", status=PASS)),
        (None, CheckResult(name="english.timing", status=PASS, detail="35 min")),
    ]
    applied = v.apply_outcomes(lesson, outcomes)
    assert names(applied.blocks["b_02"].checks) == [
        "grounding.citation_supported",
        "english.ipa_present",
        "english.vocab_level",
    ]
    assert applied.blocks["b_02"].checks[1].detail == "new"
    assert names(applied.checks) == ["english.timing"]
    again = v.apply_outcomes(applied, [(None, CheckResult(name="english.timing", status=WARN))])
    assert [c.status for c in again.checks] == [WARN]
    assert applied.checks[0].status is PASS
    assert names(lesson.blocks["b_02"].checks) == [
        "grounding.citation_supported",
        "english.ipa_present",
        "english.ipa_present",
    ]


def test_apply_outcomes_rejects_an_unknown_block() -> None:
    with pytest.raises(KeyError, match="b_missing"):
        v.apply_outcomes(
            clean_lesson(), [("b_missing", CheckResult(name="english.timing", status=PASS))]
        )


def test_run_all_returns_a_new_lesson_and_leaves_the_input_untouched() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_02"].content.items[0].ipa = None
    before = lesson.model_dump()
    done = v.run_all(lesson, CURRICULUM)
    assert lesson.model_dump() == before
    assert done is not lesson
    assert done.blocks["b_02"].content.items[0].ipa == IPA_HELLO
    assert "english.vocab_level" in names(done.blocks["b_07"].checks)


def test_run_all_twice_does_not_duplicate_checks() -> None:
    once = v.run_all(make_lesson(), CURRICULUM)
    twice = v.run_all(once, CURRICULUM)
    for lesson in (once, twice):
        assert len(names(lesson.checks)) == len(set(names(lesson.checks)))
        for block in lesson.blocks.values():
            assert len(names(block.checks)) == len(set(names(block.checks)))
    assert names(twice.checks) == names(once.checks)
    for block_id, block in once.blocks.items():
        assert names(twice.blocks[block_id].checks) == names(block.checks)
        assert [c.status for c in twice.blocks[block_id].checks] == [c.status for c in block.checks]


def test_run_all_keeps_results_from_other_stages() -> None:
    done = v.run_all(clean_lesson(), CURRICULUM)
    assert "grounding.citation_supported" in names(done.blocks["b_02"].checks)


def test_run_all_drops_results_that_no_longer_apply() -> None:
    lesson = v.run_all(clean_lesson(), CURRICULUM)
    assert "english.pattern_used" in names(lesson.blocks["b_07"].checks)
    lesson.sections[1].phase = Phase.WARM_UP
    moved = v.run_all(lesson, CURRICULUM)
    assert "english.pattern_used" not in names(moved.blocks["b_07"].checks)


def test_run_all_passes_the_sources_to_the_validators() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_07"].content.instructions = "Tien says hello."
    lesson.blocks["b_07"].sources = [Citation(doc_id=DOC_ID, page=6, span_id="p6.s3")]
    sources = {DOC_ID: make_source_document()}
    flagged = v.run_all(lesson, CURRICULUM)
    assert ("b_07", "english.vocab_level") in {(b, c.name) for b, c in v.warnings(flagged)}
    grounded = v.run_all(lesson, CURRICULUM, sources=sources)
    assert v.warnings(grounded) == []


# --- stale, failures and warnings --------------------------------------------------------------


def test_mark_stale_flips_every_check_on_the_given_blocks() -> None:
    done = v.run_all(clean_lesson(), CURRICULUM)
    stale = v.mark_stale(done, ["b_02", "b_missing"])
    assert stale is not done
    assert len(stale.blocks["b_02"].checks) == len(done.blocks["b_02"].checks) >= 2
    assert all(check.status is STALE for check in stale.blocks["b_02"].checks)
    assert names(stale.blocks["b_02"].checks) == names(done.blocks["b_02"].checks)
    assert [c.status for c in stale.blocks["b_07"].checks] == [
        c.status for c in done.blocks["b_07"].checks
    ]
    assert all(check.status is PASS for check in done.blocks["b_02"].checks)
    assert [c.status for c in stale.checks] == [c.status for c in done.checks]


def test_has_failures_looks_at_blocks_and_the_lesson() -> None:
    lesson = clean_lesson()
    assert not v.has_failures(v.run_all(lesson, CURRICULUM))
    lesson.blocks["b_07"].content.student_steps = []
    failed = v.run_all(lesson, CURRICULUM)
    assert v.has_failures(failed)
    assert not v.has_failures(v.mark_stale(failed, ["b_07"]))
    lesson_level = clean_lesson()
    lesson_level.checks.append(CheckResult(name="english.timing", status=FAIL))
    assert v.has_failures(lesson_level)


def test_warnings_list_the_lesson_first_then_blocks_in_order() -> None:
    lesson = make_lesson()
    lesson.blocks["b_02"].content.items[1].word = "giraffe"
    done = v.run_all(lesson, CURRICULUM)
    found = [(block_id, check.name) for block_id, check in v.warnings(done)]
    assert found[0] == (None, "english.timing")
    assert found[1:] == [("b_02", "english.vocab_level")]
    assert all(check.status is WARN for _, check in v.warnings(done))


# --- english.wording_kept ----------------------------------------------------------------------


def test_wording_kept_passes_when_activities_keep_the_contraction() -> None:
    results = by_block(v.wording_kept(clean_lesson(), CURRICULUM))
    assert set(results) == {"b_07", "b_08"}  # the activity and the summary
    assert all(result.status is PASS for result in results.values())


def test_wording_kept_names_the_written_out_contraction() -> None:
    lesson = clean_lesson()
    content: Any = lesson.blocks["b_07"].content
    content.instructions = "Pass the ball. Say: Hello, I am Ha."
    content.student_steps = ["Say 'Hello, I am Duc.'", "Pass it on."]
    content.teacher_steps = ["Tell pupils that I am is the long form."]  # the teacher's text
    result = by_block(v.wording_kept(lesson, CURRICULUM))["b_07"]
    assert result.status is WARN
    assert result.name == "english.wording_kept"
    assert result.detail == 'contraction written out in pupil text: "I am" (the unit writes "I\'m")'
    done = v.run_all(lesson, CURRICULUM)
    assert ("b_07", "english.wording_kept") in {(b, c.name) for b, c in v.warnings(done)}


def test_wording_kept_checks_the_summary_too() -> None:
    lesson = clean_lesson()
    lesson.blocks["b_08"].content.bullets = ["hello, hi", "Hello, I am ___."]
    result = by_block(v.wording_kept(lesson, CURRICULUM))["b_08"]
    assert result.status is WARN and '"I am"' in (result.detail or "")


def test_wording_kept_leaves_a_form_the_lesson_teaches() -> None:
    lesson = clean_lesson()
    add_block(
        lesson,
        "s_presentation",
        {
            "id": "b_20",
            "kind": "dialogue",
            "content": {"lines": [{"speaker": "Ha", "text": "I am Ha."}]},
        },
    )
    content: Any = lesson.blocks["b_07"].content
    content.student_steps = ["Say 'Hello, I am Duc.'"]
    assert v.contraction_expansions(lesson) == {}
    assert by_block(v.wording_kept(lesson, CURRICULUM))["b_07"].status is PASS


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("Hello, I'm ___.", {"i am": "I'm"}),
        ("What's your name?", {"what is": "What's"}),
        ("It's a ___.", {"it is": "It's"}),
        ("Let's play.", {"let us": "Let's"}),
        ("I don't like ___.", {"do not": "don't"}),
        ("They aren't ___.", {"are not": "aren't"}),
        ("This is Ha's ___.", {}),  # a possessive, not a contraction
        ("Nice to meet you.", {}),
    ],
)
def test_contraction_expansions_of_a_pattern(pattern: str, expected: dict[str, str]) -> None:
    lesson = clean_lesson()
    lesson.blocks["b_03"].content.pattern = pattern
    found = v.contraction_expansions(lesson)
    assert found == expected
