"""Grade 4 material: the curriculum file, two original sample units and their eval cases."""

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
from classdeck.english import validators
from classdeck.evaluation import EvalReport, load_cases, run_cases
from classdeck.ingest import ingest
from classdeck.lesson import SpanKind
from classdeck.llm import MockProvider
from classdeck.planning import lesson_runs, segment_document

ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "eval"
FIXTURES = EVAL_DIR / "fixtures" / "public"
UNITS = FIXTURES / "units"
CASE_IDS = ["en4-u1-l1-p1", "en4-u1-l3-p1", "en4-u2-l1-p1", "en4-u2-l3-p1"]
UNIT_FILES = {"ta4.u1": "gs4-unit1", "ta4.u2": "gs4-unit2"}
NAMES = frozenset({"khoa", "mi", "an", "vy", "tien", "duc", "thu", "quang", "lan"})
TEXT_FIELDS = ("pattern", "options", "lines", "words")
BRANDS = ("gemini", "openai", "muse", "meta ai", "chatgpt")


def _load_pagekit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pagekit", FIXTURES / "pagekit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pagekit = _load_pagekit()


@pytest.fixture(scope="module")
def grade4() -> Curriculum:
    return load_curriculum(ROOT / "curriculum", "gdpt-2018", 4)


@pytest.fixture(scope="module")
def grade3() -> Curriculum:
    return load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)


def _spec(stem: str) -> dict:
    return json.loads((UNITS / f"{stem}.json").read_text(encoding="utf-8"))


def _pupil_lines(spec: dict) -> list[str]:
    """Every English string a pupil sees on the pages, apart from the picture scenes."""
    lines: list[str] = []
    for lesson in spec["lessons"]:
        for activity in lesson["activities"]:
            lines.append(activity["instruction"])
            lines.extend(item["word"] for item in activity.get("items", []))
            for field in TEXT_FIELDS:
                value = activity.get(field, [])
                lines.extend([value] if isinstance(value, str) else value)
            for pair in activity.get("pairs", []):
                lines.extend(pair)
    return lines


# --- curriculum ----------


def test_curriculum_loads(grade4: Curriculum) -> None:
    file = grade4.file
    assert (file.grade, file.version, file.status, file.period_minutes) == (
        4,
        "gdpt-2018",
        "seed",
        35,
    )
    assert file.vocabulary.level == "A1"
    assert [unit.id for unit in file.units] == [f"ta4.u{n}" for n in range(1, 6)]
    for unit in file.units:
        assert 2 <= len(unit.outcomes) <= 3
        assert 1 <= len(unit.patterns) <= 3 and 1 <= len(unit.phonics) <= 2
        assert all(outcome.id.startswith(f"{unit.id}.o") for outcome in unit.outcomes)
        assert all(item.function for item in unit.patterns)


def test_word_list_is_cumulative(grade4: Curriculum, grade3: Curriculum) -> None:
    old = {word.lower() for word in grade3.file.vocabulary.words}
    new = {word.lower() for word in grade4.file.vocabulary.words}
    assert old <= new
    assert 100 <= len(new - old) <= 170
    assert grade3.file.vocabulary.classroom_language
    assert {w.lower() for w in grade3.file.vocabulary.classroom_language} <= grade4.word_list()
    assert len(new) == len(grade4.file.vocabulary.words), "duplicate word in the grade 4 list"


@pytest.mark.parametrize(
    ("rule", "sentence"),
    [
        ("perfect", "I have been to the zoo"),
        ("perfect", "She has eaten the cake"),
        ("passive", "The cake is made by Mi"),
        ("past", "We went to the park"),
        ("past", "I was tired"),
    ],
)
def test_forbidden_rules_match_what_grade_4_must_avoid(
    grade4: Curriculum, rule: str, sentence: str
) -> None:
    rules = {item.id: item for item in grade4.forbidden_rules()}
    assert rules[rule].regex.search(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "I have English on Monday",
        "She has art on Friday",
        "He has two sisters and a bike",
        "She likes swimming",
        "Can you ride a bike?",
        "What is she doing? She is cooking",
        "I get up at six o'clock",
        "There are three lamps in the room",
        "My mother is a doctor and she works in a hospital",
    ],
)
def test_forbidden_rules_leave_grade_4_sentences_alone(grade4: Curriculum, sentence: str) -> None:
    for rule in grade4.forbidden_rules():
        assert rule.regex.search(sentence) is None, (rule.id, sentence)


def test_unit_patterns_are_in_scope(grade4: Curriculum) -> None:
    for unit in grade4.file.units:
        for item in unit.patterns:
            for rule in grade4.forbidden_rules():
                assert rule.regex.search(item.pattern) is None, (unit.id, rule.id, item.pattern)


# --- unit specs and their files ----------


@pytest.fixture(scope="module", params=sorted(UNIT_FILES))
def unit(request: pytest.FixtureRequest) -> tuple[str, str]:
    return request.param, UNIT_FILES[request.param]


def test_spec_validates(unit: tuple[str, str]) -> None:
    unit_id, stem = unit
    spec = _spec(stem)
    validated = pagekit.validate_spec(spec)
    assert validated.id == stem and validated.grade == 4
    assert [lesson["no"] for lesson in spec["lessons"]] == [1, 2, 3]
    assert unit_id.endswith(f"u{spec['unit']}")
    assert [item["id"] for item in pagekit.load_specs(UNITS) if item["id"] == stem] == [stem]
    text = " ".join(_pupil_lines(spec)).lower()
    assert not [brand for brand in BRANDS if brand in text]


def test_pupil_words_are_in_the_grade_4_list(grade4: Curriculum, unit: tuple[str, str]) -> None:
    unit_id, stem = unit
    spec = _spec(stem)
    allowed = validators._forms_of_texts(grade4.word_list(unit_id)) | validators.FUNCTION_WORDS
    offenders: set[str] = set()
    for line in _pupil_lines(spec):
        for token in validators.tokenize(re.sub(r"^[abc]\.\s", "", line)):
            if token.proper or token.surface in NAMES or len(token.surface) == 1:
                continue
            forms = validators._candidate_forms(token.surface)
            if not any(form in allowed for form in forms):
                offenders.add(token.surface)
    assert offenders == set()


def test_pupil_sentences_stay_in_grammar_scope(grade4: Curriculum, unit: tuple[str, str]) -> None:
    for line in _pupil_lines(_spec(unit[1])):
        for rule in grade4.forbidden_rules():
            assert rule.regex.search(line) is None, (rule.id, line)


def test_pdf_ingests_to_six_pages_eighteen_instructions(unit: tuple[str, str]) -> None:
    _, stem = unit
    path = FIXTURES / f"{stem}.pdf"
    assert path.stat().st_size < 150_000
    document = ingest(path)
    assert [page.number for page in document.pages] == [1, 2, 3, 4, 5, 6]
    instructions = [
        span.text
        for page in document.pages
        for span in page.spans
        if span.kind is SpanKind.LIST_ITEM
    ]
    assert len(instructions) == 18
    assert all(
        page_has_image
        for page_has_image in (
            any(span.kind is SpanKind.IMAGE for span in page.spans) for page in document.pages
        )
    )
    assert len(lesson_runs(segment_document(document))) == 3


def test_pdf_matches_the_spec(unit: tuple[str, str]) -> None:
    _, stem = unit
    spec = _spec(stem)
    document = ingest(FIXTURES / f"{stem}.pdf")
    assert [
        [(span.kind.value, span.text) for span in page.spans] for page in document.pages
    ] == pagekit.text_sequence(spec)


# --- eval cases ----------


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> EvalReport:
    settings = Settings(_env_file=None, curriculum_dir=ROOT / "curriculum", provider="mock")
    cases = [
        case for case_id in CASE_IDS for case in load_cases(EVAL_DIR / "cases" / f"{case_id}.yaml")
    ]
    return run_cases(
        cases,
        settings=settings,
        provider=MockProvider(),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path_factory.mktemp("eval4"),
        timestamp="2026-10-27T10:00:00+00:00",
    )


def test_case_files_are_grade_4_and_have_two_edits() -> None:
    for case_id in CASE_IDS:
        (case,) = load_cases(EVAL_DIR / "cases" / f"{case_id}.yaml")
        assert case.id == case_id and case.grade == 4
        assert case.curriculum.unit_id in UNIT_FILES
        assert case.skipped_reason is None
        assert len(case.edits) == 2


def test_cases_pass_offline(report: EvalReport) -> None:
    assert [result.case_id for result in report.cases] == CASE_IDS
    for result in report.cases:
        assert result.status == "ok", result.reason
        assert result.metrics is not None
        failed = [key for key, ok in result.metrics.expectations.items() if not ok]
        assert not failed, (result.case_id, failed)
        assert result.metrics.vocab_violations == 0
        assert result.metrics.grammar_violations == 0
