"""Text from .docx, .pptx, .txt and .md (SPEC §6.3)."""

from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import docx

from vault.config import Config
from vault.ingest.records import Extracted, Segment, SkipFile

# Members smaller than this once unpacked can't make a meaningful bomb, whatever their ratio.
_RATIO_FLOOR = 1 << 20


def zip_guard(path: Path, max_xml_bytes: int, max_ratio: float, max_total: int | None = None) -> None:
    """Refuse zip bombs, not big decks.

    - any member over 1 MB unpacked with a compression ratio above `max_ratio` (media compresses
      ~1:1, so a bomb can't hide among it);
    - XML parts totalling more than `max_xml_bytes` unpacked (media is not counted);
    - more than `max_total` unpacked in all, only when set (demo uploads).
    """
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
    for i in infos:
        if i.file_size > _RATIO_FLOOR and i.file_size > max_ratio * max(i.compress_size, 1):
            ratio = i.file_size / max(i.compress_size, 1)
            raise SkipFile(f"zip guard: {i.filename} compression ratio {ratio:.0f} exceeds {max_ratio:.0f}")
    xml = sum(i.file_size for i in infos if i.filename.endswith((".xml", ".rels")))
    if xml > max_xml_bytes:
        raise SkipFile(f"zip guard: XML parts {xml / 2**20:.0f} MB exceed {max_xml_bytes / 2**20:.0f} MB")
    if max_total is not None:
        total = sum(i.file_size for i in infos)
        if total > max_total:
            raise SkipFile(f"zip guard: {total / 2**20:.0f} MB uncompressed exceeds {max_total / 2**20:.0f} MB")


def _guard(path: Path, cfg: Config) -> None:
    zip_guard(path, cfg.zip_max_xml_bytes, cfg.zip_max_ratio, cfg.zip_max_total)


def _iso(dt: datetime | None) -> str | None:
    return dt.replace(tzinfo=None).isoformat(timespec="seconds") if dt else None


def extract_docx(path: Path, cfg: Config) -> Extracted:
    """Paragraphs in order, then table cells row by row. Word stores no page numbers."""
    _guard(path, cfg)
    d = docx.Document(str(path))
    segments = [Segment(p.text, None, "doc_text") for p in d.paragraphs if p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = list(dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip()))  # merged cells repeat
            if cells:
                segments.append(Segment(" | ".join(cells), None, "doc_text"))
    return Extracted(segments=segments, doc_date=_iso(d.core_properties.created))


# ---------------------------------------------------------------- pptx, read straight from the zip
# python-pptx loads every part, media included, into memory. Decks with video run to hundreds of MB,
# so we parse only the presentation, slide, notes and core-properties XML.

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "dcterms": "http://purl.org/dc/terms/",
}
_P, _A, _R = (f"{{{_NS[k]}}}" for k in ("p", "a", "r"))
_TITLE_TYPES = ("title", "ctrTitle")


def _rels(zf: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relationship id → (type, part name) for internal relationships of `part` ('' = package root)."""
    folder, name = posixpath.split(part)
    rels = posixpath.join(folder, "_rels", f"{name}.rels")
    if rels not in zf.NameToInfo:
        return {}
    out = {}
    for rel in ET.fromstring(zf.read(rels)).findall("rel:Relationship", _NS):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(folder, target))
        out[rel.get("Id", "")] = (rel.get("Type", ""), resolved)
    return out


def _para_text(p: ET.Element) -> str:
    out = []
    for el in p.iter():
        if el.tag == f"{_A}t":
            out.append(el.text or "")
        elif el.tag == f"{_A}br":
            out.append("\n")
    return "".join(out)


def _body_text(body: ET.Element) -> str:
    return "\n".join(_para_text(p) for p in body.findall("a:p", _NS))


def _shape_texts(tree: ET.Element) -> Iterator[tuple[bool, str]]:
    """(is_title, text) for each text frame and table row, in document order, recursing into groups."""
    for shape in tree:
        if shape.tag == f"{_P}grpSp":
            yield from _shape_texts(shape)
        elif shape.tag == f"{_P}sp":
            body = shape.find("p:txBody", _NS)
            if body is not None:
                ph = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
                yield ph is not None and ph.get("type") in _TITLE_TYPES, _body_text(body)
        elif shape.tag == f"{_P}graphicFrame":
            for tr in shape.iter(f"{_A}tr"):
                cells = ("\n".join(_para_text(p) for p in tc.iter(f"{_A}p")) for tc in tr.findall("a:tc", _NS))
                yield False, " | ".join(c for c in cells if c.strip())


def _notes_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    for rel_type, target in _rels(zf, slide_part).values():
        if rel_type.endswith("/notesSlide") and target in zf.NameToInfo:
            for sp in ET.fromstring(zf.read(target)).iter(f"{_P}sp"):
                ph = sp.find("p:nvSpPr/p:nvPr/p:ph", _NS)
                body = sp.find("p:txBody", _NS)
                if ph is not None and ph.get("type") == "body" and body is not None:
                    return _body_text(body)
    return ""


def _slide_text(zf: zipfile.ZipFile, slide_part: str) -> str:
    """Title first, then every other text frame and table row, then speaker notes."""
    tree = ET.fromstring(zf.read(slide_part)).find("p:cSld/p:spTree", _NS)
    shapes = list(_shape_texts(tree)) if tree is not None else []
    title = next((i for i, (is_title, _) in enumerate(shapes) if is_title), None)
    parts = [shapes[title][1]] if title is not None else []
    parts += [text for i, (_, text) in enumerate(shapes) if i != title]
    parts.append(_notes_text(zf, slide_part))
    return "\n".join(p for p in parts if p.strip())


def _core_created(zf: zipfile.ZipFile) -> str | None:
    for rel_type, target in _rels(zf, "").values():
        if rel_type.endswith("/core-properties") and target in zf.NameToInfo:
            el = ET.fromstring(zf.read(target)).find("dcterms:created", _NS)
            try:
                return _iso(datetime.fromisoformat((el.text or "").strip())) if el is not None else None
            except ValueError:
                return None
    return None


def extract_pptx(path: Path, cfg: Config) -> Extracted:
    """Per slide: title, every text frame, tables, then speaker notes. page = slide number."""
    _guard(path, cfg)
    with zipfile.ZipFile(path) as zf:
        pres = next(t for typ, t in _rels(zf, "").values() if typ.endswith("/officeDocument"))
        pres_rels = _rels(zf, pres)
        ids = ET.fromstring(zf.read(pres)).findall("p:sldIdLst/p:sldId", _NS)
        slide_parts = [pres_rels[s.get(f"{_R}id", "")][1] for s in ids]
        segments = []
        for n, part in enumerate(slide_parts, start=1):
            text = _slide_text(zf, part)
            if text:
                segments.append(Segment(text, n, "slide_text"))
        return Extracted(segments=segments, pages=len(slide_parts), doc_date=_core_created(zf))


def extract_text(path: Path) -> Extracted:
    """.txt / .md as UTF-8 (errors replaced); paragraphs split on blank lines."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    paras = [p for p in re.split(r"\n\s*\n", raw) if p.strip()]
    return Extracted(segments=[Segment(p, None, "doc_text") for p in paras])
