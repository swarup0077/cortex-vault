import pytest

from vault.search.parse import ParsedQuery, parse
from vault.search.retrievers import filter_sql, fts_all_query, fts_query


def test_parse_passes_text_through() -> None:
    assert parse("  error   0x80070005 ") == ParsedQuery(text="error 0x80070005")
    assert parse("").filter_only


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Singular value", '"singular" OR "value"'),
        (
            'AND OR NOT "x" NEAR(a b) col:val *',
            '"not" OR "x" OR "near" OR "b" OR "col" OR "val"',  # "and", "or", "a" are stopwords
        ),
        ("a a b", '"b"'),
        ("!!!", None),
        ("where is the wifi password", '"wifi" OR "password"'),
        ("the who", '"the" OR "who"'),  # nothing but stopwords: keep them all
    ],
)
def test_fts_query_is_quoted(text, expected) -> None:
    assert fts_query(ParsedQuery(text=text)) == expected


def test_fts_query_phrases_are_required() -> None:
    q = fts_query(ParsedQuery(text="error", phrases=('access "denied"',)))
    assert q == '("error") AND "access ""denied"""'


@pytest.mark.parametrize("pq, expected", [
    (ParsedQuery(text="where is the gradient clipping threshold"), '"gradient" AND "clipping" AND "threshold"'),
    (ParsedQuery(text="error", phrases=("access denied",)), '"error" AND "access denied"'),
    (ParsedQuery(text="", phrases=("access denied",)), None),  # phrase-only: the BM25 list already requires it
    (ParsedQuery(text="!!!"), None),
])
def test_fts_all_query(pq, expected) -> None:
    assert fts_all_query(pq) == expected


def test_filter_sql() -> None:
    where, params = filter_sql(ParsedQuery(text="", kinds=frozenset({"pdf", "doc"}), date_from="2025-01-01"))
    assert where == "f.status = 'ok' AND f.kind IN (?,?) AND f.effective_date >= ?"
    assert params == ["doc", "pdf", "2025-01-01"]


@pytest.mark.parametrize("text, n", [("0 田", 1), ("白 这", 1), ("M", 1), ("B2 5G", 2), ("", 0), ("Lab 3", 3),
                                     ("Summary", 7), ("8:12 87%", 2), ("snake_case", 5)])
def test_longest_word(text, n) -> None:
    from vault.search.retrievers import longest_word

    assert longest_word(text) == n


def test_a_chunk_without_a_word_is_left_out_of_dense_search(tmp_path, fake_embedder) -> None:
    """Open item x: OCR junk such as "0 田" (no run of 3+ letters or digits) carries no meaning, yet sits close to
    many short queries (bench/calibrate.py tiny). It gets no place in dense search and stays in BM25."""
    from vault import db
    from vault.config import Config
    from vault.ingest.pipeline import index_paths
    from vault.search.engine import search
    from vault.search.retrievers import load_text_index

    files = tmp_path / "files"
    files.mkdir()
    (files / "junk.txt").write_text("0 田", encoding="utf-8")
    (files / "notice.txt").write_text("Lab 3 moved", encoding="utf-8")
    cfg = Config(data_dir=tmp_path / "data")
    conn = db.connect(cfg.db_path)
    index_paths(conn, [files], cfg)
    with_junk = load_text_index(conn, fake_embedder.dim)
    without = load_text_index(conn, fake_embedder.dim, cfg.dense_min_word)
    junk_chunks = {r[0] for r in conn.execute("SELECT c.id FROM chunks c JOIN paths p ON p.file_id = c.file_id "
                                              "WHERE p.path LIKE '%junk.txt' AND c.source != 'path'")}
    assert junk_chunks and junk_chunks <= set(with_junk.ids) and not junk_chunks & set(without.ids)
    assert len(without) == len(with_junk) - len(junk_chunks)  # "Lab 3 moved" keeps its vector
    resp = search(conn, "田", cfg)  # still found by its text
    assert [r.filename for r in resp.results] == ["junk.txt"] and resp.results[0].ranks.get("bm25") == 1
    assert not resp.results[0].ranks.get("dense")
