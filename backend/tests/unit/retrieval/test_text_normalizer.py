from pathlib import Path

import pytest

from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary, fold_text, normalize_for_search

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
FL = "\N{LATIN SMALL LIGATURE FL}"
LIGATURE_LINE = (
    "Butter" + FL + "y\N{NO-BREAK SPACE}valve 3\N{RIGHT DOUBLE QUOTATION MARK} \N{EN DASH} PN16"
)


@pytest.fixture(scope="module")
def vocab() -> SearchVocabulary:
    return SearchVocabulary.from_ontologies(load_ontologies(ONTOLOGY_DIR))


def test_plural_and_inflection_match(vocab: SearchVocabulary) -> None:
    # A1: "balls valve" and "Ball Valves" must share every token.
    assert normalize_for_search("balls valve", vocab) == normalize_for_search("Ball Valves", vocab)


def test_ligatures_quotes_dashes_and_nbsp_are_folded(vocab: SearchVocabulary) -> None:
    # A3: the "fl" ligature seen in a real catalogue, curly inch mark, en dash, NBSP.
    assert normalize_for_search(LIGATURE_LINE, vocab) == (
        normalize_for_search('butterfly valve 3" - PN16', vocab)
    )
    assert fold_text("m\N{SUPERSCRIPT THREE}/h") == "m3/h"


@pytest.mark.parametrize(
    ("variants", "canonical"),
    [
        (["SS316", "SS 316", "SS-316", "316 SS", "AISI 316"], "ss316"),
        (["SS316L", "SS 316 L", "316L SS"], "ss316l"),
        (["17-4-PH", "17-4PH", "17 4 PH"], "17_4ph"),
        (["A 216 Gr. WCB", "A216 WCB", "ASTM A-216 Grade WCB", "WCB"], "a216_wcb"),
        (["CF8M", "A351 CF8M", "CF-8M"], "a351_cf8m"),
        (["PN 16", "PN-16", "PN16"], "pn16"),
        (["Class 150", "150#", "150 lbs", "CL. 150"], "class150"),
        (["DN 80", "80 NB", "NB 80"], "dn80"),
        (["API 600", "API-600"], "api600"),
    ],
)
def test_code_variants_have_one_form(
    vocab: SearchVocabulary, variants: list[str], canonical: str
) -> None:
    # A4 / A5 sizes and ratings.
    assert {normalize_for_search(v, vocab) for v in variants} == {canonical}


def test_longer_code_wins_over_its_prefix(vocab: SearchVocabulary) -> None:
    assert normalize_for_search("SS316L and SS316", vocab) == "ss316l and ss316"


def test_hyphenated_compounds_split(vocab: SearchVocabulary) -> None:
    # A6: "ball-valve" tokenizes like "ball valve".
    assert normalize_for_search("ball-valve", vocab) == normalize_for_search("ball valve", vocab)


def test_hindi_is_dropped_english_kept(vocab: SearchVocabulary) -> None:
    # A8: index the English part only.
    assert normalize_for_search("गेट वाल्व Gate Valve", vocab) == normalize_for_search(
        "gate valve", vocab
    )


def test_decimals_stay_whole_and_codes_are_not_stemmed(vocab: SearchVocabulary) -> None:
    assert normalize_for_search("Power 7.5 kW, SS316 bodies", vocab) == "power 7.5 kw ss316 bodi"


def test_empty_and_non_latin_text(vocab: SearchVocabulary) -> None:
    assert normalize_for_search("", vocab) == ""
    assert normalize_for_search("गेट", vocab) == ""


def test_variant_set_shares_the_same_normal_form(vocab: SearchVocabulary) -> None:
    # Section 12 eval rule in miniature: plural, unicode and spacing variants of one query.
    queries = [
        "Butterfly Valves SS-316",
        "butter" + FL + "y valve SS 316",
        "BUTTERFLY VALVE 316 SS",
    ]
    assert len({normalize_for_search(q, vocab) for q in queries}) == 1


def test_garbled_hindi_words_do_not_leak_latin_letters(vocab: SearchVocabulary) -> None:
    # GeM bid PDFs extract Hindi as broken glyphs mixed with Latin letters and U+FFFD.
    raw = "सूZम और लघु 3दाता को | \N{REPLACEMENT CHARACTER}ववरण/Specification: Power"
    assert normalize_for_search(raw, vocab) == "specif power"


@pytest.mark.parametrize("variant", ["EN593", "EN 593", "EN-593", "BS EN 593", "BS-EN 593"])
def test_en_standard_variants_share_one_code(vocab: SearchVocabulary, variant: str) -> None:
    # "BS EN 593" is EN 593 adopted by BSI; a tender writing "EN593" must match it.
    assert normalize_for_search(variant, vocab) == "en593"


def test_plain_bs_standard_stays_bs(vocab: SearchVocabulary) -> None:
    assert normalize_for_search("BS 5155", vocab) == "bs5155"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Valve Size - 450MM", "valv size 450 mm"),
        ("7.5kW 1450RPM", "7.5 kw 1450 rpm"),
        ("10bar", "10 bar"),
        ("2in", "2 in"),
    ],
)
def test_number_glued_to_a_unit_is_split(vocab: SearchVocabulary, text: str, expected: str) -> None:
    # "450MM" must match a catalogue "Valve Size: 450" / "450 mm".
    assert normalize_for_search(text, vocab) == expected


@pytest.mark.parametrize(("text", "kept"), [("304L", "304l"), ("GG25", "gg25"), ("PN16", "pn16")])
def test_codes_are_not_split_as_units(vocab: SearchVocabulary, text: str, kept: str) -> None:
    assert normalize_for_search(text, vocab) == kept


def test_is_code() -> None:
    from app.retrieval.text_normalizer import is_code

    assert all(is_code(t) for t in ["en593", "pn10", "a216_wcb", "17_4ph"])
    assert not any(is_code(t) for t in ["450", "7.5", "epdm", "valv"])


def test_unit_spellings_keep_symbols_and_slashes(vocab: SearchVocabulary) -> None:
    assert {"bar", "m3/h", "°c", "kg/cm2"} <= vocab.unit_spellings


def test_stemming_is_safe_from_several_threads() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from app.retrieval.text_normalizer import stem_token

    words = ["valves", "thermocouples", "connections", "immersion", "ratings", "flanged"] * 400
    expected = [stem_token(w) for w in words]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: [stem_token(w) for w in words], range(8)))
    assert all(r == expected for r in results)
