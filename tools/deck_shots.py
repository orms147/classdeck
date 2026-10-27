"""Screenshot slides of an HTML deck with headless Edge (or Chrome), for theme authors.

    python tools/deck_shots.py <deck.html> --slides 1,3,5 --out <dir> [--size 1600x900]
                               [--teacher] [--browser <path>]

Writes ``<out>/<deck name>-slide<NN>.png`` for every requested slide (1-based, as in the deck's
``#N`` address) and prints the paths. Developer tool only; nothing here is part of the package.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BROWSER_CANDIDATES = (
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
)


def find_browser(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("no Edge or Chrome found; pass --browser <path>")


def parse_slides(value: str) -> list[int]:
    slides: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        first, last = int(low), int(high or low)
        slides.extend(range(first, last + 1))
    if not slides or min(slides) < 1:
        raise argparse.ArgumentTypeError("slides are 1-based numbers, e.g. 1,3,5 or 2-6")
    return slides


def parse_size(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError("size looks like 1600x900") from None


def shoot(
    browser: str, deck: Path, slide: int, target: Path, size: tuple[int, int], *, teacher: bool
) -> None:
    query = "?teacher=1" if teacher else ""
    url = f"{deck.resolve().as_uri()}{query}#{slide}"
    with tempfile.TemporaryDirectory(prefix="deck-shots-") as profile:
        command = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            f"--user-data-dir={profile}",
            f"--window-size={size[0]},{size[1]}",
            "--virtual-time-budget=1500",
            f"--screenshot={target}",
            url,
        ]
        subprocess.run(command, check=True, capture_output=True, timeout=120)
    if not target.is_file():
        raise SystemExit(f"the browser wrote no screenshot for slide {slide}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot slides of an HTML deck.")
    parser.add_argument("deck", type=Path, help="the deck .html file")
    parser.add_argument("--slides", type=parse_slides, default=[1], help="e.g. 1,3,5 or 2-6")
    parser.add_argument("--out", type=Path, required=True, help="folder for the PNG files")
    parser.add_argument("--size", type=parse_size, default=(1600, 900), help="default 1600x900")
    parser.add_argument("--teacher", action="store_true", help="teacher mode (notes, footers)")
    parser.add_argument("--browser", help="path to msedge/chrome (found automatically)")
    args = parser.parse_args(argv)

    if not args.deck.is_file():
        raise SystemExit(f"no such deck: {args.deck}")
    browser = find_browser(args.browser)
    args.out.mkdir(parents=True, exist_ok=True)
    for slide in args.slides:
        target = (args.out / f"{args.deck.stem}-slide{slide:02d}.png").resolve()
        shoot(browser, args.deck, slide, target, args.size, teacher=args.teacher)
        print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
