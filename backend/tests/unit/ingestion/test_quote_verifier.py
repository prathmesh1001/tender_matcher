"""Quote verification against page text. Page texts are synthetic."""

from difflib import SequenceMatcher

from app.ingestion.quote_verifier import exact_tokens, normalize_quote, partial_ratio, verify_quote

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


# ---------------------------------------------------------------- numbers and codes are exact

LONG_PAGE = (
    "Pressure ratings\n"
    "Dead-End Service (No downstream flanges and disc in closed position)\n"
    "2 to 12 inch (DN 50 - DN 300) 125 psi (12.0 barg)\n"
)
LONG_QUOTE = (
    "Dead-End Service (No downstream flanges and disc in closed position) "
    "2 to 12 inch (DN 50 - DN 300) 175 psi (12.0 barg)"
)


def test_exact_tokens_are_numbers_and_capital_codes() -> None:
    assert exact_tokens("A 216 Gr. WCB/ WCC, Class 150 (80 mm") == (
        "216", "wcb", "wcc", "150", "80",
    )  # fmt: skip
    assert exact_tokens("Body Materials") == ()


def test_class_150_quote_fails_on_a_class_300_page() -> None:
    check = verify_quote(
        ["Pressure rating: ASME Class 150"], "Pressure rating: ASME Class 300", 0.9
    )
    assert not check.verified


def test_one_wrong_number_in_a_long_quote_fails_despite_a_high_ratio() -> None:
    window = normalize_quote(LONG_PAGE)[normalize_quote(LONG_PAGE).find("dead") :]
    assert SequenceMatcher(None, normalize_quote(LONG_QUOTE), window).ratio() >= 0.9
    # Fuzzy alone would accept it; the exact-token rule is what rejects it.
    assert partial_ratio(normalize_quote(LONG_QUOTE), normalize_quote(LONG_PAGE)) >= 0.9
    check = verify_quote([LONG_QUOTE], LONG_PAGE, 0.9)
    assert not check.verified  # 175 psi is not 125 psi


def test_letter_code_must_be_exact() -> None:
    assert not verify_quote(["A 216 Gr. WCC"], "Body A 216 Gr. WCB", 0.9).verified


def test_word_noise_is_still_forgiven() -> None:
    check = verify_quote(["Butterfiy valves, Class 150"], "Butterfly valves, Class 150", 0.9)
    assert check.verified  # OCR "fi" for "fl" in a word; the number is exact
    assert check.score < 1.0


def test_label_printed_inside_a_wrapped_cell_still_verifies() -> None:
    # Aquaseal p8 reading order: the row label "Disc" lands between the cell's lines.
    page = (
        "SG Iron to EN 1563-400/\nDisc\n18 with Nylon-coating,\n"
        "CF8, CF8M to A 351,\nAB2 to IS EN 1982, CF3M\nStem"
    )
    cell = (
        "SG Iron to EN 1563-400/ 18 with Nylon-coating, CF8, CF8M to A 351, AB2 to IS EN 1982, CF3M"
    )
    assert verify_quote(["Disc", cell], page, 0.9).verified
