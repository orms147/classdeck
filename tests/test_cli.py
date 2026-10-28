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
    assert not (export_dir / "lesson.pptx").exists()  # HTML is the only default output
    for name in ("lesson.html",):
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


def test_console_is_utf8_on_import() -> None:
    import sys

    from classdeck import cli as cli_module

    cli_module._utf8_console()  # idempotent; must never raise
    assert (sys.stdout.encoding or "").lower().replace("-", "") == "utf8"


def test_run_does_the_whole_pipeline_offline(offline_workdir: Path) -> None:
    args = ["run", str(FIXTURE), "--grade", "3", "--unit", "ta3.u1", "--no-open"]
    result = runner.invoke(app, [*args, "--max-pictures", "1"])
    assert result.exit_code == 0, result.output
    exports = list((offline_workdir / "exports").glob("*/lesson.html"))
    assert len(exports) == 1
    html = exports[0].read_text(encoding="utf-8")
    assert "data-block-id" in html
    assert not (exports[0].parent / "lesson.pptx").exists()
    assert not (exports[0].parent / "lesson.docx").exists()
    assert "[5/5] export" in result.output


def test_run_takes_a_unit_number_and_works_for_grades_without_curriculum_data(
    offline_workdir: Path,
) -> None:
    result = runner.invoke(
        app, ["run", str(FIXTURE), "--grade", "1", "--unit", "1", "--no-open", "--no-pictures"]
    )
    assert result.exit_code == 0, result.output
    assert "grade 1, unit 1" in result.output
    lesson_id = next((offline_workdir / "exports").glob("*/lesson.html")).parent.name
    # commands that work on a saved lesson rebuild its unit from the saved pages
    checked = runner.invoke(app, ["check", lesson_id])
    assert checked.exit_code == 0, checked.output


def test_run_refuses_a_unit_that_is_not_a_number(offline_workdir: Path) -> None:
    for unit in ("one", "0", "ta4.u1"):
        result = runner.invoke(
            app, ["run", str(FIXTURE), "--grade", "3", "--unit", unit, "--no-open"]
        )
        assert result.exit_code == 2, unit
        assert "unit" in result.output.lower()


# --- secondary units (grades 6-9) ---------------------------------------------------------------


def test_run_for_grade_9_takes_a_lesson_up_to_7_and_no_period(offline_workdir: Path) -> None:
    result = runner.invoke(
        app,
        ["run", str(FIXTURE), "--grade", "9", "--unit", "ta9.u1", "--lesson", "1", "--period", "2"],
    )
    assert result.exit_code == 2
    assert "--period does not apply to grades 6-9" in result.output
    result = runner.invoke(
        app, ["run", str(FIXTURE), "--grade", "9", "--unit", "ta9.u1", "--lesson", "8", "--no-open"]
    )
    assert result.exit_code == 2  # typer's own range check


def test_run_for_a_primary_grade_keeps_lessons_1_to_3_and_rejects_a_reference(
    offline_workdir: Path,
) -> None:
    result = runner.invoke(
        app, ["run", str(FIXTURE), "--grade", "3", "--unit", "ta3.u1", "--lesson", "5"]
    )
    assert result.exit_code == 2 and "--lesson must be 1-3" in result.output
    result = runner.invoke(
        app,
        ["run", str(FIXTURE), "--grade", "3", "--unit", "ta3.u1", "--reference", str(FIXTURE)],
    )
    assert result.exit_code == 2 and "--reference is for grades 6-9" in result.output


def test_run_for_grade_9_without_the_section_banner_says_which_one_is_missing(
    offline_workdir: Path,
) -> None:
    """A text PDF that is not a Global Success 9 unit has no GETTING STARTED banner."""
    result = runner.invoke(
        app,
        ["run", str(FIXTURE), "--grade", "9", "--unit", "ta9.u1", "--lesson", "1", "--no-open"],
    )
    assert result.exit_code == 1
    assert "GETTING STARTED banner" in result.output


# --- import-edits (docs/output-formats.md, "Editing on the slide") -------------------------------


def _saved_deck_with(html: str, lesson_id: str, edits: list[dict[str, str]]) -> str:
    import json

    payload = json.dumps({"lesson_id": lesson_id, "edits": edits}, ensure_ascii=False)
    script = f'<script type="application/json" id="classdeck-edits">{payload}</script>'
    return html.replace("</body>", script + "</body>")


def test_import_edits_applies_a_saved_deck_as_one_undoable_revision(
    offline_workdir: Path, tmp_path: Path
) -> None:
    import json

    result = runner.invoke(
        app, ["run", str(FIXTURE), "--grade", "3", "--unit", "ta3.u1", "--no-open"]
    )
    assert result.exit_code == 0, result.output
    deck = next((offline_workdir / "exports").glob("*/lesson.html"))
    lesson_id = deck.parent.name
    lesson_path = offline_workdir / "lessons" / f"{lesson_id}.lesson.json"
    before = json.loads(lesson_path.read_text(encoding="utf-8"))
    vocabulary = next(b for b in before["blocks"].values() if b["kind"] == "vocabulary")
    activity = next(b for b in before["blocks"].values() if b["kind"] == "activity")
    word_path = "content/items/0/meaning_vi"
    original = vocabulary["content"]["items"][0]["meaning_vi"]
    html = deck.read_text(encoding="utf-8")
    assert f'data-field-block="{vocabulary["id"]}" data-field="{word_path}"' in html

    edited = tmp_path / "edited.html"
    edited.write_text(
        _saved_deck_with(
            html,
            lesson_id,
            [
                {
                    "block_id": vocabulary["id"],
                    "field": word_path,
                    "value": "da sua",
                    "original": original,
                },
                {"block_id": activity["id"], "field": "content/title", "value": "New title"},
            ],
        ),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["import-edits", lesson_id, str(edited)])
    assert result.exit_code == 0, result.output
    assert "imported 2 edit(s) into 2 block(s)" in result.output
    after = json.loads(lesson_path.read_text(encoding="utf-8"))
    assert after["blocks"][vocabulary["id"]]["content"]["items"][0]["meaning_vi"] == "da sua"
    assert after["blocks"][activity["id"]]["content"]["title"] == "New title"
    assert len(after["revisions"]) == len(before["revisions"]) + 1
    assert after["revisions"][-1]["instruction"] == "Edited on the slide"

    again = runner.invoke(app, ["import-edits", lesson_id, str(edited)])
    assert again.exit_code == 0 and "nothing to import" in again.output

    undone = runner.invoke(app, ["undo", lesson_id])
    assert undone.exit_code == 0, undone.output
    restored = json.loads(lesson_path.read_text(encoding="utf-8"))
    assert restored["blocks"][vocabulary["id"]] == before["blocks"][vocabulary["id"]]
    assert restored["blocks"][activity["id"]] == before["blocks"][activity["id"]]


def test_import_edits_refuses_a_deck_of_another_lesson_a_page_without_edits_and_bad_fields(
    offline_workdir: Path, tmp_path: Path
) -> None:
    result = runner.invoke(
        app, ["run", str(FIXTURE), "--grade", "3", "--unit", "ta3.u1", "--no-open"]
    )
    assert result.exit_code == 0, result.output
    deck = next((offline_workdir / "exports").glob("*/lesson.html"))
    lesson_id = deck.parent.name
    html = deck.read_text(encoding="utf-8")
    block_id = html.split('data-field-block="')[1].split('"')[0]
    path = html.split('data-field="')[1].split('"')[0]

    foreign = tmp_path / "foreign.html"
    foreign.write_text(
        _saved_deck_with(html, "l_other", [{"block_id": block_id, "field": path, "value": "x"}]),
        encoding="utf-8",
    )
    refused = runner.invoke(app, ["import-edits", lesson_id, str(foreign)])
    assert refused.exit_code == 1 and "exported from lesson l_other" in refused.output

    plain = tmp_path / "plain.html"
    plain.write_text(html, encoding="utf-8")
    missing = runner.invoke(app, ["import-edits", lesson_id, str(plain)])
    assert missing.exit_code == 1 and "no embedded edits" in missing.output

    wrong = tmp_path / "wrong.html"
    wrong.write_text(
        _saved_deck_with(html, lesson_id, [{"block_id": block_id, "field": "kind", "value": "x"}]),
        encoding="utf-8",
    )
    bad = runner.invoke(app, ["import-edits", lesson_id, str(wrong)])
    assert bad.exit_code == 1 and "not editable" in bad.output
