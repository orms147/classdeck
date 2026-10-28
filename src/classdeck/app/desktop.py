"""The desktop entry point: the local app in its own window, started from an icon.

See docs/local-app.md, "Desktop app (Windows)". The folders are set before any setting is read,
the server runs in a background thread on a free loopback port, and the window (pywebview) owns
the process: closing it stops the server. Without pywebview the default browser is used.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

APP_NAME = "ClassDeck"
FIRST_PORT = 8765
WINDOW_SIZE = (1280, 820)


def _bundle_root() -> Path:
    """Where the program's own files are: the PyInstaller folder, else the repository."""
    frozen = getattr(sys, "_MEIPASS", None)
    return Path(frozen) if frozen else Path(__file__).resolve().parents[3]


def _documents() -> Path:
    home = Path.home()
    documents = home / "Documents"
    return documents if documents.is_dir() else home


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(base) if base else Path.home() / "AppData" / "Local"


def prepare_folders() -> None:
    """Point the work folder, the cache and the curriculum at per-user folders (unless set).

    The curriculum is copied out of the program on first start, because units built from
    uploaded pages are written into it.
    """
    from classdeck.config import user_settings_dir

    work = Path(os.environ.get("CLASSDECK_WORKDIR") or _documents() / APP_NAME)
    work.mkdir(parents=True, exist_ok=True)
    os.environ["CLASSDECK_WORKDIR"] = str(work)
    os.environ.setdefault("CLASSDECK_CACHE_DIR", str(_local_appdata() / APP_NAME / "cache"))
    if not os.environ.get("CLASSDECK_CURRICULUM_DIR"):
        target = user_settings_dir() / "curriculum"
        source = _bundle_root() / "curriculum"
        if not target.is_dir() and source.is_dir():
            shutil.copytree(source, target)
        os.environ["CLASSDECK_CURRICULUM_DIR"] = str(target if target.is_dir() else source)
    # a stray .env next to the program must not change the teacher's settings
    os.chdir(work)


def ensure_streams() -> None:
    """Give a windowed program (no console: ``sys.stdout`` is None) a log file to write to.

    Uvicorn's log formatter asks the stream whether it is a terminal and fails on None.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    folder = _local_appdata() / APP_NAME
    folder.mkdir(parents=True, exist_ok=True)
    log = (folder / "classdeck.log").open("a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def _running_url() -> str | None:
    """The URL of a ClassDeck already serving on this machine, if any."""
    for port in range(FIRST_PORT, FIRST_PORT + 10):
        url = f"http://127.0.0.1:{port}/"
        try:
            with urllib.request.urlopen(url + "api/config", timeout=0.5) as reply:
                if "grades" in json.loads(reply.read().decode("utf-8")):
                    return url
        except (OSError, ValueError):
            continue
    return None


def _free_port() -> int:
    for port in range(FIRST_PORT, FIRST_PORT + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _start_server(port: int):
    import uvicorn

    from classdeck.app.server import create_app

    config = uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="classdeck-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("the ClassDeck server did not start")
    return server, thread


def _open_window(url: str) -> bool:
    """Show the app in a native window until it is closed; False when pywebview is missing."""
    try:
        import webview
    except ImportError:
        return False
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    width, height = WINDOW_SIZE
    webview.create_window(APP_NAME, url, width=width, height=height, min_size=(960, 640))
    webview.start()
    return True


def main() -> None:
    ensure_streams()
    prepare_folders()
    url = _running_url()
    if url:
        if not _open_window(url):
            webbrowser.open(url)
        return
    server, thread = _start_server(_free_port())
    url = f"http://127.0.0.1:{server.config.port}/"
    if _open_window(url):
        server.should_exit = True
        thread.join(timeout=5)
        return
    webbrowser.open(url)
    print(f"{APP_NAME} is running at {url}  (Ctrl+C to stop)")
    try:
        while thread.is_alive():
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
