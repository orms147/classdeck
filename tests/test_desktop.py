"""The desktop entry point: per-user folders and a free port (no window is opened)."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

from classdeck.app import desktop


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    bundle = tmp_path / "bundle"
    (bundle / "curriculum" / "gdpt-2018").mkdir(parents=True)
    (bundle / "curriculum" / "gdpt-2018" / "README.md").write_text("seed", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(desktop, "_bundle_root", lambda: bundle)
    monkeypatch.setenv("CLASSDECK_HOME", str(tmp_path / "settings"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    for name in ("CLASSDECK_WORKDIR", "CLASSDECK_CACHE_DIR", "CLASSDECK_CURRICULUM_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_folders_are_per_user_and_the_curriculum_is_copied(folders: Path) -> None:
    desktop.prepare_folders()
    work = folders / "home" / "Documents" / "ClassDeck"
    assert os.environ["CLASSDECK_WORKDIR"] == str(work) and work.is_dir()
    assert Path.cwd() == work
    assert os.environ["CLASSDECK_CACHE_DIR"] == str(folders / "local" / "ClassDeck" / "cache")
    curriculum = folders / "settings" / "curriculum"
    assert os.environ["CLASSDECK_CURRICULUM_DIR"] == str(curriculum)
    assert (curriculum / "gdpt-2018" / "README.md").read_text(encoding="utf-8") == "seed"


def test_a_copied_curriculum_is_never_overwritten(folders: Path) -> None:
    kept = folders / "settings" / "curriculum" / "gdpt-2018"
    kept.mkdir(parents=True)
    (kept / "README.md").write_text("teacher's units", encoding="utf-8")
    desktop.prepare_folders()
    assert (kept / "README.md").read_text(encoding="utf-8") == "teacher's units"


def test_folders_named_in_the_environment_win(
    folders: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLASSDECK_WORKDIR", str(folders / "mine"))
    monkeypatch.setenv("CLASSDECK_CURRICULUM_DIR", str(folders / "curr"))
    desktop.prepare_folders()
    assert os.environ["CLASSDECK_WORKDIR"] == str(folders / "mine")
    assert os.environ["CLASSDECK_CURRICULUM_DIR"] == str(folders / "curr")
    assert not (folders / "settings" / "curriculum").exists()


def test_a_busy_port_is_skipped() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        assert desktop._free_port() != taken


def test_a_windowed_program_logs_to_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.stdout", None)
    monkeypatch.setattr("sys.stderr", None)
    desktop.ensure_streams()
    assert sys.stdout is not None and not sys.stdout.isatty()
    print("started", file=sys.stderr)
    sys.stderr.flush()
    assert "started" in (tmp_path / "ClassDeck" / "classdeck.log").read_text(encoding="utf-8")
    sys.stdout.close()
