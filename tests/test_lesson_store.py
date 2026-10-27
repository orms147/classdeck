from __future__ import annotations

import json
from pathlib import Path

import pytest

from classdeck.lesson import (
    Citation,
    Lesson,
    LessonNotFound,
    LessonStore,
    SourceNotFound,
    SourceStore,
    find_dangling_citations,
)
from tests.test_lesson_schema import DOC_ID, IPA_HELLO, make_lesson, make_source_document

# --- LessonStore -------------------------------------------------------------------------------


def test_path_for_uses_lessons_subdirectory(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    assert store.path_for("l_3b9e") == tmp_path / "lessons" / "l_3b9e.lesson.json"


def test_save_then_load_round_trips(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    lesson = make_lesson()
    path = store.save(lesson)
    assert path == store.path_for(lesson.id)
    assert path.is_file()
    assert store.load(lesson.id) == lesson


def test_save_advances_updated_at_and_keeps_created_at(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    lesson = make_lesson()
    created, before = lesson.created_at, lesson.updated_at
    store.save(lesson)
    assert lesson.updated_at > before
    first = store.load(lesson.id)
    assert first.updated_at == lesson.updated_at
    assert first.created_at == created
    store.save(lesson)
    assert store.load(lesson.id).updated_at > first.updated_at


def test_file_is_readable_utf8_json(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    path = store.save(make_lesson())
    text = path.read_text(encoding="utf-8")
    assert IPA_HELLO in text
    assert "\\u" not in text
    assert text.startswith('{\n  "id": "l_3b9e"')
    assert json.loads(text)["blocks"]["b_07"]["depends_on"] == ["b_03"]


def test_save_leaves_no_temp_file_and_overwrites(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    lesson = make_lesson()
    store.save(lesson)
    lesson.title = "Renamed"
    store.save(lesson)
    assert sorted(p.name for p in (tmp_path / "lessons").iterdir()) == ["l_3b9e.lesson.json"]
    assert store.load(lesson.id).title == "Renamed"


def test_failed_write_keeps_the_old_file_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LessonStore(tmp_path)
    lesson = make_lesson()
    store.save(lesson)
    original = store.path_for(lesson.id).read_text(encoding="utf-8")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("classdeck.lesson.store.os.replace", boom)
    lesson.title = "Never saved"
    with pytest.raises(OSError, match="disk full"):
        store.save(lesson)
    assert store.path_for(lesson.id).read_text(encoding="utf-8") == original
    assert [p.name for p in (tmp_path / "lessons").iterdir()] == ["l_3b9e.lesson.json"]


def test_list_ids_is_sorted_and_empty_without_directory(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    assert store.list_ids() == []
    for lesson_id in ("l_b", "l_a", "l_c"):
        lesson = make_lesson()
        lesson.id = lesson_id
        store.save(lesson)
    assert store.list_ids() == ["l_a", "l_b", "l_c"]


def test_load_missing_lesson_raises_lesson_not_found(tmp_path: Path) -> None:
    store = LessonStore(tmp_path)
    with pytest.raises(LessonNotFound):
        store.load("l_nope")
    with pytest.raises(FileNotFoundError):
        store.load("l_nope")


@pytest.mark.parametrize("bad", ["", "../escape", "a/b", "a\\b", ".hidden", "x.lesson"])
def test_ids_that_could_escape_the_directory_are_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="invalid id"):
        LessonStore(tmp_path).path_for(bad)
    with pytest.raises(ValueError, match="invalid id"):
        SourceStore(tmp_path).path_for(bad)


# --- SourceStore -------------------------------------------------------------------------------


def test_source_store_round_trip(tmp_path: Path) -> None:
    store = SourceStore(tmp_path)
    document = make_source_document()
    path = store.save(document)
    assert path == tmp_path / "sources" / f"{DOC_ID}.json"
    assert store.load(DOC_ID) == document
    assert store.list_ids() == [DOC_ID]
    assert sorted(p.name for p in (tmp_path / "sources").iterdir()) == [f"{DOC_ID}.json"]


def test_source_store_missing_and_empty(tmp_path: Path) -> None:
    store = SourceStore(tmp_path)
    assert store.list_ids() == []
    with pytest.raises(SourceNotFound):
        store.load("d_nope")


def test_stores_share_a_root_without_mixing(tmp_path: Path) -> None:
    LessonStore(tmp_path).save(make_lesson())
    SourceStore(tmp_path).save(make_source_document())
    assert LessonStore(tmp_path).list_ids() == ["l_3b9e"]
    assert SourceStore(tmp_path).list_ids() == [DOC_ID]


def test_find_span() -> None:
    document = make_source_document()
    span = document.find_span(6, "p6.s4")
    assert span is not None
    assert span.image_path == f"sources/{DOC_ID}/p6_img1.png"
    assert document.find_span(7, "p6.s4") is None
    assert document.find_span(6, "p6.s99") is None


# --- find_dangling_citations -------------------------------------------------------------------


def test_consistent_lesson_has_no_dangling_citations() -> None:
    lesson = make_lesson()
    sources = {DOC_ID: make_source_document()}
    assert find_dangling_citations(lesson, sources) == []


def test_missing_span_is_reported() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources[0] = Citation(doc_id=DOC_ID, page=6, span_id="p6.s99")
    problems = find_dangling_citations(lesson, {DOC_ID: make_source_document()})
    assert len(problems) == 1
    block_id, citation, reason = problems[0]
    assert block_id == "b_03"
    assert citation.span_id == "p6.s99"
    assert "p6.s99" in reason


def test_span_on_the_wrong_page_is_reported() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources[0] = Citation(doc_id=DOC_ID, page=7, span_id="p6.s6")
    problems = find_dangling_citations(lesson, {DOC_ID: make_source_document()})
    assert [(block_id, citation.page) for block_id, citation, _ in problems] == [("b_03", 7)]


def test_vocabulary_item_image_is_checked() -> None:
    lesson = make_lesson()
    block = lesson.blocks["b_02"]
    block.content.items[0].image = Citation(doc_id=DOC_ID, page=6, span_id="p6.s77")
    problems = find_dangling_citations(lesson, {DOC_ID: make_source_document()})
    assert [(block_id, citation.span_id) for block_id, citation, _ in problems] == [
        ("b_02", "p6.s77")
    ]


def test_unknown_document_is_reported() -> None:
    lesson = make_lesson()
    lesson.blocks["b_03"].sources.append(Citation(doc_id="d_other", page=1, span_id="p1.s1"))
    problems = find_dangling_citations(lesson, {DOC_ID: make_source_document()})
    assert len(problems) == 1
    block_id, citation, reason = problems[0]
    assert (block_id, citation.doc_id) == ("b_03", "d_other")
    assert "not listed" in reason


def test_listed_document_that_is_not_loaded_is_reported() -> None:
    lesson = make_lesson()
    problems = find_dangling_citations(lesson, {})
    assert {block_id for block_id, _, _ in problems} == {"b_02", "b_03"}
    assert all("not available" in reason for _, _, reason in problems)
    assert len([p for p in problems if p[0] == "b_02"]) == 2  # the source and the item image


def test_lesson_loaded_from_disk_checks_the_same(tmp_path: Path) -> None:
    LessonStore(tmp_path).save(make_lesson())
    SourceStore(tmp_path).save(make_source_document())
    lesson: Lesson = LessonStore(tmp_path).load("l_3b9e")
    sources = {
        doc_id: SourceStore(tmp_path).load(doc_id) for doc_id in SourceStore(tmp_path).list_ids()
    }
    assert find_dangling_citations(lesson, sources) == []
