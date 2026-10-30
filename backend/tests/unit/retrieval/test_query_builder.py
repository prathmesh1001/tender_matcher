"""Query builder: typo correction (A2), abbreviations (A5), synonym expansion, stemming (A1)."""

from dataclasses import replace

import pytest

from app.retrieval.query_builder import QueryVocabulary, build_query, edit_distance


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [
        ("valve", "valve", 0),
        ("vlave", "valve", 1),  # neighbour swap counts once
        ("buterfly", "butterfly", 1),
        ("globle", "globe", 1),
        ("chek", "check", 1),
    ],
)
def test_edit_distance(a: str, b: str, distance: int) -> None:
    assert edit_distance(a, b, limit=3) == distance


def test_edit_distance_is_capped_above_the_limit() -> None:
    assert edit_distance("centrifugal", "gate", limit=2) == 3


@pytest.mark.parametrize(
    ("typo", "fixed"),
    [("buterfly valve", "butterfly"), ("gate vlave", "valve"), ("globle valve", "globe")],
)
def test_typos_are_corrected_to_ontology_words(
    query_vocab: QueryVocabulary, typo: str, fixed: str
) -> None:
    query = build_query(typo, query_vocab)
    assert [c.corrected for c in query.corrections] == [fixed]
    assert (
        query.terms
        == build_query(typo.replace(query.corrections[0].original, fixed), query_vocab).terms
    )
    assert fixed in query.dense_text  # the embedder and reranker see the fix too


def test_words_of_the_searched_chunks_are_never_corrected(query_vocab: QueryVocabulary) -> None:
    # "water" is one edit from the ontology word "wafer", but catalogues say "water".
    assert build_query("water service", query_vocab, known_terms={"water"}).corrections == ()


def test_short_words_codes_and_known_words_are_left_alone(query_vocab: QueryVocabulary) -> None:
    query = build_query("chek valves SS316 PN16", query_vocab)
    assert query.corrections == ()  # "chek" is shorter than 5 letters
    assert query.terms == ("chek", "valv", "ss316", "pn16")


def test_correction_can_be_switched_off(query_vocab: QueryVocabulary) -> None:
    off = replace(query_vocab, max_distance=0)
    assert build_query("buterfly valve", off).corrections == ()


def test_ambiguous_typos_are_not_corrected(query_vocab: QueryVocabulary) -> None:
    # "wafter" is one deletion from both words.
    tied = replace(query_vocab, typo_words=("wafer", "water"), typo_stems=frozenset())
    assert build_query("wafter", tied).corrections == ()


def test_abbreviations_expand(query_vocab: QueryVocabulary) -> None:
    query = build_query("NRV, PN 16, flanged", query_vocab)
    assert query.terms == ("nrv", "pn16", "flang")
    assert {"check", "valv"} <= set(query.expansion_terms)
    assert query.dense_text.endswith("check valve")


def test_named_attribute_and_product_type_add_their_synonyms(query_vocab: QueryVocabulary) -> None:
    query = build_query("Ball Valves size 50 mm", query_vocab)
    assert {"nomin", "bore", "dn", "nps"} <= set(query.expansion_terms)


def test_given_attribute_adds_its_synonyms(query_vocab: QueryVocabulary) -> None:
    query = build_query("200 mm", query_vocab, attribute="nominal_size")
    assert {"size", "nomin", "dn"} <= set(query.expansion_terms)


def test_expansion_never_repeats_query_terms(query_vocab: QueryVocabulary) -> None:
    query = build_query("butterfly valve", query_vocab)
    assert not set(query.terms) & set(query.expansion_terms)
    assert len(query.expansion_terms) == len(set(query.expansion_terms))


def test_plural_and_singular_build_the_same_terms(query_vocab: QueryVocabulary) -> None:
    assert (
        build_query("balls valve", query_vocab).terms
        == build_query("Ball Valves", query_vocab).terms
    )


def test_rerank_text_leads_with_the_attribute_name(query_vocab: QueryVocabulary) -> None:
    query = build_query("Body- CI", query_vocab, attribute="body_material")
    assert query.rerank_text == f"body material: {query.dense_text}"
    assert query.dense_text.startswith("Body- CI")  # the embedder input is unchanged


def test_rerank_text_without_attribute_is_the_dense_text(query_vocab: QueryVocabulary) -> None:
    query = build_query("NRV PN16", query_vocab)
    assert query.rerank_text == query.dense_text


def test_material_names_expand_to_grade_codes(query_vocab: QueryVocabulary) -> None:
    # Catalogues often give only the grade: "Body: ASTM A126 Gr B" is cast iron (CI).
    body = build_query("Body- CI", query_vocab, attribute="body_material")
    assert {"cast", "iron", "a126", "is210", "en1561"} <= set(body.expansion_terms)
    assert "A126" in body.rerank_text
    disc = build_query("Disc- SGI", query_vocab)
    assert {"ductil", "a536", "a395", "en1563"} <= set(disc.expansion_terms)
    assert "cast" not in disc.expansion_terms  # SG iron never pulls in grey cast iron


def test_dense_text_has_no_repeated_expansions(query_vocab: QueryVocabulary) -> None:
    query = build_query("Body- CI", query_vocab)
    assert "cast iron cast iron" not in query.dense_text  # abbreviation and equivalent agree


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("DESIGN/MFG STD- EN593", ("en593",)),
        ("CLASS - PN10", ("pn10",)),
        ("Valve Size - 450MM", ()),  # bare numbers are never codes (B4)
        ("Seat- EPDM", ()),
    ],
)
def test_code_terms(query_vocab: QueryVocabulary, text: str, codes: tuple[str, ...]) -> None:
    assert build_query(text, query_vocab).code_terms == codes


def test_code_terms_include_equivalent_grade_codes(query_vocab: QueryVocabulary) -> None:
    codes = build_query("Disc- SGI", query_vocab).code_terms
    assert {"a536", "a395", "en1563"} <= set(codes)
    assert "sgi" not in codes  # a word, not a code
