import numpy as np

from vault import db
from vault.models import l2_normalise
from vault.vectors import VectorIndex


def test_topk_order_and_mask() -> None:
    m = l2_normalise(np.array([[1, 0], [0.9, 0.1], [0, 1], [-1, 0]]))
    idx = VectorIndex(ids=[10, 11, 12, 13], matrix=m, groups=[1, 1, 2, 2])
    q = np.array([1, 0], np.float32)
    assert [i for i, _ in idx.search(q, 2)] == [10, 11]
    assert [i for i, _ in idx.search(q, 10)] == [10, 11, 12, 13]
    assert [i for i, _ in idx.search(q, 10, idx.mask_groups([2]))] == [12, 13]
    assert idx.search(q, 5, idx.mask_groups([])) == []


def test_from_query_roundtrip(conn) -> None:
    with db.transaction(conn):
        fid = conn.execute("INSERT INTO files(sha256, kind) VALUES ('x', 'doc')").lastrowid
        db.insert_chunk(conn, fid, None, 0, "doc_text", "a", np.array([0.6, 0.8], np.float32))
    idx = VectorIndex.from_query(
        conn, "SELECT v.chunk_id, v.vec, c.file_id FROM text_vectors v JOIN chunks c ON c.id = v.chunk_id", 2
    )
    assert len(idx) == 1 and idx.groups.tolist() == [fid]
    (cid, score), = idx.search(np.array([0.6, 0.8], np.float32), 3)
    assert abs(score - 1.0) < 1e-6


def test_empty_index(conn) -> None:
    idx = VectorIndex.from_query(conn, "SELECT chunk_id, vec FROM text_vectors", 4)
    assert len(idx) == 0 and idx.search(np.ones(4, np.float32), 5) == []
