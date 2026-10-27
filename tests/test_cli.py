from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from classdeck import __version__
from classdeck import config as config_module
from classdeck.cli import app

runner = CliRunner()
REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "eval" / "fixtures" / "public" / "mini-unit-hello.pdf"


@pytest.fixture
def offline_workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Mock provider, no cache, a fresh workdir; settings cache cleared around the test."""
    workdir = tmp_path / "out"
    monkeypatch.setenv("CLASSDECK_PROVIDER", "mock")
    monkeypatch.setenv("CLASSDECK_CACHE_DIR", "")
    monkeypatch.setenv("CLASSDECK_WORKDIR", str(workdir))
    monkeypatch.setenv("CLASSDECK_CURRICULUM_DIR", str(REPO / "curriculum"))
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    config_module.get_settings.cache_clear()
    yield workdir
    config_module.get_settings.cache_clear()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_config_lists_settings_and_masks_key() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "CLASSDECK_MODEL" in result.output
    assert "MODEL_API_KEY" in result.output
    # Whatever the environment holds, the raw key never reaches the terminal.
    assert "sk-" not in result.output


def test_ingest_requires_existing_file() -> None:
    result = runner.invoke(app, ["ingest", "does-not-exist.pdf"])
    assert result.exit_code != 0


def test_ping_with_mock_provider(offline_workdir: Path) -> None:
    result = runner.invoke(app, ["ping"])
    assert result.exit_code == 0, result.output
    assert "mock" in result.output


def test_ping_without_key_fails_cleanly(offline_workdir: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLASSDECK_PROVIDER", "api")
    config_module.get_settings.cache_clear()
    result = runner.invoke(app, ["ping"])
    assert result.exit_code == 1
    assert "ping failed" in result.output


def test_pipeline_end_to_end_offline(offline_workdir: Path) -> None:
    """ingest -> plan -> draft -> check -> export -> handoff, all without a model."""
    result = runner.invoke(app, ["ingest", str(FIXTURE)])
    assert result.exit_code == 0, result.output
    doc_match = re.search(r"\bd_[0-9a-f]{8}\b", result.output)
    assert doc_match, result.output
    doc_id = doc_match.group(0)
    assert (offline_workdir / "sources" / f"{doc_id}.json").is_file()
    assert any((offline_workdir / "images" / doc_id).glob("*"))

    result = runner.invoke(app, ["plan", doc_id, "--grade", "3", "--unit", "ta3.u1"])
    assert result.exit_code == 0, result.output
    outlines = list((offline_workdir / "outlines").glob("*.outline.json"))
    assert len(outlines) == 1
    assert "presentation" in result.output

    result = runner.invoke(app, ["draft", str(outlines[0])])
    assert result.exit_code == 0, result.output
    lesson_match = re.search(r"\bl_[0-9a-f]{6}\b", result.output)
    assert lesson_match, result.output
    lesson_id = lesson_match.group(0)
    lesson_path = offline_workdir / "lessons" / f"{lesson_id}.lesson.json"
    assert lesson_path.is_file()
    assert "vocabulary" in result.output and "dialogue" in result.output

    result = runner.invoke(app, ["check", lesson_id])
    assert result.exit_code == 0, result.output

    result = runner.invoke(app, ["export", lesson_id])
    assert result.exit_code == 0, result.output
    export_dir = offline_workdir / "exports" / lesson_id
    for name in ("lesson.html", "lesson.pptx", "lesson.docx"):
        assert (export_dir / name).stat().st_size > 1000, name
    html = (export_dir / "lesson.html").read_text(encoding="utf-8")
    assert "data-block-id" in html and "http" not in html.lower()

    result = runner.invoke(app, ["handoff", "deck", lesson_id])
    assert result.exit_code == 0, result.output
    pack = offline_workdir / "handoff" / f"{lesson_id}.deck-pack.md"
    assert pack.is_file() and "data-block-id" in pack.read_text(encoding="utf-8")

    result = runner.invoke(
        app, ["handoff", "check-deck", lesson_id, str(export_dir / "lesson.html")]
    )
    assert result.exit_code == 0, result.output
    assert (export_dir / "lesson.checked.html").is_file()

    # Editing needs a model; the offline pipeline says so instead of pretending.
    result = runner.invoke(app, ["edit", lesson_id, "Make it shorter", "-b", "b_missing"])
    assert result.exit_code == 1
    assert "needs a model" in result.output

    result = runner.invoke(app, ["undo", lesson_id])
    assert result.exit_code == 1


def test_export_by_path_and_theme(offline_workdir: Path) -> None:
    runner.invoke(app, ["ingest", str(FIXTURE)])
    doc_id = next((offline_workdir / "sources").glob("*.json")).stem
    runner.invoke(app, ["plan", doc_id, "--grade", "3", "--unit", "ta3.u1", "--period", "2"])
    outline = next((offline_workdir / "outlines").glob("*p2.outline.json"))
    draft = runner.invoke(app, ["draft", str(outline)])
    assert draft.exit_code == 0, draft.output
    lesson_path = next((offline_workdir / "lessons").glob("*.lesson.json"))
    out_dir = offline_workdir / "custom"
    result = runner.invoke(
        app,
        [
            "export",
            str(lesson_path),
            "--no-pptx",
            "--no-docx",
            "--theme",
            "plain",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert (out_dir / "lesson.html").is_file()
    assert not (out_dir / "lesson.pptx").exists()


def test_draft_passes_the_repair_switch_to_the_pipeline(
    offline_workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``draft`` repairs by default and ``--no-repair`` turns the round off."""
    import classdeck.pipeline as pipeline

    result = runner.invoke(app, ["ingest", str(FIXTURE)])
    doc_id = re.search(r"\bd_[0-9a-f]{8}\b", result.output).group(0)  # type: ignore[union-attr]
    result = runner.invoke(app, ["plan", doc_id, "--grade", "3", "--unit", "ta3.u1"])
    outline = next((offline_workdir / "outlines").glob("*.outline.json"))

    seen: list[bool] = []
    real = pipeline.build_lesson

    def spy(*args: object, **kwargs: object):
        seen.append(bool(kwargs["repair"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(pipeline, "build_lesson", spy)
    assert runner.invoke(app, ["draft", str(outline)]).exit_code == 0
    assert runner.invoke(app, ["draft", str(outline), "--no-repair"]).exit_code == 0
    assert seen == [True, False]


def test_ping_with_named_endpoint_but_no_key_fails_cleanly(offline_workdir: Path) -> None:
    result = runner.invoke(app, ["ping", "--endpoint", "meta"])
    assert result.exit_code == 1
    assert "ping failed" in result.output


def test_unknown_endpoint_is_reported(offline_workdir: Path) -> None:
    result = runner.invoke(app, ["ping", "--endpoint", "nope"])
    assert result.exit_code == 2
    assert "unknown endpoint" in result.output


def test_illustrate_then_export_offline(offline_workdir: Path) -> None:
    runner.invoke(app, ["ingest", str(FIXTURE)])
    doc_id = next((offline_workdir / "sources").glob("*.json")).stem
    runner.invoke(app, ["plan", doc_id, "--grade", "3", "--unit", "ta3.u1"])
    outline = next((offline_workdir / "outlines").glob("*.outline.json"))
    draft = runner.invoke(app, ["draft", str(outline)])
    assert draft.exit_code == 0, draft.output
    lesson_id = re.search(r"\bl_[0-9a-f]{6}\b", draft.output).group(0)
    lesson_path = offline_workdir / "lessons" / f"{lesson_id}.lesson.json"
    before = lesson_path.read_text(encoding="utf-8")

    dry = runner.invoke(app, ["illustrate", lesson_id, "--provider", "mock", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "new picture(s)" in dry.output
    assert lesson_path.read_text(encoding="utf-8") == before
    assert not (offline_workdir / "images" / "generated").exists()

    result = runner.invoke(app, ["illustrate", lesson_id, "--provider", "mock", "--max", "1"])
    assert result.exit_code == 0, result.output
    assert "generated 1" in result.output
    assert len(list((offline_workdir / "images" / "generated").glob("*.png"))) == 1
    assert lesson_path.read_text(encoding="utf-8").count('"generated_picture": "generated/') == 1

    again = runner.invoke(app, ["illustrate", lesson_id, "--provider", "mock", "--max", "2"])
    assert again.exit_code == 0, again.output
    assert "generated 1" in again.output  # the picture drawn first is not drawn again
    assert lesson_path.read_text(encoding="utf-8").count('"generated_picture": "generated/') == 2

    result = runner.invoke(app, ["export", lesson_id])
    assert result.exit_code == 0, result.output
    html = (offline_workdir / "exports" / lesson_id / "lesson.html").read_text(encoding="utf-8")
    assert html.count('data-generated="true"') >= 2
    assert "AI picture" in html and "http" not in html.lower()
