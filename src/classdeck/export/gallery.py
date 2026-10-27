"""Theme gallery: one lesson rendered in every deck theme, plus a page to compare and rate them.

``build_gallery`` writes ``<out_dir>/<theme>.html`` (a normal deck per theme) and
``<out_dir>/index.html``: a self-contained page (inline CSS and JavaScript, no network) with a live
preview of each deck, previous/next buttons that drive the preview through ``postMessage``, an
"open full screen" link and a rating row (stars and a comment) that is remembered in the browser
and can be exported as plain text. The page is addressed to Vietnamese teachers: its interface
text is Vietnamese with the English label in parentheses where that helps.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from importlib import resources
from pathlib import Path

from classdeck.export.html import ThemeInfo, available_themes, export_html
from classdeck.export.source_map import ensure_exportable
from classdeck.lesson import Lesson, SourceDocument

INDEX_NAME = "index.html"
PAGE_TEMPLATE = "gallery_page.html"
CARD_TEMPLATE = "gallery_card.html"
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def build_gallery(
    lesson: Lesson,
    out_dir: Path,
    *,
    sources: Mapping[str, SourceDocument] | None = None,
    image_root: Path | None = None,
    themes: Sequence[str] | None = None,
) -> Path:
    """Render ``lesson`` in each theme into ``out_dir`` and write the gallery page; return its path.

    ``themes`` limits and orders the themes by id (default: every available theme, by id).
    Raises ``ValueError`` for an unknown theme and
    :class:`~classdeck.export.source_map.ExportBlocked` when the lesson has failing checks; both
    are raised before anything is written.
    """
    known = {info.id: info for info in available_themes()}
    chosen = list(themes) if themes is not None else list(known)
    unknown = [theme for theme in chosen if theme not in known]
    if unknown:
        raise ValueError(f"unknown theme {unknown[0]!r}; available themes: {', '.join(known)}")
    if not chosen:
        raise ValueError("a gallery needs at least one theme")
    ensure_exportable(lesson)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for theme in chosen:
        export_html(
            lesson,
            target / f"{theme}.html",
            sources=sources,
            image_root=image_root,
            theme=theme,
        )
    index = target / INDEX_NAME
    index.write_text(
        _render_index(lesson, [known[theme] for theme in chosen]), encoding="utf-8", newline="\n"
    )
    return index


@lru_cache(maxsize=2)
def _resource(name: str) -> str:
    return resources.files("classdeck.export").joinpath(name).read_text(encoding="utf-8")


def _fill(template: str, values: Mapping[str, str]) -> str:
    """Replace every ``{{name}}`` once; inserted text is never scanned again."""
    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def _card(info: ThemeInfo) -> str:
    return _fill(
        _resource(CARD_TEMPLATE),
        {
            "id": html.escape(info.id, quote=True),
            "label": html.escape(info.label, quote=True),
            "description": html.escape(info.description),
            "suits": html.escape(info.suits),
        },
    ).strip()


def _render_index(lesson: Lesson, infos: Sequence[ThemeInfo]) -> str:
    return _fill(
        _resource(PAGE_TEMPLATE),
        {
            "title": html.escape(lesson.title),
            "lesson_id": html.escape(lesson.id, quote=True),
            "cards": "\n".join(_card(info) for info in infos),
        },
    )
