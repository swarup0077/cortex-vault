"""OS-specific bits live here so the rest of the code stays portable (CLAUDE.md)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path, PurePosixPath

# Folder names that are system or tool internals on any OS.
SYSTEM_DIR_NAMES = frozenset(
    {"$RECYCLE.BIN", "System Volume Information", "__pycache__", "node_modules"}
)

_HIDDEN_OR_SYSTEM = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 2) | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 4)


def is_hidden(path: Path, st: os.stat_result | None = None) -> bool:
    """Dotfiles everywhere; on Windows also the hidden/system attributes."""
    if path.name.startswith("."):
        return True
    attrs = getattr(st if st is not None else path.stat(), "st_file_attributes", 0)
    return bool(attrs & _HIDDEN_OR_SYSTEM)


def open_file(path: Path) -> None:
    """Open a file in its default app (local mode's "Open file"). Never called in demo mode."""
    if not path.is_file():
        raise FileNotFoundError(path)
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows only
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def resolve_stored_path(stored: str, base: Path) -> Path:
    """A `paths.path` value as a real path. The demo index stores repo-relative POSIX paths (`corpus/x.png`,
    SPEC §5), resolved against `base`; everything else is absolute already."""
    p = Path(stored)
    if p.is_absolute():
        return p
    return base.joinpath(*PurePosixPath(stored).parts)
