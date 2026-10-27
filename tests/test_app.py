"""The local teacher app (``classdeck.app``): routes, jobs, proposals, uploads, static page.

Everything runs offline: the draft uses the template pipeline (``endpoint=offline``) and edits
use a scripted ``MockProvider`` injected through ``create_app(provider_factory=...)``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from classdeck.app import server
from classdeck.app.server import create_app
from classdeck.config import Settings
from classdeck.lesson import Lesson, LessonStore
from classdeck.llm import MockProvider
from classdeck.llm.base import Message

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "eval" / "fixtures" / "public"
SAMPLE = "gs3-unit1"
UNIT = "ta3.u1"


def make_settings(tmp_path: Path, **extra: Any) -> Settings:
    return Settings(
        workdir=tmp_path / "out",
        curriculum_dir=ROOT / "curriculum",
        provider="mock",
        cache_dir=None,
        **extra,
    )


def block_by_kind(lesson: Lesson, kind: str, *, min_items: int = 0) -> Any:
    for block in lesson.ordered_blocks():
        if block.kind == kind and len(getattr(block.content, "items", [])) >= min_items:
            return block
    raise AssertionError(f"no {kind} block with {min_items} items")


def drop_last_word_reply(store: LessonStore, lesson_id: str) -> Callable[[list[Message]], str]:
    """A scripted model: reply with the first vocabulary block minus its last item."""

    def handler(_: list[Message]) -> str:
        lesson = store.load(lesson_id)
        block = block_by_kind(lesson, "vocabulary", min_items=2).model_dump(mode="json")
        block["content"]["items"] = block["content"]["items"][:-1]
        return json.dumps(
            {
                "replacements": {block["id"]: block},
                "added_blocks": [],
                "removed_block_ids": [],
                "rationale": "Dropped the last word.",
            }
        )

    return handler


class Harness:
    """A client plus the pieces tests poke at."""

    def __init__(self, tmp_path: Path, **kwargs: Any) -> None:
        self.settings = make_settings(tmp_path)
        self.store = LessonStore(self.settings.workdir)
        self.provider: MockProvider | None = None
        self.client = TestClient(
            create_app(
                self.settings,
                provider_factory=lambda _settings, endpoint: (
                    None if endpoint == "offline" else self.provider
                ),
                sample_dir=SAMPLE_DIR,
                **kwargs,
            )
        )

    def create_lesson(self, *, pictures: bool = False, **form: Any) -> dict[str, Any]:
        data = {
            "sample": SAMPLE,
            "grade": 3,
            "unit": UNIT,
            "lesson": 1,
            "period": 1,
            "endpoint": "offline",
            "pictures": str(pictures).lower(),
            **form,
        }
        response = self.client.post("/api/jobs", data=data)
        assert response.status_code == 200, response.text
        return self.wait(response.json()["job_id"])

    def wait(self, job_id: str) -> dict[str, Any]:
        deadline = time.time() + 120
        while time.time() < deadline:
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] != "running":
                return job
            time.sleep(0.1)
        raise AssertionError("the job did not finish")


@pytest.fixture
def app_env(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def lesson_id(app_env: Harness) -> str:
    job = app_env.create_lesson()
    assert job["status"] == "done", job
    return job["lesson_id"]


# --- config ------------------------------------------------------------------------------------


def test_config_lists_endpoints_units_themes_and_samples(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, deepseek_api_key="sk-secret-value-1234")
    client = TestClient(create_app(settings, sample_dir=SAMPLE_DIR))
    response = client.get("/api/config")
    assert response.status_code == 200
    config = response.json()

    endpoints = {e["name"]: e for e in config["endpoints"]}
    assert endpoints["offline"]["configured"] is True
    assert endpoints["deepseek"]["configured"] is True
    assert endpoints["meta"]["configured"] is False
    assert endpoints["openrouter"]["configured"] is False
    assert "sk-secret-value-1234" not in response.text

    grades = {g["grade"]: g for g in config["grades"]}
    assert set(grades) == {3, 4, 5}
    assert grades[3]["units"][0] == {
        "id": UNIT,
        "title": grades[3]["units"][0]["title"],
        "lessons": 3,
    }
    assert grades[3]["units"][0]["title"]
    assert {t["id"] for t in config["themes"]} >= {"kids", "plain"}
    assert all(t["label"] for t in config["themes"])
    assert any(s["id"] == SAMPLE and s["unit_id"] == UNIT for s in config["samples"])
    assert config["default_endpoint"] == "offline"
    assert any(p["id"] == "reduce_words" and p["takes_number"] for p in config["presets"])
    assert config["limits"]["upload_mb"] == 30


def test_unconfigured_endpoint_is_refused(app_env: Harness) -> None:
    response = app_env.client.post(
        "/api/jobs",
        data={"sample": SAMPLE, "grade": 3, "unit": UNIT, "endpoint": "meta"},
    )
    assert response.status_code == 400
    assert "API key" in response.json()["error"]


# --- jobs, lessons, exports ---------------------------------------------------------------------


def test_job_runs_the_pipeline_and_saves_the_lesson(app_env: Harness) -> None:
    job = app_env.create_lesson(pictures=True)
    assert job["status"] == "done", job
    assert job["stages_done"] == ["ingest", "outline", "draft", "pictures", "export"]
    assert job["error"] is None

    listing = app_env.client.get("/api/lessons").json()["lessons"]
    assert [row["id"] for row in listing] == [job["lesson_id"]]
    assert listing[0]["grade"] == 3 and listing[0]["unit_id"] == UNIT

    exports = app_env.settings.workdir / "exports" / job["lesson_id"]
    assert {p.name for p in exports.iterdir()} == {"lesson.html", "lesson.pptx", "lesson.docx"}


def test_job_without_pictures_skips_that_stage(app_env: Harness) -> None:
    job = app_env.create_lesson(pictures=False)
    assert job["stages_skipped"] == ["pictures"]


def test_unknown_unit_and_bad_form_values_are_400(app_env: Harness) -> None:
    base = {"sample": SAMPLE, "grade": 3, "unit": UNIT, "endpoint": "offline"}
    for change in ({"unit": "ta3.u99"}, {"grade": 7}, {"lesson": 9}, {"period": 3}, {"theme": "x"}):
        response = app_env.client.post("/api/jobs", data={**base, **change})
        assert response.status_code == 400, change
        assert "error" in response.json()
    assert app_env.client.post("/api/jobs", data={**base, "sample": "nope"}).status_code == 400
    assert app_env.client.get("/api/jobs/missing").status_code == 404


def test_lesson_endpoint_returns_blocks_for_the_panes(app_env: Harness, lesson_id: str) -> None:
    body = app_env.client.get(f"/api/lessons/{lesson_id}").json()
    lesson, summary = body["lesson"], body["summary"]
    assert lesson["id"] == lesson_id
    rows = summary["blocks"]
    assert rows and {row["id"] for row in rows} == set(lesson["blocks"])
    first = rows[0]
    assert {"kind", "phase", "label", "provenance", "status", "citations"} <= set(first)
    assert summary["can_undo"] is False and summary["can_redo"] is False
    assert any(row["citations"] for row in rows), "some block quotes the source with a page number"
    cited = next(c for row in rows for c in row["citations"])
    assert cited["page"] >= 1 and cited["quote"]
    assert all(r["phase"] in {s["phase"] for s in summary["sections"]} for r in rows)


def test_deck_endpoint_renders_html_with_block_ids(app_env: Harness, lesson_id: str) -> None:
    response = app_env.client.get(f"/api/lessons/{lesson_id}/deck", params={"theme": "plain"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "data-block-id" in response.text
    assert app_env.client.get(f"/api/lessons/{lesson_id}/deck?theme=nope").status_code == 400


@pytest.mark.parametrize(
    ("fmt", "content_type"),
    [
        ("html", "text/html"),
        ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ],
)
def test_export_endpoints_return_the_right_content_type(
    app_env: Harness, lesson_id: str, fmt: str, content_type: str
) -> None:
    response = app_env.client.get(f"/api/lessons/{lesson_id}/export/{fmt}")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(content_type)
    assert f"{lesson_id}.{fmt}" in response.headers["content-disposition"]
    assert len(response.content) > 1000
    if fmt in ("pptx", "docx"):
        assert response.content[:2] == b"PK"


def test_unknown_lesson_and_format_are_json_404(app_env: Harness, lesson_id: str) -> None:
    for url in (
        "/api/lessons/l_nope",
        "/api/lessons/..%2Fx",
        f"/api/lessons/{lesson_id}/export/zip",
    ):
        response = app_env.client.get(url)
        assert response.status_code == 404
        assert "error" in response.json()


def test_check_and_illustrate(app_env: Harness, lesson_id: str) -> None:
    checked = app_env.client.post(f"/api/lessons/{lesson_id}/check", json={})
    assert checked.status_code == 200
    assert checked.json()["lesson"]["id"] == lesson_id

    plan = app_env.client.post(
        f"/api/lessons/{lesson_id}/illustrate",
        json={"max": 2, "dry_run": True, "endpoint": "offline"},
    ).json()
    assert plan["report"]["planned"] == 2
    assert not (app_env.settings.workdir / "images" / "generated").exists()

    done = app_env.client.post(
        f"/api/lessons/{lesson_id}/illustrate", json={"max": 2, "endpoint": "offline"}
    ).json()
    assert done["report"]["generated"] == 2
    pictures = [
        item
        for block in done["lesson"]["blocks"].values()
        if block["kind"] == "vocabulary"
        for item in block["content"]["items"]
        if item.get("generated_picture")
    ]
    assert len(pictures) >= 2


# --- proposals, accept, undo, redo --------------------------------------------------------------


def propose(env: Harness, lesson_id: str, **extra: Any) -> Any:
    lesson = env.store.load(lesson_id)
    block = block_by_kind(lesson, "vocabulary", min_items=2)
    env.provider = MockProvider(handler=drop_last_word_reply(env.store, lesson_id))
    return env.client.post(
        f"/api/lessons/{lesson_id}/proposals",
        json={"block_ids": [block.id], "instruction": "Drop the last word", **extra},
    )


def test_proposal_accept_undo_redo(app_env: Harness, lesson_id: str) -> None:
    before = app_env.store.load(lesson_id)
    block = block_by_kind(before, "vocabulary", min_items=2)
    words = [item.word for item in block.content.items]

    response = propose(app_env, lesson_id)
    assert response.status_code == 200, response.text
    proposal = response.json()
    change = proposal["changes"][0]
    assert change["op"] == "replace" and change["block_id"] == block.id
    assert [i["word"] for i in change["before"]["content"]["items"]] == words
    assert [i["word"] for i in change["after"]["content"]["items"]] == words[:-1]
    assert proposal["rationale"] == "Dropped the last word."
    assert isinstance(proposal["affected"], list) and isinstance(proposal["checks_preview"], list)
    # Nothing is saved until the teacher accepts.
    assert app_env.store.load(lesson_id).model_dump() == before.model_dump()

    accepted = app_env.client.post(f"/api/lessons/{lesson_id}/proposals/{proposal['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["summary"]["can_undo"] is True and body["summary"]["can_redo"] is False
    new_words = [i["word"] for i in body["lesson"]["blocks"][block.id]["content"]["items"]]
    assert new_words == words[:-1]
    assert len(body["lesson"]["revisions"]) == 1
    assert [i.word for i in app_env.store.load(lesson_id).blocks[block.id].content.items] == words[
        :-1
    ]
    # The proposal is spent.
    again = app_env.client.post(f"/api/lessons/{lesson_id}/proposals/{proposal['id']}/accept")
    assert again.status_code == 404

    undone = app_env.client.post(f"/api/lessons/{lesson_id}/undo").json()
    assert undone["summary"]["can_undo"] is False and undone["summary"]["can_redo"] is True
    assert [i["word"] for i in undone["lesson"]["blocks"][block.id]["content"]["items"]] == words

    redone = app_env.client.post(f"/api/lessons/{lesson_id}/redo").json()
    assert redone["summary"]["can_undo"] is True and redone["summary"]["can_redo"] is False
    assert [i["word"] for i in redone["lesson"]["blocks"][block.id]["content"]["items"]] == words[
        :-1
    ]

    assert app_env.client.post(f"/api/lessons/{lesson_id}/redo").status_code == 409


def test_undo_with_no_edits_is_409(app_env: Harness, lesson_id: str) -> None:
    response = app_env.client.post(f"/api/lessons/{lesson_id}/undo")
    assert response.status_code == 409
    assert "error" in response.json()


def test_discarded_proposal_cannot_be_accepted(app_env: Harness, lesson_id: str) -> None:
    proposal = propose(app_env, lesson_id).json()
    url = f"/api/lessons/{lesson_id}/proposals/{proposal['id']}"
    assert app_env.client.delete(url).json() == {"ok": True}
    assert app_env.client.post(url + "/accept").status_code == 404


def test_proposal_validation(app_env: Harness, lesson_id: str) -> None:
    url = f"/api/lessons/{lesson_id}/proposals"
    block_id = next(iter(app_env.store.load(lesson_id).blocks))
    # No model behind the default endpoint.
    offline = app_env.client.post(
        url, json={"block_ids": [block_id], "instruction": "x", "endpoint": "offline"}
    )
    assert offline.status_code == 400 and "model" in offline.json()["error"]
    app_env.provider = MockProvider()
    assert (
        app_env.client.post(url, json={"block_ids": ["b_nope"], "instruction": "x"}).status_code
        == 400
    )
    assert (
        app_env.client.post(url, json={"block_ids": [block_id], "instruction": "  "}).status_code
        == 400
    )
    assert app_env.client.post(url, json={"instruction": "x"}).status_code == 422


def test_model_failure_is_502_and_saves_nothing(app_env: Harness, lesson_id: str) -> None:
    before = app_env.store.load(lesson_id).model_dump()
    block = block_by_kind(app_env.store.load(lesson_id), "vocabulary")
    app_env.provider = MockProvider(responses=[])  # raises LLMError on the first call
    response = app_env.client.post(
        f"/api/lessons/{lesson_id}/proposals",
        json={"block_ids": [block.id], "instruction": "Make it shorter"},
    )
    assert response.status_code == 502
    assert "error" in response.json()
    assert app_env.store.load(lesson_id).model_dump() == before


def test_unusable_model_reply_is_422(app_env: Harness, lesson_id: str) -> None:
    block = block_by_kind(app_env.store.load(lesson_id), "vocabulary")
    empty = json.dumps(
        {"replacements": {}, "added_blocks": [], "removed_block_ids": [], "rationale": "no"}
    )
    app_env.provider = MockProvider(handler=lambda _: empty)
    response = app_env.client.post(
        f"/api/lessons/{lesson_id}/proposals",
        json={"block_ids": [block.id], "instruction": "Do something"},
    )
    assert response.status_code in (422, 502), response.text
    assert "error" in response.json()


# --- uploads ------------------------------------------------------------------------------------


def upload(env: Harness, name: str, content: bytes) -> Any:
    return env.client.post(
        "/api/jobs",
        data={"grade": 3, "unit": UNIT, "endpoint": "offline"},
        files={"file": (name, content, "application/octet-stream")},
    )


def test_upload_rejects_other_formats(app_env: Harness) -> None:
    response = upload(app_env, "notes.txt", b"hello")
    assert response.status_code == 400
    assert "PDF" in response.json()["error"]
    assert not (app_env.settings.workdir / "uploads").exists() or not list(
        (app_env.settings.workdir / "uploads").iterdir()
    )


def test_upload_rejects_a_file_that_is_not_what_its_name_says(app_env: Harness) -> None:
    assert upload(app_env, "fake.pdf", b"not a pdf").status_code == 400
    assert upload(app_env, "empty.pdf", b"").status_code == 400


def test_upload_rejects_oversize_files(app_env: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 2048)
    response = upload(app_env, "big.pdf", b"%PDF-1.4\n" + b"0" * 5000)
    assert response.status_code == 413
    assert "30 MB" in response.json()["error"]
    uploads = app_env.settings.workdir / "uploads"
    assert not uploads.exists() or not list(uploads.iterdir())


def test_upload_is_saved_under_a_generated_name_and_ingested(app_env: Harness) -> None:
    content = (SAMPLE_DIR / f"{SAMPLE}.pdf").read_bytes()
    response = upload(app_env, "../../evil name.PDF", content)
    assert response.status_code == 200, response.text
    job = app_env.wait(response.json()["job_id"])
    assert job["status"] == "done", job
    saved = list((app_env.settings.workdir / "uploads").iterdir())
    assert len(saved) == 1
    assert saved[0].suffix == ".pdf" and "evil" not in saved[0].name


# --- the page -----------------------------------------------------------------------------------


def test_static_page_is_served(app_env: Harness) -> None:
    index = app_env.client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "ClassDeck" in index.text and "/static/app.js" in index.text
    for name, kind in (("app.js", "javascript"), ("app.css", "css"), ("i18n.js", "javascript")):
        response = app_env.client.get(f"/static/{name}")
        assert response.status_code == 200 and kind in response.headers["content-type"]
    assert app_env.client.get("/static/../server.py").status_code in (400, 404)


def test_only_loopback_hosts_are_served(app_env: Harness) -> None:
    assert app_env.client.get("/api/config", headers={"host": "evil.example"}).status_code == 403
    assert app_env.client.get("/api/config", headers={"host": "127.0.0.1:8765"}).status_code == 200
    assert app_env.client.get("/api/config", headers={"host": "localhost:9"}).status_code == 200


def test_ui_strings_cover_the_keys_the_page_uses() -> None:
    """Every literal ``t('key')`` in app.js exists in both languages."""
    import re

    static = Path(server.__file__).parent / "static"
    script = (static / "app.js").read_text(encoding="utf-8")
    strings = (static / "i18n.js").read_text(encoding="utf-8")
    keys = set(re.findall(r"\bt\('([a-z0-9_.]+)'", script)) - {
        "stage.",
        "prov.",
        "status.",
        "dl.",
        "prop.op.",
    }
    vi, en = strings.split("\n  en: {")
    for key in sorted(keys):
        needle = f"'{key}':" if "." in key else f"{key}:"
        assert needle in vi, f"vi is missing {key}"
        assert needle in en, f"en is missing {key}"


def test_serve_refuses_non_loopback_hosts() -> None:
    with pytest.raises(ValueError, match="loopback"):
        server.serve(host="0.0.0.0", open_browser=False)
