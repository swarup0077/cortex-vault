"""Walk the indexed roots and list candidate files (SPEC §6.1 step 1)."""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from vault.ingest.classify import EXT_KINDS, IMAGE_EXTS
from vault.platform import SYSTEM_DIR_NAMES, is_hidden

log = logging.getLogger(__name__)

TEXT_EXTS = frozenset(EXT_KINDS)
# HEIC only when pillow-heif is installed (SPEC §6.1); it's optional and not in requirements.txt.
_HEIF = importlib.util.find_spec("pillow_heif") is not None
INDEX_EXTS = TEXT_EXTS | (IMAGE_EXTS if _HEIF else IMAGE_EXTS - {".heic"})


@dataclass(frozen=True)
class FoundFile:
    path: Path
    root: Path
    size: int
    mtime: float


def _skip_dir(path: Path) -> bool:
    if path.name in SYSTEM_DIR_NAMES or path.name.startswith("$"):
        return True
    try:
        return is_hidden(path)
    except OSError:
        return True


def walk(root: Path, exts: frozenset[str] = INDEX_EXTS) -> Iterator[FoundFile]:
    """Yield allowed files under `root`, skipping hidden/system folders and Office lock files."""
    root = Path(root).resolve()

    def on_error(err: OSError) -> None:
        log.warning("cannot read %s: %s", err.filename, err.strerror)

    for dirpath, dirnames, filenames in os.walk(root, onerror=on_error):
        here = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if not _skip_dir(here / d))
        for name in sorted(filenames):
            path = here / name
            if path.suffix.lower() not in exts or name.startswith("~$"):
                continue
            try:
                st = path.stat()
            except OSError as e:
                log.warning("cannot stat %s: %s", path, e)
                continue
            if is_hidden(path, st):
                continue
            yield FoundFile(path=path, root=root, size=st.st_size, mtime=st.st_mtime)


def walk_roots(roots: Iterable[Path], exts: frozenset[str] = INDEX_EXTS) -> list[FoundFile]:
    """Walk several roots. A path reachable from two roots is listed once, under the first."""
    seen: dict[Path, FoundFile] = {}
    for root in roots:
        for f in walk(root, exts):
            seen.setdefault(f.path, f)
    return list(seen.values())


def sha256_file(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(block):
            h.update(chunk)
    return h.hexdigest()
