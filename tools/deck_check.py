"""Check that every slide of an HTML deck fits its stage, in headless Edge (or Chrome).

    python tools/deck_check.py <deck.html> [--states pupil,teacher,notes,edit] [--size 1600x900]
                               [--json] [--browser <path>]

The deck is opened over the DevTools protocol; for each state the checker visits every slide, once
as it is shown and once with everything revealed (answers, dialogue lines, flashcard meanings):

- ``pupil``: the pupil view;
- ``teacher``: teacher mode with the notes panel closed (footer and badge visible);
- ``notes``: teacher mode with the notes panel open (the slide is 620 px narrower);
- ``edit``: the pupil view in edit mode (pencil bar, everything revealed, answers shown).

A slide fails when an element of its ``.slide-body`` ends outside the body, when an element's own
contents are wider or taller than its box (clipped text) or when content runs under the
pencil, the Save button, the footer, the AI-added badge, the notes panel or the progress dots.
Teacher-only slides (the notes and the source map) scroll by design and are not checked.
The exit status is 1 when any slide fails. Developer tool only; nothing here is part of the
package.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

BROWSER_CANDIDATES = (
    "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "C:/Program Files/Google/Chrome/Application/chrome.exe",
    "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
)
STATES = ("pupil", "teacher", "notes", "edit")


def find_browser(explicit: str | None = None) -> str | None:
    """The browser executable, or ``None`` when there is none."""
    if explicit:
        return explicit
    for candidate in BROWSER_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    for name in ("msedge", "microsoft-edge", "google-chrome", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


# --- the page-side checker (tools/deck_check.js) -------------------------------------------------

PAGE_SCRIPT = Path(__file__).with_name("deck_check.js").read_text(encoding="utf-8")


# --- DevTools protocol -------------------------------------------------------------------------


class Cdp:
    """A minimal DevTools protocol client over the ``websockets`` sync API."""

    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect

        try:
            self.ws = connect(url, max_size=None, open_timeout=30, legacy=True)
        except TypeError:  # an older websockets without the legacy flag
            self.ws = connect(url, max_size=None, open_timeout=30)
        self.next_id = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float = 120.0, **params: Any) -> dict[str, Any]:
        self.next_id += 1
        ident = self.next_id
        self.ws.send(json.dumps({"id": ident, "method": method, "params": params}))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError(f"{method} timed out")
            message = json.loads(self.ws.recv(timeout=left))
            if message.get("id") == ident:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)

    def wait_event(self, name: str, timeout: float = 60.0) -> None:
        if any(e["method"] == name for e in self.events):
            return
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            message = json.loads(self.ws.recv(timeout=end - time.monotonic()))
            if "method" in message:
                self.events.append(message)
                if message["method"] == name:
                    return
        raise TimeoutError(f"no {name} event")

    def close(self) -> None:
        self.ws.close()


def _launch(browser: str, profile: str, size: tuple[int, int]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-default-browser-check",
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            f"--window-size={size[0]},{size[1]}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Close the browser and its helper processes."""
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _debug_port(profile: str, process: subprocess.Popen[bytes]) -> int:
    marker = Path(profile) / "DevToolsActivePort"
    end = time.monotonic() + 30
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError("the browser exited before it opened a debugging port")
        try:
            lines = marker.read_text(encoding="utf-8").split() if marker.is_file() else []
        except OSError:  # the browser is still writing it
            lines = []
        if lines:
            return int(lines[0])
        time.sleep(0.1)
    raise RuntimeError("the browser did not open a debugging port")


def _page_socket(port: int) -> str:
    end = time.monotonic() + 30
    while time.monotonic() < end:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as reply:
            targets = json.loads(reply.read())
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return str(target["webSocketDebuggerUrl"])
        time.sleep(0.2)
    raise RuntimeError("the browser has no page to drive")


def check_deck(
    deck: Path,
    *,
    states: tuple[str, ...] = STATES,
    size: tuple[int, int] = (1600, 900),
    browser: str | None = None,
) -> list[dict[str, Any]]:
    """Visit every slide of ``deck`` in each state; one result dict per (state, slide).

    A browser that drops the connection (a busy machine) is started again, twice at most.
    """
    for attempt in range(3):
        try:
            return _check_once(deck, states=states, size=size, browser=browser)
        except (OSError, TimeoutError, ConnectionError, EOFError) as error:
            if attempt == 2:
                raise RuntimeError(f"the browser kept failing: {error}") from error
        except Exception as error:  # websockets raises its own hierarchy
            if attempt == 2 or type(error).__module__.split(".")[0] != "websockets":
                raise
    raise AssertionError("unreachable")


def _check_once(
    deck: Path,
    *,
    states: tuple[str, ...] = STATES,
    size: tuple[int, int] = (1600, 900),
    browser: str | None = None,
) -> list[dict[str, Any]]:
    """Visit every slide of ``deck`` in each state; one result dict per (state, slide)."""
    exe = find_browser(browser)
    if exe is None:
        raise RuntimeError("no Edge or Chrome found; pass --browser <path>")
    with tempfile.TemporaryDirectory(prefix="deck-check-", ignore_cleanup_errors=True) as profile:
        process = _launch(exe, profile, size)
        try:
            port = _debug_port(profile, process)
            client = Cdp(_page_socket(port))
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
                        "Runtime.evaluate",
                        timeout=60,
                        expression=expression,
                        returnByValue=True,
                    )
                    if "exceptionDetails" in reply:
                        raise RuntimeError(f"the checker failed in the page: {reply}")
                    return reply["result"].get("value")

                def wait_fit() -> None:
                    for _ in range(40):  # the deck re-fits a moment after a change
                        if not run("document.body.hasAttribute('data-fit-pending')"):
                            return
                        time.sleep(0.1)

                run(PAGE_SCRIPT)
                results: list[dict[str, Any]] = []
                for state in states:
                    run(f"window.__deckCheck.setState({json.dumps(state)})")
                    time.sleep(0.15)
                    for index in range(int(run("window.__deckCheck.count()"))):
                        run(f"window.__deckCheck.goto({index})")
                        time.sleep(0.06)
                        wait_fit()
                        info = run("window.__deckCheck.describe()")
                        if info is None:
                            continue
                        shown = run("window.__deckCheck.measure()")
                        if run("window.__deckCheck.reveal()"):
                            time.sleep(0.2)
                            wait_fit()
                            revealed = run("window.__deckCheck.measure()")
                        else:
                            revealed = {"fit": shown["fit"], "problems": []}
                        problems = [dict(p, phase="shown") for p in shown["problems"]]
                        problems += [dict(p, phase="revealed") for p in revealed["problems"]]
                        results.append(
                            {
                                "state": state,
                                "number": index + 1,
                                **info,
                                "fit": {"shown": shown["fit"], "revealed": revealed["fit"]},
                                "problems": problems,
                                "pass": not problems,
                            }
                        )
            finally:
                client.close()
        finally:
            _stop(process)
    return results


# --- command line ------------------------------------------------------------------------------


def parse_size(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        return int(width), int(height)
    except ValueError:
        raise argparse.ArgumentTypeError("size looks like 1600x900") from None


def parse_states(value: str) -> tuple[str, ...]:
    states = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [s for s in states if s not in STATES]
    if unknown or not states:
        raise argparse.ArgumentTypeError(f"states are some of {', '.join(STATES)}")
    return states


def failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if not r["pass"]]


def report(results: list[dict[str, Any]]) -> str:
    lines = []
    for state in dict.fromkeys(r["state"] for r in results):
        rows = [r for r in results if r["state"] == state]
        bad = failures(rows)
        fits = sorted({r["fit"]["revealed"] for r in rows})
        lines.append(
            f"{state}: {len(rows) - len(bad)}/{len(rows)} slides fit; "
            f"lowest --fit {fits[0] if fits else 1.0}"
        )
        for row in bad:
            lines.append(
                f"  slide {row['number']} ({row['kind']}, {row['title']!r}) "
                f"fit {row['fit']['shown']}/{row['fit']['revealed']}"
            )
            for problem in row["problems"]:
                lines.append(
                    f"    [{problem['phase']}] {problem['kind']}: {problem['el']} "
                    f"{problem['text']!r} - {problem['detail']}"
                )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check that every slide of a deck fits.")
    parser.add_argument("deck", type=Path, help="the deck .html file")
    parser.add_argument("--states", type=parse_states, default=STATES, help="default: all four")
    parser.add_argument("--size", type=parse_size, default=(1600, 900), help="default 1600x900")
    parser.add_argument("--json", action="store_true", help="print the full results as JSON")
    parser.add_argument("--browser", help="path to msedge/chrome (found automatically)")
    args = parser.parse_args(argv)

    if not args.deck.is_file():
        raise SystemExit(f"no such deck: {args.deck}")
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    try:
        results = check_deck(args.deck, states=args.states, size=args.size, browser=args.browser)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(report(results))
        print("FAIL" if failures(results) else "OK")
    return 1 if failures(results) else 0


if __name__ == "__main__":
    sys.exit(main())
