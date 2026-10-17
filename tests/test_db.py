import numpy as np
import pytest

from vault import db


def _add_file(conn, sha="a" * 64) -> int:
    return conn.execute("INSERT INTO files(sha256, kind) VALUES (?, 'doc')", (sha,)).lastrowid


def test_schema_and_version(conn) -> None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"files", "paths", "chunks", "ocr_lines", "chunks_fts", "text_vectors", "image_vectors", "runs", "meta"} <= tables
    assert db.get_meta(conn, "schema_version") == db.SCHEMA_VERSION


def test_delete_file_removes_fts_and_cascades(conn) -> None:
    with db.transaction(conn):
        fid = _add_file(conn)
        conn.execute("INSERT INTO paths VALUES ('C:/x y/a.txt', ?, 'C:/x y', 1, 1.0, 1)", (fid,))
        cid = db.insert_chunk(conn, fid, None, 0, "doc_text", "hello world", np.ones(4, np.float32))
        conn.execute("INSERT INTO ocr_lines(chunk_id, line_no, block_no, text) VALUES (?, 0, 0, 'x')", (cid,))
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'hello'").fetchone()[0] == 1

    with db.transaction(conn):
        db.delete_file(conn, fid)
    for table in ("files", "paths", "chunks", "chunks_fts", "text_vectors", "ocr_lines"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table


def test_transaction_rolls_back(conn) -> None:
    with pytest.raises(RuntimeError):
        with db.transaction(conn):
            _add_file(conn)
            raise RuntimeError
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0


def test_update_chunk_text_keeps_id(conn) -> None:
    with db.transaction(conn):
        fid = _add_file(conn)
        cid = db.insert_chunk(conn, fid, None, 0, "path", "old name", np.zeros(4, np.float32))
        db.update_chunk_text(conn, cid, "new name", np.ones(4, np.float32))
    assert conn.execute("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'new'").fetchone()[0] == cid
    assert conn.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'old'").fetchone()[0] == 0
    blob = conn.execute("SELECT vec FROM text_vectors WHERE chunk_id = ?", (cid,)).fetchone()[0]
    assert db.blob_to_vec(blob).tolist() == [1.0] * 4


def test_model_mismatch(conn) -> None:
    db.check_text_model(conn, "model-a")  # empty DB, no claim: fine, nothing stored
    assert db.get_meta(conn, "text_model") is None
    db.check_text_model(conn, "model-a", claim=True)
    db.check_text_model(conn, "model-a")
    with pytest.raises(db.ModelMismatchError, match="reindex"):
        db.check_text_model(conn, "model-b")


def test_roots_merge(conn) -> None:
    db.add_roots(conn, ["C:/a", "C:/b"])
    db.add_roots(conn, ["C:/b", "D:/c d"])
    assert db.get_roots(conn) == ["C:/a", "C:/b", "D:/c d"]


def test_deleted_text_is_wiped_from_the_db_file(conn, cfg) -> None:
    """`secure_delete` zeroes deleted rows; FTS5's own secure-delete drops their terms from its index. The hard
    case for FTS5 is a big, fully merged index with no writes after the delete (e.g. a demo session at rest)."""
    secret = b"zqxjvplk"  # the porter stemmer leaves it alone, so the index holds the same bytes
    with db.transaction(conn):
        for i in range(2000):
            fid = _add_file(conn, f"{i:064d}")
            text = "invoice zqxjvplk for march" if i == 1000 else f"lecture notes part {i}"
            cid = db.insert_chunk(conn, fid, None, 0, "ocr", text, np.ones(4, np.float32))
            if i == 1000:
                target = fid
                conn.execute("INSERT INTO ocr_lines(chunk_id, line_no, block_no, text) VALUES (?, 0, 0, ?)", (cid, text))
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    assert cfg.db_path.read_bytes().count(secret) == 4  # chunks, ocr_lines, FTS content, FTS index

    with db.transaction(conn):
        db.delete_file(conn, target)
    assert secret not in cfg.db_path.read_bytes()


def test_fts_secure_delete_is_written_once(cfg) -> None:
    db.connect(cfg.db_path).close()
    c = db.connect(cfg.db_path)
    changes = c.total_changes
    db._fts_secure_delete(c)
    assert c.total_changes == changes  # already on: reopening the (read-only) demo index writes nothing
    assert c.execute("PRAGMA secure_delete").fetchone()[0] == 1
    c.close()
