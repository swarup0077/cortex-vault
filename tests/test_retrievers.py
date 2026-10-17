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
