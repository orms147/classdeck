"""Grade 5 material: curriculum file, the two public sample units (gs5-unit1, gs5-unit2) and the
offline evaluation cases built on them."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

from classdeck.config import Settings
from classdeck.curriculum import Curriculum, load_curriculum
from classdeck.english.validators import (
    FUNCTION_WORDS,
    _candidate_forms,
    _forms_of_texts,
    tokenize,
)
from classdeck.evaluation import EvalReport, load_cases, run_cases
from classdeck.ingest import ingest
from classdeck.lesson import SpanKind
from classdeck.llm import MockProvider
from classdeck.planning import lesson_runs, segment_document

ROOT = Path(__file__).resolve().parents[1]
CURRICULUM_DIR = ROOT / "curriculum"
EVAL_DIR = ROOT / "eval"
FIXTURES = EVAL_DIR / "fixtures" / "public"
UNITS = FIXTURES / "units"
VERSION = "gdpt-2018"

UNIT_OF = {"gs5-unit1": "ta5.u1", "gs5-unit2": "ta5.u2"}
CHARACTERS = {"vy", "quang", "thu", "an", "khoa", "mi"}
CASE_IDS = ["en5-u1-l1-p1", "en5-u1-l3-p1", "en5-u2-l1-p1", "en5-u2-l3-p1"]
MAX_PDF_BYTES = 150 * 1024


def _load_pagekit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pagekit", FIXTURES / "pagekit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve postponed annotations via sys.modules
    spec.loader.exec_module(module)
    return module


pagekit = _load_pagekit()


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CURRICULUM_DIR, VERSION, 5)


@pytest.fixture(scope="module")
def grade3() -> Curriculum:
    return load_curriculum(CURRICULUM_DIR, VERSION, 3)


def _spec(unit: str) -> dict:
    return json.loads((UNITS / f"{unit}.json").read_text(encoding="utf-8"))


def _pupil_texts(spec: dict) -> list[str]:
    """Every string a pupil reads in the unit, with the "a." / "b." option labels removed."""
    texts: list[str] = []
    for lesson in spec["lessons"]:
        for activity in lesson["activities"]:
            texts.append(activity["instruction"])
            texts.extend(activity.get(key, "") for key in ("pattern",))
            texts.extend(item["word"] for item in activity.get("items", []))
            texts.extend(activity.get("options", []))
            texts.extend(activity.get("words", []))
            texts.extend(activity.get("lines", []))
            for pair in activity.get("pairs", []):
                texts.extend(pair)
    return [re.sub(r"^[a-z]\.\s+", "", text) for text in texts if text]


# --- the curriculum file -----------------------------------------------------------------------


def test_curriculum_header_and_units(curriculum: Curriculum) -> None:
    file = curriculum.file
    assert (file.version, file.subject, file.grade, file.status) == (
        VERSION,
        "tieng-anh",
        5,
        "seed",
    )
    assert file.period_minutes == 35
    assert file.vocabulary.level == "A1"
    assert [unit.id for unit in file.units] == [f"ta5.u{n}" for n in range(1, 6)]
    for unit in file.units:
        assert unit.lessons == 3
        assert 2 <= len(unit.outcomes) <= 3
        assert [o.id for o in unit.outcomes] == [
            f"{unit.id}.o{n}" for n in range(1, len(unit.outcomes) + 1)
        ]
        assert all(o.skills for o in unit.outcomes)
        assert 1 <= len(unit.patterns) <= 4 and all(p.function for p in unit.patterns)
        assert 1 <= len(unit.phonics) <= 2
        assert unit.vocabulary


def test_word_list_extends_grade_three(curriculum: Curriculum, grade3: Curriculum) -> None:
    words = curriculum.file.vocabulary.words
    assert len(words) == len(set(words)) and all(w == w.lower() for w in words)
    assert set(grade3.file.vocabulary.words) <= set(words)
    assert grade3.word_list() <= curriculum.word_list()
    assert 380 <= len(words) <= 520
    # grade 4 themes and grade 5 themes are both present
    assert {"monday", "october", "kitchen", "hospital", "doctor", "windy", "ninety"} <= set(words)
    assert {"yesterday", "ago", "holiday", "future", "healthy", "bigger", "because"} <= set(words)
    # instruction language of the six-activity pattern
    assert {"talk", "project", "circle", "match", "tick", "chant"} <= curriculum.word_list()


def test_forbidden_rules_are_perfect_and_passive_only(curriculum: Curriculum) -> None:
    assert [rule.id for rule in curriculum.forbidden_rules()] == ["perfect", "passive"]


def _hits(curriculum: Curriculum, sentence: str) -> list[str]:
    return [rule.id for rule in curriculum.forbidden_rules() if rule.regex.search(sentence)]


@pytest.mark.parametrize(
    "sentence",
    [
        "I visited my grandmother last weekend.",
        "We went to the zoo and saw a big bird.",
        "She ate noodles and watched TV.",
        "Did you play football? No, I did not.",
        "I had a headache yesterday.",
        "I'm going to be a pilot. I'll help you.",
        "Fruit is healthier than sweets.",
        "I went there by boat two days ago.",
    ],
)
def test_in_scope_sentences_are_not_flagged(curriculum: Curriculum, sentence: str) -> None:
    assert _hits(curriculum, sentence) == []


@pytest.mark.parametrize(
    ("sentence", "rule"),
    [
        ("I have visited Hue.", "perfect"),
        ("She has eaten the cake.", "perfect"),
        ("We've seen the sea.", "perfect"),
        ("The cake was made by Mi.", "passive"),
        ("The room is cleaned by Khoa.", "passive"),
    ],
)
def test_out_of_scope_sentences_are_flagged(
    curriculum: Curriculum, sentence: str, rule: str
) -> None:
    assert rule in _hits(curriculum, sentence)


def test_grade_three_still_forbids_past_simple(grade3: Curriculum) -> None:
    assert "past" in [rule.id for rule in grade3.forbidden_rules()]


# --- the two sample units ----------------------------------------------------------------------


@pytest.mark.parametrize("unit", UNIT_OF)
def test_spec_validates_and_is_plain_ascii(unit: str) -> None:
    spec = _spec(unit)
    pagekit.validate_spec(spec)
    assert spec["id"] == unit and spec["grade"] == 5
    assert [lesson["no"] for lesson in spec["lessons"]] == [1, 2, 3]
    assert all(len(lesson["activities"]) == 6 for lesson in spec["lessons"])
    assert all(text.isascii() for text in _pupil_texts(spec))


@pytest.mark.parametrize("unit", UNIT_OF)
def test_activity_shapes_follow_the_course_pattern(unit: str) -> None:
    lessons = _spec(unit)["lessons"]
    for lesson in lessons[:2]:
        assert [a["type"] for a in lesson["activities"]][:5] == [
            "look_listen_repeat",
            "listen_point_say",
            "lets_talk",
            "listen_and_do",
            "look_complete_read",
        ]
        assert lesson["activities"][5]["type"] in {"song", "game"}
    assert [a["type"] for a in lessons[2]["activities"]] == [
        "phonics",
        "listen_and_do",
        "chant",
        "read_and_match",
        "lets_write",
        "project",
    ]
    assert len(lessons[2]["activities"][3]["pairs"]) == 3


@pytest.mark.parametrize("unit", UNIT_OF)
def test_every_pupil_word_is_in_scope(unit: str, curriculum: Curriculum) -> None:
    allowed = _forms_of_texts(curriculum.word_list(UNIT_OF[unit])) | FUNCTION_WORDS | CHARACTERS
    offenders = {
        token.surface
        for text in _pupil_texts(_spec(unit))
        for token in tokenize(text)
        if not token.proper and not any(form in allowed for form in _candidate_forms(token.surface))
    }
    assert offenders == set()


@pytest.mark.parametrize("unit", UNIT_OF)
def test_no_pupil_sentence_leaves_the_grammar_scope(unit: str, curriculum: Curriculum) -> None:
    for text in _pupil_texts(_spec(unit)):
        assert _hits(curriculum, text) == [], text


@pytest.mark.parametrize("unit", UNIT_OF)
def test_pdf_ingests_to_six_pages_eighteen_instructions_three_lessons(
    unit: str, tmp_path: Path
) -> None:
    committed = FIXTURES / f"{unit}.pdf"
    assert committed.stat().st_size < MAX_PDF_BYTES
    rendered = pagekit.render_unit(_spec(unit), tmp_path, formats=("pdf",))["pdf"]
    assert rendered.read_bytes() == committed.read_bytes(), "re-run make_fixtures.py"

    document = ingest(committed)
    assert [page.number for page in document.pages] == [1, 2, 3, 4, 5, 6]
    for page in document.pages:
        assert any(span.kind is SpanKind.IMAGE for span in page.spans), page.number
    instructions = [
        span for page in document.pages for span in page.spans if span.kind is SpanKind.LIST_ITEM
    ]
    assert len(instructions) == 18
    runs = lesson_runs(segment_document(document))
    assert [[chunk.number for chunk in run] for run in runs] == [[1, 2, 3, 4, 5, 6]] * 3


@pytest.mark.parametrize("unit", UNIT_OF)
def test_office_files_are_committed(unit: str) -> None:
    for extension in ("docx", "pptx"):
        assert (FIXTURES / f"{unit}.{extension}").is_file()


# --- evaluation cases --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> EvalReport:
    settings = Settings(_env_file=None, curriculum_dir=CURRICULUM_DIR, provider="mock")
    cases = [case for case in load_cases(EVAL_DIR / "cases") if case.id in CASE_IDS]
    return run_cases(
        cases,
        settings=settings,
        provider=MockProvider(),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path_factory.mktemp("eval5"),
        timestamp="2026-01-01T00:00:00+00:00",
    )


def test_cases_load_with_two_edits_each() -> None:
    cases = {case.id: case for case in load_cases(EVAL_DIR / "cases") if case.id in CASE_IDS}
    assert sorted(cases) == sorted(CASE_IDS)
    for case in cases.values():
        assert case.grade == 5 and case.skipped_reason is None
        assert case.curriculum.unit_id in UNIT_OF.values()
        assert len(case.edits) == 2


def test_cases_pass_offline(report: EvalReport) -> None:
    assert sorted(result.case_id for result in report.cases) == sorted(CASE_IDS)
    for result in report.cases:
        assert result.status == "ok", result.reason
        assert result.metrics is not None
        failed = [key for key, ok in result.metrics.expectations.items() if not ok]
        assert result.metrics.passed, f"{result.case_id} failed {failed}"
        assert result.metrics.vocab_violations == 0
        assert result.metrics.grammar_violations == 0


def test_word_list_extends_grade_four_when_present(curriculum: Curriculum) -> None:
    try:
        grade4 = load_curriculum(CURRICULUM_DIR, VERSION, 4)
    except FileNotFoundError:
        pytest.skip("no grade 4 curriculum file")
    assert grade4.word_list() <= curriculum.word_list()
