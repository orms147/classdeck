from __future__ import annotations

from pathlib import Path

import pytest

from classdeck import english
from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.english import presets, rules, validators
from classdeck.lesson import ActivityType, BlockKind, Interaction, Phase, Skill

ROOT = Path(__file__).resolve().parents[1] / "curriculum"
CURRICULUM = load_curriculum(ROOT, "gdpt-2018", 3)


# --- phases and constants ----------------------------------------------------------------------


def test_phases_follow_the_period_and_sum_to_35() -> None:
    assert [spec.phase for spec in rules.PHASES] == list(Phase)
    assert [spec.default_minutes for spec in rules.PHASES] == [5, 10, 12, 6, 2]
    assert sum(spec.default_minutes for spec in rules.PHASES) == CURRICULUM.period_minutes == 35
    assert all(spec.description for spec in rules.PHASES)


def test_constants() -> None:
    assert rules.PERIOD_MINUTES_TOLERANCE == 2
    assert rules.GROUNDING_THRESHOLDS == (0.20, 0.60)
    assert rules.CONTENT_LANGUAGE == "en"
    assert frozenset(BlockKind) == rules.DRAFTABLE_BLOCK_KINDS
    assert len(rules.DRAFTABLE_BLOCK_KINDS) == 9
    assert {
        BlockKind.OBJECTIVE,
        BlockKind.TEACHER_NOTE,
        BlockKind.SUMMARY,
    } == rules.NON_CONTENT_KINDS


# --- lesson shapes -----------------------------------------------------------------------------


def test_lesson_shapes_match_the_global_success_table() -> None:
    shapes = rules.LESSON_SHAPES
    assert sorted(shapes) == [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2)]
    for lesson_no in (1, 2):
        assert shapes[(lesson_no, 1)] == [
            ActivityType.LOOK_LISTEN_REPEAT,
            ActivityType.LISTEN_POINT_SAY,
            ActivityType.LETS_TALK,
        ]
        assert shapes[(lesson_no, 2)] == [
            ActivityType.LISTEN_AND_DO,
            ActivityType.LOOK_COMPLETE_READ,
            ActivityType.SONG,
        ]
    assert shapes[(3, 1)] == [
        ActivityType.LOOK_LISTEN_REPEAT,
        ActivityType.LISTEN_AND_DO,
        ActivityType.CHANT,
    ]
    assert shapes[(3, 2)] == [
        ActivityType.READ_AND_MATCH,
        ActivityType.LETS_WRITE,
        ActivityType.PROJECT,
    ]
    assert {ActivityType.SONG, ActivityType.GAME} == rules.ALT_PRODUCTION


def test_lesson_shapes_do_not_share_lists() -> None:
    rules.LESSON_SHAPES[(1, 1)].append(ActivityType.GAME)
    try:
        assert len(rules.LESSON_SHAPES[(2, 1)]) == 3
    finally:
        rules.LESSON_SHAPES[(1, 1)].pop()


def test_phase_for_maps_every_activity_type() -> None:
    for activity_type in ActivityType:
        assert isinstance(rules.phase_for(activity_type), Phase)
    expected = {
        ActivityType.LOOK_LISTEN_REPEAT: Phase.PRESENTATION,
        ActivityType.LISTEN_POINT_SAY: Phase.PRACTICE,
        ActivityType.LISTEN_AND_DO: Phase.PRACTICE,
        ActivityType.LOOK_COMPLETE_READ: Phase.PRACTICE,
        ActivityType.READ_AND_MATCH: Phase.PRACTICE,
        ActivityType.LETS_TALK: Phase.PRODUCTION,
        ActivityType.LETS_WRITE: Phase.PRODUCTION,
        ActivityType.SONG: Phase.PRODUCTION,
        ActivityType.CHANT: Phase.PRODUCTION,
        ActivityType.GAME: Phase.PRODUCTION,
        ActivityType.ROLE_PLAY: Phase.PRODUCTION,
        ActivityType.PROJECT: Phase.PRODUCTION,
        ActivityType.TPR: Phase.WARM_UP,
        ActivityType.OTHER: Phase.PRACTICE,
    }
    assert {kind: rules.phase_for(kind) for kind in ActivityType} == expected


def test_every_shape_activity_lands_in_a_known_phase() -> None:
    for shape in rules.LESSON_SHAPES.values():
        assert len(shape) == 3
        assert {rules.phase_for(kind) for kind in shape} <= set(Phase)


# --- prompt rules ------------------------------------------------------------------------------


def test_prompt_rules_is_a_bullet_list_with_the_core_rules() -> None:
    text = rules.prompt_rules(3)
    lines = text.splitlines()
    assert len(lines) == 10
    assert all(line.startswith("- ") for line in lines)
    assert "imperatives" in text
    assert "perfect tenses" in text
    assert "passive" in text
    assert "reported speech" in text
    assert "at most 12 words" in text
    assert "meaning_vi" in text
    assert "`sources: []`" in text
    assert "image span" in text
    assert "write I'm, never I am" in text  # the textbook's wording is kept
    assert "TPR in classroom language" in text  # the warm-up does not drill the new pattern
    assert "Answer key" in text and "how many rounds" in text


def test_prompt_rules_scope_line_depends_on_the_grade() -> None:
    grade3, grade4, grade5 = (rules.prompt_rules(grade) for grade in (3, 4, 5))
    assert "Language scope for grade 3: present simple with be and have" in grade3
    assert "present continuous" in grade4
    assert "present continuous" not in grade3
    assert "past simple" in grade5
    assert "going to" in grade5
    assert "perfect tenses" in grade5
    assert grade3 == rules.prompt_rules(3)


def test_prompt_rules_rejects_other_grades() -> None:
    for grade in (2, 6):
        with pytest.raises(ValueError, match="grade"):
            rules.prompt_rules(grade)


def test_prompt_rules_takes_the_scope_from_the_curriculum() -> None:
    text = rules.prompt_rules(3, CURRICULUM)
    allowed = CURRICULUM.file.grammar_scope.allowed
    assert allowed
    assert "; ".join(allowed) in text
    assert "What's ...? questions" not in text
    assert "perfect tenses" in text


def test_prompt_rules_falls_back_when_the_curriculum_lists_no_scope() -> None:
    file = CURRICULUM.file.model_copy(deep=True)
    file.grammar_scope.allowed = []
    assert rules.prompt_rules(3, Curriculum(file)) == rules.prompt_rules(3)


# --- activity templates ------------------------------------------------------------------------


def test_every_activity_type_has_a_template() -> None:
    templates = rules.load_activity_templates()
    assert list(templates) == list(ActivityType)
    for template in templates.values():
        assert isinstance(template, rules.ActivityTemplate)
        assert template.default_skills
        assert all(isinstance(skill, Skill) for skill in template.default_skills)
        assert isinstance(template.default_interaction, Interaction)
        assert template.default_minutes >= 1
        assert template.title_template
        assert template.instructions_template
        assert template.teacher_steps
        assert template.student_steps


def test_templates_use_the_documented_placeholders() -> None:
    templates = rules.load_activity_templates()
    text = " ".join(
        [t.instructions_template for t in templates.values()]
        + [step for t in templates.values() for step in t.teacher_steps + t.student_steps]
    )
    for placeholder in ("{{pattern}}", "{{words}}", "{{sound}}"):
        assert placeholder in text
    assert "{{sound}}" in " ".join(templates[ActivityType.CHANT].teacher_steps)


def test_loading_templates_returns_a_fresh_dict() -> None:
    first = rules.load_activity_templates()
    first.pop(ActivityType.GAME)
    assert ActivityType.GAME in rules.load_activity_templates()


def test_a_template_file_missing_a_type_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    real = rules.yaml.safe_load(
        rules.resources.files("classdeck.english")
        .joinpath("templates", "activities.yaml")
        .read_text(encoding="utf-8")
    )
    del real["templates"]["chant"]
    rules._read_templates.cache_clear()
    monkeypatch.setattr(rules.yaml, "safe_load", lambda _text: real)
    with pytest.raises(ValueError, match="chant"):
        rules.load_activity_templates()
    monkeypatch.undo()
    rules._read_templates.cache_clear()
    assert ActivityType.CHANT in rules.load_activity_templates()


# --- edit presets ------------------------------------------------------------------------------


def test_there_are_eight_presets_with_unique_ids() -> None:
    assert len(presets.EDIT_PRESETS) == 8
    assert len({preset.id for preset in presets.EDIT_PRESETS}) == 8
    for preset in presets.EDIT_PRESETS:
        assert preset.label_en
        assert preset.label_vi
        assert preset.instruction
        assert preset.applies_to


def test_presets_that_take_a_number_keep_the_placeholder() -> None:
    numbered = {p.id for p in presets.EDIT_PRESETS if p.takes_number}
    assert numbered == {"reduce_words", "shorten_minutes"}
    for preset in presets.EDIT_PRESETS:
        assert ("N" in preset.label_en.split()) == preset.takes_number
        assert ("{{n}}" in preset.instruction) == preset.takes_number


def test_render_fills_the_number() -> None:
    preset = next(p for p in presets.EDIT_PRESETS if p.id == "shorten_minutes")
    assert "{{n}}" not in preset.render(4)
    assert preset.render(4).count("4") == 2
    with pytest.raises(ValueError, match="number"):
        preset.render()
    plain = next(p for p in presets.EDIT_PRESETS if p.id == "pair_work")
    assert plain.render() == plain.instruction


def test_presets_for_a_kind() -> None:
    activity_ids = {p.id for p in presets.presets_for(BlockKind.ACTIVITY)}
    assert activity_ids >= {"pair_work", "whole_class_game", "add_listening", "add_tpr"}
    assert next(p.id for p in presets.presets_for(BlockKind.VOCABULARY)) == "reduce_words"
    assert "bilingual_notes" in {p.id for p in presets.presets_for(BlockKind.TEACHER_NOTE)}
    assert presets.presets_for(BlockKind.IMAGE_REF) == []
    assert presets.presets_for(BlockKind.OBJECTIVE) == []


# --- package surface ---------------------------------------------------------------------------


def test_package_reexports_the_public_names() -> None:
    for name in english.__all__:
        assert hasattr(english, name)
    assert english.run_all is validators.run_all
    assert english.prompt_rules is rules.prompt_rules
    assert english.presets_for is presets.presets_for
    assert next(fn.__name__ for fn in english.ALL_VALIDATORS) == "vocab_level"
