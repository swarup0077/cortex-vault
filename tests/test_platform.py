"""OS-specific bits (vault/platform.py): "Open file" on each OS, without opening anything."""

import sys
from pathlib import Path

import pytest

from vault import platform


@pytest.mark.parametrize(("os_name", "command"), [("darwin", "open"), ("linux", "xdg-open")])
def test_open_file_uses_the_os_opener(monkeypatch, tmp_path, os_name, command) -> None:
    calls = []
    monkeypatch.setattr(sys, "platform", os_name)
    monkeypatch.setattr(platform.subprocess, "Popen", lambda args, **kw: calls.append(args))
    f = tmp_path / "my notes.pdf"
    f.write_bytes(b"x")
    platform.open_file(f)
    assert calls == [[command, str(f)]]


def test_open_file_on_windows_uses_startfile(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform.os, "startfile", lambda p: calls.append(p), raising=False)
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    platform.open_file(f)
    assert calls == [str(f)]


def test_open_file_refuses_a_missing_file(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        platform.open_file(tmp_path / "gone.pdf")


def test_resolve_stored_path_handles_absolute_and_repo_relative(tmp_path) -> None:
    assert platform.resolve_stored_path(str(tmp_path / "x.png"), Path("/base")) == tmp_path / "x.png"
    assert platform.resolve_stored_path("corpus/screens/x.png", tmp_path) == tmp_path / "corpus" / "screens" / "x.png"
