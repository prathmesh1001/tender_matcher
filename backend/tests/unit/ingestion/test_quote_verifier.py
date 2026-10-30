"""Quote verification against page text. Page texts are synthetic."""

from app.ingestion.quote_verifier import normalize_quote, partial_ratio, verify_quote

PAGE = (
    "Standards & Materials\n"
    "No. Components Materials WCB/ WCC Materials LCB/ LCC\n"
    "1 Body A 216 Gr. WCB/\nWCC A 352 Gr. LCB/ LCC\n"
    "Butterﬂy valves “Triple-offset” – 3” to 100”\n"  # noqa: RUF001 (the en dash is the case)
)


def test_normalize_folds_case_space_ligatures_and_quotes() -> None:
    assert normalize_quote("Butterﬂy  “TOBV”\n3”") == 'butterfly "tobv" 3"'


def test_exact_and_wrapped_pieces_score_one() -> None:
    page = normalize_quote(PAGE)
    assert partial_ratio(normalize_quote("Body"), page) == 1.0
    assert partial_ratio(normalize_quote("A 216 Gr. WCB/ WCC"), page) == 1.0  # wrapped cell
    assert (
        partial_ratio(normalize_quote('Butterfly valves "Triple-offset" - 3" to 100"'), page) == 1.0
    )


def test_small_difference_still_verifies() -> None:
    check = verify_quote(["A 216 Gr WCB/ WCC"], PAGE, 0.9)  # missing "." after Gr
    assert check.verified
    assert 0.9 <= check.score < 1.0


def test_quote_not_on_page_fails() -> None:
    check = verify_quote(["A 351 Gr. CF8M"], PAGE, 0.9)
    assert not check.verified
    assert check.failed_part == "A 351 Gr. CF8M"


def test_every_piece_must_be_on_the_page() -> None:
    check = verify_quote(["Body", "ASTM A105"], PAGE, 0.9)
    assert (check.verified, check.failed_part) == (False, "ASTM A105")


def test_unknown_page_or_no_pieces_never_verify() -> None:
    assert not verify_quote(["Body"], None, 0.9).verified
    assert not verify_quote(["", "  "], PAGE, 0.9).verified
    assert partial_ratio("", "x") == 0.0
