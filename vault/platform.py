"""OS-specific bits live here so the rest of the code stays portable (CLAUDE.md)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

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
