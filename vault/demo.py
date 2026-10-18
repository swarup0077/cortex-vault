"""The demo index (SPEC §2, §10, §15): built from `corpus/` on the laptop, committed as `demo_index/`, and opened
read-only on the server, which never indexes the corpus itself.

Its paths are stored relative to the repo (`corpus/screens/x.png`, SPEC §5): the laptop's absolute paths don't
exist on the server, and the public UI shouldn't show them. No image with GPS EXIF gets in (SPEC §10, §18).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

from PIL import Image

from vault import db
from vault.config import REPO_ROOT, Config
from vault.ingest.classify import IMAGE_EXTS
from vault.ingest.meta import GPS_IFD, register_heif
from vault.ingest.pipeline import IndexReport, ProgressFn, index_paths

log = logging.getLogger(__name__)

CORPUS_DIR = REPO_ROOT / "corpus"
DEMO_DIR = REPO_ROOT / "demo_index"


def _gps_tags(path: Path) -> dict:
    register_heif()
    try:
        with Image.open(path) as im:
            return dict(im.getexif().get_ifd(GPS_IFD))
    except Exception:  # not an image we can read: the indexer reports it
        return {}


def gps_files(folder: Path) -> list[Path]:
    """Images under `folder` that carry any GPS EXIF tag."""
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file() and _gps_tags(p))


def strip_gps(path: Path) -> bool:
    """Remove the GPS block from an image's EXIF, keeping the other tags (`vault scrub-exif`). JPEGs keep their
    quantisation tables, so the re-save loses as little as possible. Returns whether anything was removed."""
    if not _gps_tags(path):
        return False
    tmp = path.with_name(f".{path.name}.scrub")
    with Image.open(path) as im:
        im.load()
        exif = im.getexif()
        del exif[GPS_IFD]
        im.save(tmp, im.format, exif=exif, **({"quality": "keep"} if im.format == "JPEG" else {}))
    os.replace(tmp, path)
    return True


def build_demo(corpus: Path = CORPUS_DIR, out: Path = DEMO_DIR, cfg: Config | None = None, *,
               base: Path = REPO_ROOT, progress: ProgressFn | None = None) -> IndexReport:
    """Index `corpus` into a fresh index, store its paths relative to `base`, and replace `out` with it.

    Refuses a corpus with GPS EXIF (run `vault scrub-exif`) or with files that fail to index, so a broken demo
    index is never committed.
    """
    cfg = cfg or Config()
    corpus, base = corpus.resolve(), base.resolve()
    if bad := gps_files(corpus):
        raise ValueError("images with GPS EXIF (run `python -m vault scrub-exif <folder>`): "
                         + ", ".join(p.relative_to(corpus).as_posix() for p in bad))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".demo-build-") as tmp:
        build = Path(tmp) / "demo_index"
        build_cfg = replace(cfg, data_dir=build)
        conn = db.connect(build_cfg.db_path)
        try:
            report = index_paths(conn, [corpus], build_cfg, progress=progress)
            if report.errors:
                raise ValueError("files that failed to index: "
                                 + "; ".join(f"{e['path']}: {e['error']}" for e in report.errors))
            with db.transaction(conn):
                for (path,) in conn.execute("SELECT path FROM paths").fetchall():
                    rel = Path(path).relative_to(base).as_posix()
                    conn.execute("UPDATE paths SET path = ? WHERE path = ?", (rel, path))
                root = corpus.relative_to(base).as_posix()
                conn.execute("UPDATE paths SET root = ?", (root,))
                db.set_roots(conn, [root])
                conn.execute("DELETE FROM runs")  # the build's report names the laptop's paths
            db.compact(conn)
        finally:
            conn.close()
        if out.exists():
            shutil.rmtree(out)
        shutil.move(str(build), str(out))
    return report
