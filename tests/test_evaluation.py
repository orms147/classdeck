from __future__ import annotations

import json
from pathlib import Path

import pytest

from classdeck.config import Settings
from classdeck.evaluation import (
    CRITERIA,
    EvalReport,
    JudgeScores,
    compare,
    judge_lesson,
    load_cases,
    run_cases,
)
from classdeck.evaluation.cases import EvalCase
from classdeck.evaluation.judge import render_lesson
from classdeck.evaluation.runner import changed_block_ids
from classdeck.lesson import Lesson, SourceStore
from classdeck.llm import MockProvider

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"
CASES_DIR = EVAL_DIR / "cases"
CASE_IDS = ["en3-u1-l1-p1-public", "en3-u1-l1-p2-public"]
# The two public mini-unit cases; eval/cases also holds the grade 3 to 5 unit cases.
PUBLIC_CASE_FILES = [CASES_DIR / f"{case_id}.yaml" for case_id in CASE_IDS]


def _public_cases() -> list[EvalCase]:
    return [case for path in PUBLIC_CASE_FILES for case in load_cases(path)]


TIMESTAMP = "2026-10-27T10:00:00+00:00"


@pytest.fixture(scope="module")
def settings() -> Settings:
    return Settings(_env_file=None, curriculum_dir=ROOT / "curriculum", provider="mock")


@pytest.fixture(scope="module")
def offline_report(settings: Settings, tmp_path_factory: pytest.TempPathFactory) -> EvalReport:
    return run_cases(
        _public_cases(),
        settings=settings,
        provider=MockProvider(),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path_factory.mktemp("eval"),
        timestamp=TIMESTAMP,
    )


def _rubric(score: int = 4) -> str:
    return json.dumps({name: {"score": score, "justification": f"{name} ok"} for name in CRITERIA})


def _first_lesson(report: EvalReport) -> Lesson:
    path = report.cases[0].lesson_path
    assert path is not None
    return Lesson.model_validate_json(Path(path).read_text(encoding="utf-8"))


def test_load_cases_from_directory_and_file() -> None:
    cases = load_cases(CASES_DIR)
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))  # one case per file, loaded in file-name order
    assert ids == [case.id for case in load_cases(CASES_DIR)]  # deterministic
    assert set(CASE_IDS) <= set(ids)
    first = cases[0]
    assert first.curriculum.unit_id == "ta3.u1"
    assert first.pages == [1, 2]
    assert first.expect.must_cover_skills == ["listening", "speaking"]
    assert [edit.target_kind for edit in first.edits] == ["vocabulary", "activity"]
    assert first.skipped_reason is None
    single = load_cases(CASES_DIR / "en3-u1-l1-p2-public.yaml")
    assert [case.id for case in single] == [CASE_IDS[1]]


def test_missing_fixture_is_skipped(settings: Settings, tmp_path: Path) -> None:
    case_file = tmp_path / "cases" / "private.yaml"
    case_file.parent.mkdir()
    case_file.write_text(
        "id: en3-private\ngrade: 3\n"
        "curriculum: { version: gdpt-2018, unit_id: ta3.u1, lesson_no: 1, period: 1 }\n"
        "source: fixtures/private/none.pdf\n",
        encoding="utf-8",
    )
    cases = load_cases(case_file)
    assert cases[0].skipped_reason == "missing fixture: fixtures/private/none.pdf"
    report = run_cases(
        cases,
        settings=settings,
        provider=MockProvider(),
        eval_dir=tmp_path,
        workdir=tmp_path / "out",
        timestamp=TIMESTAMP,
    )
    assert report.cases[0].status == "skipped"
    assert "missing fixture" in (report.cases[0].reason or "")
    assert report.summary["skipped"] == 1
    assert report.summary["ran"] == 0
    assert "skipped" in report.table()


def test_unknown_case_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        EvalCase.model_validate(
            {
                "id": "x",
                "grade": 3,
                "curriculum": {"version": "v", "unit_id": "u", "lesson_no": 1, "period": 1},
                "source": "a.pdf",
                "typo": 1,
            }
        )


def test_offline_run_populates_metrics(offline_report: EvalReport) -> None:
    assert [case.case_id for case in offline_report.cases] == CASE_IDS
    for result in offline_report.cases:
        assert result.status == "ok", result.reason
        metrics = result.metrics
        assert metrics is not None
        assert 0.0 <= (metrics.grounded_ratio or 0) <= 1.0
        assert metrics.ai_added_ratio is not None
        assert metrics.vocab_violations is not None
        assert metrics.grammar_violations is not None
        assert metrics.skill_coverage == 1.0
        assert metrics.outcome_coverage == 1.0
        assert metrics.timing_ok is True
        assert {"ingest", "outline", "draft", "grounding", "validators"} <= set(metrics.latency_s)
        assert metrics.tokens_in == 0
        assert metrics.expectations
        assert metrics.passed == all(metrics.expectations.values())
        assert result.lesson_path is not None and Path(result.lesson_path).is_file()
        failed = [key for key, ok in metrics.expectations.items() if not ok]
        print(result.case_id, "failed expectations:", failed or "none")


def test_offline_edits_and_judge_are_skipped_not_faked(offline_report: EvalReport) -> None:
    for result in offline_report.cases:
        assert result.edits
        assert all(edit.status == "skipped" and edit.reason for edit in result.edits)
        metrics = result.metrics
        assert metrics is not None
        assert metrics.edit_locality is None
        assert metrics.edit_success is None
        assert metrics.edit_note
        assert metrics.judge_source == "skipped"
        assert metrics.judge_mean is None
        assert not any(key.startswith("edit[") for key in metrics.expectations)


def test_report_round_trips(offline_report: EvalReport, tmp_path: Path) -> None:
    path = offline_report.write(tmp_path / "results" / "run.json")
    assert EvalReport.read(path) == offline_report
    assert EvalReport.from_json(offline_report.to_json()) == offline_report
    assert offline_report.timestamp == TIMESTAMP
    assert offline_report.summary["ran"] == 2


def test_table_lists_case_ids(offline_report: EvalReport) -> None:
    table = offline_report.table()
    for case_id in CASE_IDS:
        assert case_id in table
    assert "mean" in table
    assert "provider=mock" in table


def test_compare_two_reports(offline_report: EvalReport, tmp_path: Path) -> None:
    first = offline_report.write(tmp_path / "a.json")
    changed = offline_report.model_copy(deep=True)
    assert changed.cases[0].metrics is not None
    changed.cases[0].metrics.grounded_ratio = 1.0
    changed.summary["mean_grounded_ratio"] = 0.9
    second = changed.write(tmp_path / "b.json")
    text = compare(first, second)
    for case_id in CASE_IDS:
        assert case_id in text
    assert "grounded_ratio" in text
    assert "->" in text
    assert "summary" in text


def test_judge_with_scripted_provider(offline_report: EvalReport) -> None:
    lesson = _first_lesson(offline_report)
    provider = MockProvider([_rubric(4)])
    scores = judge_lesson(lesson, {}, provider, independent=True)
    assert scores.source == "independent"
    assert scores.mean == 4.0
    assert set(scores.scores()) == set(CRITERIA)
    assert scores.justifications()["flow"] == "flow ok"
    prompt = provider.calls[0]["messages"][0]["content"]
    assert lesson.title in prompt
    assert "faithfulness" in prompt

    generator = judge_lesson(lesson, {}, MockProvider([_rubric(2)]))
    assert generator.source == "generator"
    assert generator.mean == 2.0


def test_judge_is_skipped_for_plain_mock(offline_report: EvalReport) -> None:
    lesson = _first_lesson(offline_report)
    for provider in (MockProvider(), None):
        scores = judge_lesson(lesson, {}, provider)
        assert isinstance(scores, JudgeScores)
        assert scores.source == "skipped"
        assert scores.mean is None


def test_judge_prompt_shows_cited_spans(offline_report: EvalReport) -> None:
    result = offline_report.cases[0]
    assert result.lesson_path is not None
    lesson = _first_lesson(offline_report)
    store = SourceStore(Path(result.lesson_path).parent.parent)
    document = store.load(lesson.sources[0].doc_id)
    text = render_lesson(lesson, {document.doc_id: document})
    assert "SOURCE p" in text
    assert "(span not found)" not in text


def test_scripted_judge_flows_into_case_metrics(settings: Settings, tmp_path: Path) -> None:
    cases = [case for case in _public_cases() if case.id == CASE_IDS[0]]
    judge = MockProvider(handler=lambda _messages: _rubric(5))
    report = run_cases(
        cases,
        settings=settings,
        provider=MockProvider(),
        judge_provider=judge,
        eval_dir=EVAL_DIR,
        workdir=tmp_path,
        timestamp=TIMESTAMP,
    )
    result = report.cases[0]
    metrics = result.metrics
    assert metrics is not None
    assert metrics.judge_mean == 5.0
    assert metrics.judge_faithfulness == 5
    assert metrics.judge_source == "generator"
    assert metrics.judge_tokens_in > 0
    assert result.judge_justifications["flow"] == "flow ok"


def test_failed_edits_are_counted_not_faked(settings: Settings, tmp_path: Path) -> None:
    cases = [case for case in _public_cases() if case.id == CASE_IDS[1]]
    # A provider that only returns prose: model calls fall back or fail validation.
    provider = MockProvider(handler=lambda _messages: "not json")
    report = run_cases(
        cases,
        settings=settings,
        provider=provider,
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path,
        timestamp=TIMESTAMP,
    )
    result = report.cases[0]
    assert result.status == "ok", result.reason
    assert result.edits
    assert all(edit.status == "rejected" for edit in result.edits)
    assert result.metrics is not None
    assert result.metrics.edit_success == 0.0
    assert result.metrics.tokens_in > 0
    assert result.metrics.passed is False


def test_changed_block_ids_ignores_checks(offline_report: EvalReport) -> None:
    lesson = _first_lesson(offline_report)
    other = lesson.model_copy(deep=True)
    for block in other.blocks.values():
        block.checks = []
    assert changed_block_ids(lesson, other) == []
    victim = next(b for b in other.blocks.values() if b.kind == "objective")
    victim.content.text = victim.content.text + " (changed)"
    assert changed_block_ids(lesson, other) == [victim.id]


def _editing_handler(messages: list) -> str:
    """Answers edit requests with a small valid change and everything else with prose."""
    text = messages[0].content if isinstance(messages[0].content, str) else ""
    for message in messages:
        if isinstance(message.content, str) and "## Target blocks" in message.content:
            text = message.content
    if "## Target blocks" not in text:
        return "not json"
    start = text.index("```json", text.index("## Target blocks")) + len("```json")
    block = json.loads(text[start : text.index("```", start)])[0]
    if block["kind"] == "activity":
        block["content"]["interaction"] = "pairs"
    elif block["kind"] == "vocabulary":
        block["content"]["items"] = block["content"]["items"][:2]
    return json.dumps({"replacements": {block["id"]: block}, "rationale": "test edit"})


def test_applied_edits_are_measured(settings: Settings, tmp_path: Path) -> None:
    cases = [case for case in _public_cases() if case.id == CASE_IDS[0]]
    report = run_cases(
        cases,
        settings=settings,
        provider=MockProvider(handler=_editing_handler),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path,
        timestamp=TIMESTAMP,
    )
    result = report.cases[0]
    assert result.status == "ok", result.reason
    assert [edit.status for edit in result.edits] == ["applied", "applied"]
    for edit in result.edits:
        assert edit.locality is True
        assert set(edit.changed_block_ids) <= set(edit.target_block_ids)
    assert result.edits[1].changed_block_ids == result.edits[1].target_block_ids
    metrics = result.metrics
    assert metrics is not None
    assert metrics.edit_locality == 1.0
    assert metrics.edit_success is not None
    assert metrics.expectations["edit[1].changed_blocks"] is True
    assert metrics.expectations["edit[1].interaction"] is True
    assert metrics.expectations["edit[0].edit_locality"] is True


def test_the_runner_builds_with_the_repair_round(settings: Settings, tmp_path: Path) -> None:
    """``build_lesson(repair=True)``: an offending model block is rewritten once, in the saved
    lesson, and the repair's tokens are attributed to the generator."""
    repair_prompts: list[str] = []
    offending = "Salute the contestant with Hello or Hi."
    clean = "Say Hello or Hi to your partner."

    def handler(messages: list) -> str:
        text = messages[0].content
        if not isinstance(text, str):
            return "not json"
        if text.startswith("You fix one block"):
            repair_prompts.append(text)
            block = json.loads(text.split("BLOCK (JSON)\n", 1)[1].split("\n\nPROBLEMS", 1)[0])
            block["content"]["instructions"] = clean
            block["content"]["student_steps"] = ["Listen.", clean]
            return json.dumps(block)
        if '"phase": "warm_up"' in text and "SOURCE SPANS OF THIS SECTION" in text:
            content = {
                "type": "game",
                "title": "Hello ball",
                "skills": ["speaking"],
                "interaction": "pairs",
                "minutes": 5,
                "instructions": offending,
                "teacher_steps": ["Model it."],
                "student_steps": ["Listen.", offending],
            }
            return json.dumps({"blocks": [{"kind": "activity", "content": content}]})
        return "not json"

    cases = [case for case in _public_cases() if case.id == CASE_IDS[0]]
    report = run_cases(
        cases,
        settings=settings,
        provider=MockProvider(handler=handler),
        judge_provider=None,
        eval_dir=EVAL_DIR,
        workdir=tmp_path,
        timestamp=TIMESTAMP,
    )
    result = report.cases[0]
    assert result.status == "ok", result.reason
    assert len(repair_prompts) == 1
    lesson = _first_lesson(report)
    game = next(b for b in lesson.blocks.values() if b.kind == "activity")
    assert game.content.instructions == clean
    assert result.metrics is not None and result.metrics.tokens_in > 0
