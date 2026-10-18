from vault.search.snippet import make_snippet


def test_marks_terms_with_crude_stemming() -> None:
    s = make_snippet("The definition of SVD: it is defined for every matrix.", "where is SVD defined")
    assert s == "The <mark>definition</mark> of <mark>SVD</mark>: it is <mark>defined</mark> for every matrix."


def test_escapes_html() -> None:
    s = make_snippet("<b>error</b> & more", "error")
    assert s == "&lt;b&gt;<mark>error</mark>&lt;/b&gt; &amp; more"


def test_window_centres_on_matches() -> None:
    text = ("filler words here " * 30) + "the gradient descent step " + ("more filler " * 30)
    s = make_snippet(text, "gradient descent", width=120)
    assert "<mark>gradient</mark> <mark>descent</mark>" in s
    assert s.startswith("…") and s.endswith("…")
    assert len(s.replace("<mark>", "").replace("</mark>", "")) <= 122


def test_no_match_returns_start() -> None:
    s = make_snippet("alpha beta gamma " * 20, "zeta", width=50)
    assert s.startswith("alpha beta") and s.endswith("…") and "<mark>" not in s


def test_highlight_marks_the_whole_text_and_escapes_it() -> None:
    from vault.search.snippet import highlight

    text = "Gradient <descent> steps " + "x " * 300 + "against the gradient"
    out = highlight(text, "gradient descent")
    assert out.count("<mark>") == 3 and "&lt;<mark>descent</mark>&gt;" in out and out.endswith("<mark>gradient</mark>")
