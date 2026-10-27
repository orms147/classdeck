from __future__ import annotations

import re
from pathlib import Path

import pytest

from classdeck.export import ExportBlocked, export_html
from classdeck.export.gallery import build_gallery
from classdeck.export.html import available_themes, theme_ids
from classdeck.lesson import Lesson, SourceDocument
from tests.test_export import FAILURE, make_export_lesson, write_png
from tests.test_export_html import Deck, load
from tests.test_lesson_schema import DOC_ID, make_source_document


@pytest.fixture
def lesson() -> Lesson:
    return make_export_lesson()


@pytest.fixture
def sources() -> dict[str, SourceDocument]:
    return {DOC_ID: make_source_document()}


@pytest.fixture
def image_root(tmp_path: Path) -> Path:
    root = tmp_path / "workdir"
    write_png(root)
    return root


@pytest.fixture
def gallery(
    tmp_path: Path, lesson: Lesson, sources: dict[str, SourceDocument], image_root: Path
) -> Path:
    return build_gallery(lesson, tmp_path / "gallery", sources=sources, image_root=image_root)


def page(path: Path) -> Deck:
    return load(path)


def test_writes_one_deck_per_theme_and_an_index(
    gallery: Path,
    tmp_path: Path,
    lesson: Lesson,
    sources: dict[str, SourceDocument],
    image_root: Path,
) -> None:
    folder = tmp_path / "gallery"
    assert gallery == folder / "index.html"
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        ["index.html", *(f"{t}.html" for t in theme_ids())]
    )
    for theme in theme_ids():
        expected = export_html(
            lesson, tmp_path / "again.html", sources=sources, image_root=image_root, theme=theme
        )
        assert (folder / f"{theme}.html").read_text(encoding="utf-8") == expected.read_text(
            encoding="utf-8"
        )
        deck = load(folder / f"{theme}.html")
        assert deck.root.one("body").attrs["data-theme"] == theme
        assert deck.root.find("img")  # the pictures are embedded


def test_index_shows_each_theme_with_its_manifest_and_a_live_preview(gallery: Path) -> None:
    index = page(gallery)
    cards = index.root.find("article", "theme")
    infos = available_themes()
    assert [c.attrs["data-theme"] for c in cards] == [i.id for i in infos]
    for card, info in zip(cards, infos, strict=True):
        assert card.one("h2").text() == info.label
        assert card.one(class_="desc").text() == info.description
        assert info.suits in card.one(class_="suits").text()
        frame = card.one("iframe")
        assert frame.attrs["src"] == f"{info.id}.html"
        link = card.one("a", "open")
        assert link.attrs["href"] == f"{info.id}.html"
        assert link.attrs["target"] == "_blank"
        assert "Open full screen" in link.text()
        assert card.one("button", "prev") and card.one("button", "next")
        assert len(card.find("button", "star")) == 5
        assert card.one("textarea", "comment")
    assert index.root.one("html").attrs["lang"] == "vi"
    assert "Chấm điểm (Rate)" in index.text
    assert index.root.one("button", id="export").text().startswith("Xuất đánh giá")
    assert index.root.one("textarea", id="export-text")


def test_index_is_self_contained(gallery: Path) -> None:
    text = gallery.read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    assert "url(" not in text
    assert "@import" not in text
    assert "<link" not in text
    assert len(re.findall(r"<script", text)) == 1
    # the only references to other files are the decks
    assert sorted(set(re.findall(r'(?:src|href)="([^"#]+)"', text))) == sorted(
        f"{t}.html" for t in theme_ids()
    )


def test_index_script_drives_the_previews_and_keeps_ratings_in_local_storage(
    gallery: Path,
) -> None:
    script = page(gallery).root.one("script").text()
    for needle in (
        "'classdeck:prev'",
        "'classdeck:next'",
        "'classdeck:slide'",
        "postMessage",
        "localStorage.setItem",
        "localStorage.getItem",
        "navigator.clipboard.writeText",
        "execCommand('copy')",
        "event.source !== frame.contentWindow",
    ):
        assert needle in script, needle
    # every storage access is guarded
    assert script.count("try {") >= 3
    assert "output.value = text" in script  # the summary is also shown in the textarea
    assert "' " + chr(8212) + " '" in script  # "theme: stars - comment"


def test_decks_understand_the_messages_the_index_sends(gallery: Path) -> None:
    deck_script = load(gallery.parent / "kids.html").root.one("script").text()
    index_script = page(gallery).root.one("script").text()
    for message in re.findall(r"type: '(classdeck:\w+)'", index_script):
        if message != "classdeck:slide":  # that one goes the other way
            assert f"'{message}'" in deck_script


def test_theme_subset_keeps_the_given_order(tmp_path: Path, lesson: Lesson) -> None:
    index = build_gallery(lesson, tmp_path / "g", themes=["plain", "kids"])
    cards = page(index).root.find("article", "theme")
    assert [c.attrs["data-theme"] for c in cards] == ["plain", "kids"]

    only = build_gallery(lesson, tmp_path / "one", themes=["plain"])
    assert sorted(p.name for p in only.parent.iterdir()) == ["index.html", "plain.html"]


def test_unknown_theme_and_blocked_lesson_write_nothing(tmp_path: Path, lesson: Lesson) -> None:
    out = tmp_path / "g"
    with pytest.raises(ValueError, match="unknown theme 'neon'"):
        build_gallery(lesson, out, themes=["kids", "neon"])
    with pytest.raises(ValueError, match="at least one"):
        build_gallery(lesson, out, themes=[])
    assert not out.exists()
    lesson.blocks["b_07"].checks.append(FAILURE)
    with pytest.raises(ExportBlocked):
        build_gallery(lesson, out)
    assert not out.exists()


def test_titles_are_escaped(tmp_path: Path, lesson: Lesson) -> None:
    lesson.title = 'Unit <b>1</b> & "more"'
    index = build_gallery(lesson, tmp_path / "g", themes=["kids"])
    text = index.read_text(encoding="utf-8")
    assert "<b>1</b>" not in text
    assert "Unit &lt;b&gt;1&lt;/b&gt; &amp; &quot;more&quot;" in text or "&amp; " in text
    assert page(index).root.one(class_="lesson").text() == lesson.title
