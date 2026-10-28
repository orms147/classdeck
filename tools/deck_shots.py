"""Screenshot slides of an HTML deck with headless Edge (or Chrome), for theme authors.

    python tools/deck_shots.py <deck.html> --slides 1,3,5 --out <dir> [--size 1600x900]
                               [--state pupil|teacher|notes|edit] [--reveal] [--all]
                               [--browser <path>]

Writes ``<out>/<deck name>-slide<NN>.png`` for every requested slide (1-based, as in the deck's
``#N`` address) and prints the paths. The deck is driven over the DevTools protocol: transitions
are switched off and the shot waits for the slide to settle (the auto-fit has run), so no picture
is faded. A slide number past the end of the deck is reported and skipped (the deck is never
shot twice on its last slide); ``--all`` shoots every slide. ``--reveal`` taps everything open
(answers, dialogue lines, flashcard meanings) first. Developer tool only; nothing here is part
of the package.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deck_check


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
    deck: Path,
    out: Path,
    slides: list[int] | None,
    *,
    size: tuple[int, int],
    state: str,
    reveal: bool,
    browser: str | None,
) -> list[Path]:
    """Shoot ``slides`` (``None``: all); returns the written files."""
    exe = deck_check.find_browser(browser)
    if exe is None:
        raise SystemExit("no Edge or Chrome found; pass --browser <path>")
    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="deck-shots-", ignore_cleanup_errors=True) as profile:
        process = deck_check._launch(exe, profile, size)
        try:
            client = deck_check.Cdp(
                deck_check._page_socket(deck_check._debug_port(profile, process))
            )
            try:
                client.call("Page.enable")
                client.call(
                    "Emulation.setDeviceMetricsOverride",
                    width=size[0],
                    height=size[1],
                    deviceScaleFactor=1,
                    mobile=False,
                )
                client.call("Page.navigate", url=deck.resolve().as_uri())
                client.wait_event("Page.loadEventFired")

                def run(expression: str) -> Any:
                    reply = client.call(
                        "Runtime.evaluate", timeout=60, expression=expression, returnByValue=True
                    )
                    if "exceptionDetails" in reply:
                        raise SystemExit(f"the deck failed in the browser: {reply}")
                    return reply["result"].get("value")

                run(deck_check.PAGE_SCRIPT)  # also switches animations and transitions off
                run(f"window.__deckCheck.setState({json.dumps(state)})")
                time.sleep(0.3)
                count = int(run("window.__deckCheck.count()"))
                wanted = slides if slides is not None else list(range(1, count + 1))
                for number in wanted:
                    if number > count:
                        print(f"slide {number}: the deck has {count} slides here; skipped")
                        continue
                    run(f"window.__deckCheck.goto({number - 1})")
                    if reveal:
                        run("window.__deckCheck.reveal()")
                    time.sleep(0.15)
                    for _ in range(40):  # let the auto-fit run
                        if not run("document.body.hasAttribute('data-fit-pending')"):
                            break
                        time.sleep(0.1)
                    time.sleep(0.1)
                    data = client.call("Page.captureScreenshot", format="png")["data"]
                    target = (out / f"{deck.stem}-slide{number:02d}.png").resolve()
                    target.write_bytes(base64.b64decode(data))
                    written.append(target)
            finally:
                client.close()
        finally:
            deck_check._stop(process)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot slides of an HTML deck.")
    parser.add_argument("deck", type=Path, help="the deck .html file")
    parser.add_argument("--slides", type=parse_slides, default=[1], help="e.g. 1,3,5 or 2-6")
    parser.add_argument("--all", action="store_true", help="every slide of the deck")
    parser.add_argument("--out", type=Path, required=True, help="folder for the PNG files")
    parser.add_argument("--size", type=parse_size, default=(1600, 900), help="default 1600x900")
    parser.add_argument(
        "--state", choices=deck_check.STATES, default="pupil", help="the view to shoot"
    )
    parser.add_argument("--teacher", action="store_true", help="same as --state notes")
    parser.add_argument("--reveal", action="store_true", help="show answers and hidden parts")
    parser.add_argument("--browser", help="path to msedge/chrome (found automatically)")
    args = parser.parse_args(argv)

    if not args.deck.is_file():
        raise SystemExit(f"no such deck: {args.deck}")
    args.out.mkdir(parents=True, exist_ok=True)
    state = "notes" if args.teacher else args.state
    paths = shoot(
        args.deck,
        args.out,
        None if args.all else args.slides,
        size=args.size,
        state=state,
        reveal=args.reveal,
        browser=args.browser,
    )
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
