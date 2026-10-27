"""The five grade-3 practice units (eval/fixtures/public/units/gs3-unit*.json) and their cases.

Each unit is original material in the usual shape (three lessons of six activities). The tests
check that the spec validates, that the committed PDF is what the spec renders to, that the PDF
ingests into the expected activity structure, that every pupil-facing word is on the unit's
word list, and that the eval cases run offline.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from classdeck.config import Settings
from classdeck.curriculum import load_curriculum
from classdeck.english import validators
from classdeck.evaluation import EvalReport, load_cases, run_cases
from classdeck.ingest import ingest
from classdeck.lesson import Lesson, SpanKind
from classdeck.llm import MockProvider
from classdeck.planning import lesson_runs, segment_document

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "public"
EVAL_DIR = ROOT / "eval"
CASES_DIR = EVAL_DIR / "cases"
UNIT_NUMBERS = [1, 2, 3, 4, 5]
MAX_PDF_BYTES = 150 * 1024

NAMES = frozenset({"ha", "tien", "duc", "lan", "khoa", "mi", "an", "vy", "quang", "thu"})
"""The invented children's names; a name at the start of a line is not a proper noun to the
tokenizer, so the check lists them."""

PIPELINE_WORDS = frozenset({"catch", "pass", "sound", "rhythm"})
"""Words the offline pipeline's own templates add to an activity (the warm-up ball game and the
chant steps). They are not in any fixture; see the offline-cases test below."""


def _load_pagekit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pagekit", FIXTURES / "pagekit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve postponed annotations via sys.modules
    spec.loader.exec_module(module)
    return module


pagekit = _load_pagekit()
CURRICULUM = load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)


def _unit_id(n: int) -> str:
    return f"gs3-unit{n}"


def _spec(n: int) -> dict:
    return json.loads((FIXTURES / "units" / f"{_unit_id(n)}.json").read_text(encoding="utf-8"))


def _pupil_texts(spec: dict) -> Iterator[str]:
    """Every string a pupil sees or hears on the pages of the unit (headings included)."""
    yield spec["title"]
    for lesson in spec["lessons"]:
        for activity in lesson["activities"]:
            yield activity["instruction"]
            for key in ("pattern", "letters"):
                if activity.get(key):
                    yield activity[key]
            yield from (item["word"] for item in activity.get("items", []))
            for key in ("words", "options", "lines"):
                yield from activity.get(key, [])
            for left, right in activity.get("pairs", []):
                yield left
                yield right


def _is_exempt(word: str) -> bool:
    """Names, single letters (spelling and phonics) and the digraph ``ch``."""
    return word in NAMES or len(word) == 1 or word == "ch"


def _offenders(spec: dict, unit_id: str) -> list[str]:
    allowed = validators._forms_of_texts(CURRICULUM.word_list(unit_id)) | validators.FUNCTION_WORDS
    found: set[str] = set()
    for text in _pupil_texts(spec):
        text = re.sub(r"^[a-c]\.\s", "", text)  # option letters
        if text.startswith("/"):
            continue
        for token in validators.tokenize(text):
            word = token.surface
            if token.proper or _is_exempt(word):
                continue
            if not any(form in allowed for form in validators._candidate_forms(word)):
                found.add(word)
    return sorted(found)


@pytest.mark.parametrize("n", UNIT_NUMBERS)
def test_spec_validates_and_has_the_unit_shape(n: int) -> None:
    spec = _spec(n)
    unit = pagekit.validate_spec(spec)
    assert unit.id == _unit_id(n)
    assert (unit.grade, unit.unit) == (3, n)
    assert unit.title == CURRICULUM.unit(f"ta3.u{n}").title
    assert [lesson.no for lesson in unit.lessons] == [1, 2, 3]
    first, second, third = unit.lessons
    for lesson in (first, second):
        assert [a.instruction for a in lesson.activities][:5] == [
            "Look, listen and repeat.",
            "Listen, point and say.",
            "Let's talk.",
            lesson.activities[3].instruction,
            "Look, complete and read.",
        ]
        assert lesson.activities[3].instruction in {"Listen and tick.", "Listen and number."}
        assert lesson.activities[5].instruction in {"Let's sing.", "Let's play."}
    assert [a.instruction for a in third.activities] == [
        "Listen and repeat.",
        "Listen and circle.",
        "Let's chant.",
        "Read and match.",
        "Let's write.",
        "Project.",
    ]
    assert len(third.activities[3].pairs) == 3
    assert len(third.activities[2].lines) == 4
    assert len(first.activities[5].lines) == 4


@pytest.mark.parametrize("n", UNIT_NUMBERS)
def test_committed_pdf_matches_the_spec(n: int, tmp_path: Path) -> None:
    committed = FIXTURES / f"{_unit_id(n)}.pdf"
    assert committed.is_file(), "run eval/fixtures/public/make_fixtures.py"
    assert committed.stat().st_size < MAX_PDF_BYTES
    rendered = pagekit.render_unit(_spec(n), tmp_path, formats=("pdf",))["pdf"]
    assert rendered.read_bytes() == committed.read_bytes()


@pytest.mark.parametrize("n", UNIT_NUMBERS)
def test_pdf_ingests_to_three_lessons_of_six_activities(n: int) -> None:
    document = ingest(FIXTURES / f"{_unit_id(n)}.pdf")
    assert [page.number for page in document.pages] == [1, 2, 3, 4, 5, 6]
    for page in document.pages:
        assert any(span.kind is SpanKind.IMAGE for span in page.spans), f"page {page.number}"
    chunks = segment_document(document)
    assert [chunk.number for chunk in chunks] == [1, 2, 3, 4, 5, 6] * 3
    runs = lesson_runs(chunks)
    assert [[chunk.number for chunk in run] for run in runs] == [[1, 2, 3, 4, 5, 6]] * 3


@pytest.mark.parametrize("n", UNIT_NUMBERS)
def test_pupil_text_is_on_the_unit_word_list(n: int) -> None:
    assert _offenders(_spec(n), f"ta3.u{n}") == []


@pytest.mark.parametrize("n", UNIT_NUMBERS)
def test_pupil_text_stays_in_grade_three_grammar(n: int) -> None:
    rules = CURRICULUM.forbidden_rules()
    hits = [
        (rule.id, text)
        for text in _pupil_texts(_spec(n))
        for rule in rules
        if rule.regex.search(text)
    ]
    assert hits == []


def test_no_textbook_character_names() -> None:
    banned = {"bill", "mai", "nam", "ba", "lucy", "linh", "minh", "hoa"}
    for n in UNIT_NUMBERS:
        words = {
            token.lower()
            for text in _pupil_texts(_spec(n))
            for token in re.findall(r"[A-Za-z]+", text)
        }
        assert not words & banned, (n, words & banned)


# --- eval cases --------------------------------------------------------------------------------

CASE_IDS = [f"en3-u{n}-l{lesson}-p1" for n in UNIT_NUMBERS for lesson in (1, 3)]
TIMESTAMP = "2026-10-27T10:00:00+00:00"


@pytest.fixture(scope="module")
def offline_report(tmp_path_factory: pytest.TempPathFactory) -> EvalReport:
    settings = Settings(_env_file=None, curriculum_dir=ROOT / "curriculum", provider="mock")
    cases = [case for case in load_cases(CASES_DIR) if case.id in CASE_IDS]
    return run_cases(
        cases,
        settings=settings,
        provider=MockProvider(),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path_factory.mktemp("eval-grade3"),
        timestamp=TIMESTAMP,
    )


def test_cases_load_and_point_at_their_unit() -> None:
    cases = {case.id: case for case in load_cases(CASES_DIR) if case.id in CASE_IDS}
    assert sorted(cases) == sorted(CASE_IDS)
    for n in UNIT_NUMBERS:
        outcomes = [o.id for o in CURRICULUM.unit(f"ta3.u{n}").outcomes]
        for lesson, pages in ((1, [1, 2]), (3, [5, 6])):
            case = cases[f"en3-u{n}-l{lesson}-p1"]
            assert case.skipped_reason is None
            assert case.source == f"fixtures/public/{_unit_id(n)}.pdf"
            assert case.pages == pages
            assert case.curriculum.unit_id == f"ta3.u{n}"
            assert (case.curriculum.lesson_no, case.curriculum.period) == (lesson, 1)
            # at least one of the unit's leading outcomes; a phonics period may cover only one
            assert case.expect.must_cover_outcomes
            assert set(case.expect.must_cover_outcomes) <= set(outcomes[:2])
            assert case.expect.max_vocab_violations == 0
            assert case.expect.max_grammar_violations == 0
            assert case.expect.min_grounded_ratio == (0.6 if lesson == 1 else 0.5)
            assert len(case.edits) == 2


def test_offline_cases_pass_every_expectation(offline_report: EvalReport) -> None:
    """The template pipeline meets every case expectation offline: grounded ratio, no
    out-of-list words (classroom language covers the templates' own words), no grammar hits,
    outcomes and skills covered, 35 minutes."""
    assert [result.case_id for result in offline_report.cases] == CASE_IDS
    for result in offline_report.cases:
        assert result.status == "ok", result.reason
        metrics = result.metrics
        assert metrics is not None and metrics.expectations
        failed = {key for key, ok in metrics.expectations.items() if not ok}
        assert not failed, (result.case_id, failed)
        assert metrics.vocab_violations == 0
        assert metrics.grammar_violations == 0
        assert metrics.timing_ok is True
        assert result.lesson_path is not None
        lesson = Lesson.model_validate_json(Path(result.lesson_path).read_text(encoding="utf-8"))
        assert lesson.blocks
