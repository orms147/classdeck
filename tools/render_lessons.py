"""Render stored lessons (``out/lessons/*.lesson.json``) as HTML decks, for checking and review.

    python tools/render_lessons.py --out <dir> [--theme sunny,chalkboard | --all-themes]
                                   [--workdir out] [lesson ids ...]

Writes ``<out>/<lesson id>-<theme>.html`` and prints the paths. Pictures come from
``<workdir>/sources`` and ``<workdir>/images``. Developer tool only; nothing here is part of the
package.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from classdeck.export import export_html
from classdeck.export.html import theme_ids
from classdeck.lesson import Lesson, SourceDocument


def load_sources(workdir: Path) -> dict[str, SourceDocument]:
    sources: dict[str, SourceDocument] = {}
    for path in sorted((workdir / "sources").glob("*.json")):
        document = SourceDocument.model_validate(json.loads(path.read_text(encoding="utf-8")))
        sources[document.doc_id] = document
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render stored lessons as HTML decks.")
    parser.add_argument("lessons", nargs="*", help="lesson ids (default: all in the workdir)")
    parser.add_argument("--out", type=Path, required=True, help="folder for the decks")
    parser.add_argument("--workdir", type=Path, default=Path("out"), help="default: out")
    parser.add_argument("--theme", default="sunny", help="comma-separated theme ids")
    parser.add_argument("--all-themes", action="store_true", help="every installed theme")
    args = parser.parse_args(argv)

    themes = theme_ids() if args.all_themes else [t.strip() for t in args.theme.split(",") if t]
    files = sorted((args.workdir / "lessons").glob("*.lesson.json"))
    if args.lessons:
        files = [f for f in files if f.name.removesuffix(".lesson.json") in args.lessons]
    sources = load_sources(args.workdir)
    args.out.mkdir(parents=True, exist_ok=True)
    for file in files:
        lesson = Lesson.model_validate(json.loads(file.read_text(encoding="utf-8")))
        for theme in themes:
            target = args.out / f"{lesson.id}-{theme}.html"
            export_html(
                lesson, target, sources=sources, image_root=args.workdir / "images", theme=theme
            )
            print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
