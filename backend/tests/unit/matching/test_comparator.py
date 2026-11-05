"""Rule comparator. Catalogue rows and tender lines are synthetic, shaped like the real ones."""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.matching.chunk_source import CatalogueChunk, CatalogueDocument
from app.matching.comparator import ComparatorOptions, MatchContext, compare
from app.matching.fields import FieldOptions, chunk_fields, is_dimension_table, located_fields
from app.matching.values import quantities
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DocType, MatchStatus
from app.schemas.matching import Comparison
from app.schemas.requirements import LineItem, Requirement
from app.tender.splitter import SplitOptions, item_requirements, leaves

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"
OPTIONS = ComparatorOptions.from_settings(
    Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[arg-type]
)  # config defaults
SPLIT = SplitOptions(label_max_words=6, enum_max_words=2)
DOC = CatalogueDocument(
    manufacturer="Acme",
    category="valves_pumps",
    title="Synthetic butterfly valve catalogue",
    doc_type=DocType.CATALOGUE,
    file="synthetic.pdf",
    sha256="a" * 64,
    product_types=("butterfly_valve",),
)
FULL, PARTIAL = MatchStatus.FULL_MATCH, MatchStatus.PARTIAL_MATCH
MISSING, UNSUPPORTED = MatchStatus.MISSING_CAPABILITY, MatchStatus.UNSUPPORTED
BOTH = pytest.mark.parametrize("with_ontology", [True, False], ids=["ontology", "no-ontology"])


@pytest.fixture(scope="module")
def valves() -> Ontology:
    [ontology] = load_ontologies(ONTOLOGY_DIR)
    return ontology


def _ontology(valves: Ontology, with_ontology: bool) -> Ontology | None:
    return valves if with_ontology else None


def _req(line: str, ontology: Ontology | None) -> Requirement:
    item = LineItem(
        item_no="1",
        title="BUTTERFLY VALVE",
        description_lines=[line],
        document="tender.pdf",
        page_number=1,
    )
    [requirement] = leaves(item_requirements(item, ontology, SPLIT))
    return requirement


def _row(header: list[str], cells: list[str], section: str = "Materials") -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=4,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=table_row_text(header, cells, section),
        section=section,
        token_count=1,
        meta={"header": header, "cells": cells},
    )
    return CatalogueChunk(DOC, chunk)


def _prose(text: str) -> CatalogueChunk:
    chunk = ChunkDraft(
        page_number=2, position=0, chunk_type=ChunkType.PROSE, raw_text=text, token_count=1
    )
    return CatalogueChunk(DOC, chunk)


def _compare(line: str, chunks: Sequence[CatalogueChunk], ontology: Ontology | None) -> Comparison:
    fields = located_fields(chunks, OPTIONS.fields)
    return compare(_req(line, ontology), fields, MatchContext.for_ontology(ontology), OPTIONS)


PARTS = ["No.", "Components", "Materials"]
DISC_WCB = _row(PARTS, ["2", "Disc", "ASTM A216 Gr. WCB"])
SIZE_RANGE = _prose("Size range: 80 mm to 2500 mm")


# ---------------------------------------------------------------- like-for-like labels


@BOTH
def test_code_in_another_component_row_is_not_evidence(
    valves: Ontology, with_ontology: bool
) -> None:
    result = _compare("BODY MOC : ASTM A216 Gr. WCB", [DISC_WCB], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED
    assert result.needs_review
    assert result.evidence == []


@BOTH
def test_same_component_row_decides(valves: Ontology, with_ontology: bool) -> None:
    body = _row(PARTS, ["1", "Body", "ASTM A216 Gr. WCB"])
    result = _compare(
        "BODY MOC : ASTM A216 Gr. WCB", [DISC_WCB, body], _ontology(valves, with_ontology)
    )
    assert result.status is FULL
    [evidence] = result.evidence
    assert evidence.label == "Body Materials"
    assert evidence.quote == "Components: Body; Materials: ASTM A216 Gr. WCB"
    assert evidence.quote in body.chunk.raw_text


@BOTH
def test_same_component_with_other_material_is_missing(
    valves: Ontology, with_ontology: bool
) -> None:
    body = _row(PARTS, ["1", "Body", "ASTM A351 Gr. CF8M"])
    result = _compare(
        "BODY MOC : ASTM A216 Gr. WCB", [DISC_WCB, body], _ontology(valves, with_ontology)
    )
    assert result.status is MISSING


def test_version_chunk_uses_only_its_own_column(valves: Ontology) -> None:
    header = ["Components", "Materials WCB", "Materials CF8M"]
    cells = ["Body", "A 216 Gr. WCB", "A 351 Gr. CF8M"]
    chunk = _row(header, cells)
    shown_only_cf8m = chunk.chunk.model_copy(
        update={"raw_text": table_row_text([header[0], header[2]], [cells[0], cells[2]], None)}
    )
    [key, value] = chunk_fields(shown_only_cf8m, OPTIONS.fields)
    assert (key.value, value.value) == ("Body", "A 351 Gr. CF8M")


# ---------------------------------------------------------------- sizes and ranges


DIMENSIONS = _row(["Size", "A", "B", "Weight (kg)"], ["250", "330", "457", "120"], "Dimensions")


@BOTH
def test_size_only_in_dimension_table_is_unsupported(valves: Ontology, with_ontology: bool) -> None:
    result = _compare("SIZE : 250 MM", [DIMENSIONS], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED
    assert result.needs_review


def test_dimension_table_detection() -> None:
    options = OPTIONS.fields
    assert is_dimension_table(["Size", "A", "B", "C"], None, options)  # drawing callouts
    assert is_dimension_table(["Size", "Weight (kg)"], None, options)
    assert is_dimension_table(["Size", "L"], "Construction and Dimensions", options)
    assert not is_dimension_table(["Size range", "Pressure rating"], "Technical data", options)


@BOTH
@pytest.mark.parametrize(
    ("line", "status"),
    [
        ("SIZE : 250 MM", FULL),
        ("SIZE : 3000 MM", MISSING),
        ("SIZE : 80 MM", FULL),  # range ends are inclusive
        ("SIZE : 2500 MM", FULL),
    ],
)
def test_size_against_labelled_range(
    valves: Ontology, with_ontology: bool, line: str, status: MatchStatus
) -> None:
    result = _compare(line, [DIMENSIONS, SIZE_RANGE], _ontology(valves, with_ontology))
    assert result.status is status
    assert [e.quote for e in result.evidence] == ["Size range: 80 mm to 2500 mm"]


@BOTH
def test_unlabelled_line_counts_when_label_leads(valves: Ontology, with_ontology: bool) -> None:
    lines = _prose("Butterfly valves for water\nSize range 80 mm to 2500 mm")
    result = _compare("SIZE : 250 MM", [lines], _ontology(valves, with_ontology))
    assert result.status is FULL


@BOTH
@pytest.mark.parametrize(
    "condition",
    [
        'For sizes 6" and above, lifting eyebolt option',  # L&T note: was a false full match
        "If size 300 mm or more, gear operator is supplied",
        "When size exceeds 600 mm, consult factory",
        "where size 150 mm, bypass is optional",
    ],
)
def test_condition_line_never_decides(
    valves: Ontology, with_ontology: bool, condition: str
) -> None:
    lines = _prose(f"Butterfly valves for water\n{condition}")
    result = _compare('SIZE : 8"', [lines], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED
    assert result.evidence == []


@pytest.mark.parametrize(
    ("line", "status"),
    [
        ('SIZE : 3"', FULL),  # inside: was "missing" when "thru" read as a list of 2" and 12"
        ('SIZE : 2"', FULL),
        ('SIZE : 12"', FULL),
        ('SIZE : 1"', MISSING),
        ('SIZE : 14"', MISSING),
    ],
)
def test_thru_is_a_range(valves: Ontology, line: str, status: MatchStatus) -> None:
    mueller = _prose("Sizes: 2” thru 12” / DN 50 thru DN 300\n2.2 Working pressure")
    assert _compare(line, [mueller], valves).status is status


@pytest.mark.parametrize(
    ("text", "low", "high", "unit"),
    [
        ("50 thru 300 mm", 50.0, 300.0, "mm"),  # "thru" is never the unit of 50
        ("2 through 8 inch", 2.0, 8.0, "inch"),
        ("DN 50 thru DN 300", 50.0, 300.0, "dn"),
    ],
)
def test_thru_and_through_join_a_range(text: str, low: float, high: float, unit: str) -> None:
    [found] = quantities(text, ("pn", "class", "dn"))
    assert (found.low, found.high, found.unit) == (low, high, unit)


def test_inch_size_against_dn_range(valves: Ontology) -> None:
    dn_range = _prose("Size range: DN 50 to DN 600")
    assert _compare('SIZE : 2"', [dn_range], valves).status is FULL  # NPS 2 = DN 50 (edge)
    assert _compare('SIZE : 1"', [dn_range], valves).status is MISSING  # DN 25
    no_units = _compare('SIZE : 2"', [dn_range], None)
    assert no_units.status is UNSUPPORTED  # inch vs DN needs the ontology's size table


@pytest.mark.parametrize(("line", "status"), [
    ("WORKING PRESSURE : 150 PSI", FULL),  # 10.3 bar, rating 16 bar
    ("WORKING PRESSURE : 300 PSI", MISSING),  # 20.7 bar
])  # fmt: skip
def test_psi_against_bar_rating(valves: Ontology, line: str, status: MatchStatus) -> None:
    rating = _prose("Working pressure: 16 bar")
    assert _compare(line, [rating], valves).status is status


def test_psi_against_bar_without_ontology_is_unsupported() -> None:
    rating = _prose("Working pressure: 16 bar")
    result = _compare("WORKING PRESSURE : 150 PSI", [rating], None)
    assert (result.status, result.needs_review) == (UNSUPPORTED, True)


# ---------------------------------------------------------------- designations


@BOTH
@pytest.mark.parametrize(
    ("printed", "status"), [("PN 10", MISSING), ("PN 16", FULL), ("PN 10/16/25", FULL)]
)
def test_pn_or_higher(
    valves: Ontology, with_ontology: bool, printed: str, status: MatchStatus
) -> None:
    rating = _row(["Body Style", "Pressure Rating"], ["Wafer", printed], "Aquaseal")
    result = _compare(
        "PRESSURE RATING : PN 16 OR HIGHER", [rating], _ontology(valves, with_ontology)
    )
    assert result.status is status


def test_class_against_pn_only_through_the_table(valves: Ontology) -> None:
    rating = _prose("Pressure rating: PN 20")
    assert _compare("PRESSURE RATING : CLASS #150", [rating], valves).status is FULL  # EN 1759
    assert _compare("PRESSURE RATING : CLASS #150", [rating], None).status is UNSUPPORTED


def test_class_range(valves: Ontology) -> None:
    rating = _prose("Pressure rating: ASME Class 150 to 1500")
    assert _compare("PRESSURE RATING : CLASS #300", [rating], valves).status is FULL


# ---------------------------------------------------------------- no value, options, text


@BOTH
@pytest.mark.parametrize(
    ("line", "catalogue"),
    [
        ("SIZE : 250 MM", "Size range: on request"),
        ("SIZE : 250 MM", "Size range:"),
        ("BODY MOC : ASTM A216 Gr. WCB", "Body:"),
    ],
)
def test_label_without_value_is_never_missing(
    valves: Ontology, with_ontology: bool, line: str, catalogue: str
) -> None:
    result = _compare(line, [_prose(catalogue)], _ontology(valves, with_ontology))
    assert (result.status, result.needs_review) == (UNSUPPORTED, True)


@BOTH
def test_single_value_without_direction_is_not_missing(
    valves: Ontology, with_ontology: bool
) -> None:
    # "=" against one printed value: missing only when the ontology says the value is exact.
    result = _compare("SIZE : 250 MM", [_prose("Size: 300 mm")], _ontology(valves, with_ontology))
    assert result.status is (MISSING if with_ontology else UNSUPPORTED)


@BOTH
def test_one_option_is_full_when_the_tender_writes_a_slash(
    valves: Ontology, with_ontology: bool
) -> None:
    # "/" means either option is acceptable, the same as "or" (decision of 2026-11-02).
    disc = _row(PARTS, ["2", "Disc", "ASTM A351 Gr. CF8M"])
    result = _compare(
        "DISC MOC : ASTM A217 Gr. CA15/CF8M", [disc], _ontology(valves, with_ontology)
    )
    assert (result.status, result.needs_review) == (FULL, False)


@BOTH
def test_no_option_of_a_slash_found_is_not_full(valves: Ontology, with_ontology: bool) -> None:
    disc = _row(PARTS, ["2", "Disc", "ASTM A351 Gr. CF3M"])
    result = _compare(
        "DISC MOC : ASTM A217 Gr. CA15/CF8M", [disc], _ontology(valves, with_ontology)
    )
    assert result.status is not FULL


@BOTH
def test_both_options_found_is_full(valves: Ontology, with_ontology: bool) -> None:
    disc = _row(PARTS, ["2", "Disc", "A 217 Gr. CA15 / A 351 Gr. CF8M"])
    result = _compare(
        "DISC MOC : ASTM A217 Gr. CA15/CF8M", [disc], _ontology(valves, with_ontology)
    )
    assert result.status is FULL


@BOTH
def test_one_option_is_full_when_the_tender_says_or(valves: Ontology, with_ontology: bool) -> None:
    disc = _row(PARTS, ["2", "Disc", "ASTM A351 Gr. CF8M"])
    result = _compare(
        "DISC MOC : ASTM A217 Gr. CA15 or CF8M", [disc], _ontology(valves, with_ontology)
    )
    assert (result.status, result.needs_review) == (FULL, False)


@BOTH
def test_no_option_found_is_not_full_when_the_tender_says_or(
    valves: Ontology, with_ontology: bool
) -> None:
    disc = _row(PARTS, ["2", "Disc", "ASTM A105"])
    result = _compare(
        "DISC MOC : ASTM A217 Gr. CA15 or CF8M", [disc], _ontology(valves, with_ontology)
    )
    assert result.status is not FULL


def test_bolted_bonnet_never_fails_a_body_row(valves: Ontology) -> None:
    body = _row(PARTS, ["1", "Body & Bonnet", "Carbon Steel"])
    result = _compare("Body / Bonnet Construction : Bolted Bonnet", [body], valves)
    assert result.status is UNSUPPORTED  # not missing: the row states no bonnet type


def test_row_key_decides_when_the_column_is_a_model_or_heading(valves: Ontology) -> None:
    row = _row(["Component", "Etanorm"], ["Body", "A 216 Gr. WCB"])
    assert _compare("BODY MOC : ASTM A216 Gr. WCB", [row], valves).status is FULL


def test_a_part_of_the_component_is_not_the_component(valves: Ontology) -> None:
    nut = _row(PARTS, ["7", "Disc Nut", "ASTM A351 Gr. CF8M"])
    assert _compare("DISC MOC : CF8M", [nut], valves).status is UNSUPPORTED


def test_free_text_and_statements_need_the_judge(valves: Ontology) -> None:
    for line in ("OPERATION : HAND GEAR OPERATED", "THE VALVE SHALL BE FIRE SAFE"):
        result = _compare(line, [SIZE_RANGE], valves)
        assert (result.status, result.needs_review) == (UNSUPPORTED, True)


def test_prose_label_group_continues_on_wrapped_lines() -> None:
    chunk = _prose("Body: ASTM A126 Gr B\nASTM A216 Gr WCB\n•\nDisc: ASTM A351 Gr. CF8M")
    labelled = [f for f in chunk_fields(chunk.chunk, FieldOptions(6, (), 2)) if f.label]
    assert [(f.label, f.value) for f in labelled] == [
        ("Body", "ASTM A126 Gr B ASTM A216 Gr WCB"),
        ("Disc", "ASTM A351 Gr. CF8M"),
    ]
    assert labelled[0].quote == "Body: ASTM A126 Gr B\nASTM A216 Gr WCB"


# ---------------------------------------------------------------- found on the real catalogues


@BOTH
def test_sentence_after_a_label_word_is_not_a_value(valves: Ontology, with_ontology: bool) -> None:
    # Shape of a DelVal line: "Ends" opens a sentence, it does not state the end connection.
    sentence = _prose("Ends shall be machined and internal surfaces coated")
    result = _compare("END CONNECTION : WAFER", [sentence], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED


@BOTH
def test_long_value_without_codes_is_never_missing(valves: Ontology, with_ontology: bool) -> None:
    body = _prose("Body: shall be designed to give smooth flow with low pressure drop")
    result = _compare("BODY MOC : ASTM A216 Gr. WCB", [body], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED


@BOTH
def test_bare_number_is_not_a_stated_code(valves: Ontology, with_ontology: bool) -> None:
    shaft_no = _row(PARTS, ["5", "Shaft", ""])  # only the "No." cell holds a value
    result = _compare(
        "SHAFT MOC : ASTM A479 Type SS 410", [shaft_no], _ontology(valves, with_ontology)
    )
    assert result.status is UNSUPPORTED


def test_number_before_the_label_is_not_a_labelled_line(valves: Ontology) -> None:
    line = _prose('300) 175 psi (12.0 barg) 14"-24"(DN 350 - DN 600) 150 psi')
    assert _compare("SIZE : 400 MM", [line], valves).status is UNSUPPORTED


def test_up_to_is_an_open_range(valves: Ontology) -> None:
    line = _prose('Size: up to 100" (2500 mm)')
    inside = _compare("SIZE : 250 MM", [line], valves)
    assert (inside.status, inside.needs_review) == (PARTIAL, True)  # smallest size not printed
    assert _compare("SIZE : 3000 MM", [line], valves).status is MISSING


@BOTH
def test_hyphenated_compound_label_is_one_word(valves: Ontology, with_ontology: bool) -> None:
    dead_end = _prose("Dead-End Service: 2-12 (DN 50 - DN 300) 175 psi")
    result = _compare("END CONNECTION : WAFER", [dead_end], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED


@BOTH
def test_words_are_not_compared_with_material_codes(valves: Ontology, with_ontology: bool) -> None:
    # "SINGLE PIECE" printed under the shaft MOC line vs the shaft material cell.
    shaft = _row(PARTS, ["5", "Shaft", "A 479 Type SS 410/ A 564 Type 630"])
    result = _compare("SHAFT MOC : SINGLE PIECE", [shaft], _ontology(valves, with_ontology))
    assert result.status is UNSUPPORTED


@BOTH
def test_words_found_among_codes_still_match(valves: Ontology, with_ontology: bool) -> None:
    ends = _row(["Parameter", "Standard"], ["End Connection", "Wafer Flangeless, Lugged (EN 1092)"])
    pressure = _row(["Parameter", "Standard"], ["End Connection", "Class 125"])
    result = _compare("END CONNECTION : WAFER", [pressure, ends], _ontology(valves, with_ontology))
    assert result.status is FULL
    alone = _compare("END CONNECTION : WAFER", [pressure], _ontology(valves, with_ontology))
    assert alone.status is UNSUPPORTED  # "Class 125" is not an end-connection option


@BOTH
def test_designation_label_stays_with_its_number(valves: Ontology, with_ontology: bool) -> None:
    # L&T TOBV cover line shape: "Class" is both the label and the designation.
    cover = _prose("ASME Class 150 to 1500")
    result = _compare("PRESSURE RATING : CLASS #150", [cover], _ontology(valves, with_ontology))
    assert result.status is (FULL if with_ontology else UNSUPPORTED)  # no "pressure rating" words
    higher = _compare("RATING : CLASS #300", [cover], _ontology(valves, with_ontology))
    assert higher.status is (FULL if with_ontology else UNSUPPORTED)


def test_inline_evidence_value_is_verbatim(valves: Ontology) -> None:
    cover = _prose("ASME Class 150 to 1500 | 3” to 100”")
    [evidence] = _compare("PRESSURE RATING : CLASS #150", [cover], valves).evidence
    assert evidence.value == "Class 150 to 1500 | 3” to 100”"
    assert evidence.value in cover.chunk.raw_text


@pytest.mark.parametrize(
    ("printed", "status"),
    [
        ('3/8" 1/2"', MISSING),  # small bore: never 8 inch or 2 inch
        ('1-1/2"', MISSING),
        ('8"', FULL),
        ('2" to 12"', FULL),
    ],
)
def test_inch_fractions_are_not_whole_sizes(
    printed: str, status: MatchStatus, valves: Ontology
) -> None:
    result = _compare("SIZE : 8 INCH", [_prose(f"Size: {printed}")], valves)
    assert result.status is status


def test_fraction_size_matches_itself(valves: Ontology) -> None:
    assert _compare('SIZE : 3/4"', [_prose('Size: 1/2" to 2"')], valves).status is FULL


def test_or_equivelent_typo_does_not_block_a_match(valves: Ontology) -> None:
    body = _row(PARTS, ["1", "Body", "A 216 Gr. WCB"])
    assert _compare("BODY MOC : ASTM A216 Grade WCB or equivelent", [body], valves).status is FULL


@pytest.mark.parametrize(
    ("printed", "status"),
    [
        ("-46\u00b0C to 343\u00b0C", FULL),
        ("0\u00b0C to 343\u00b0C", PARTIAL),  # only part of the range
        ("100\u00b0C to 343\u00b0C", MISSING),
    ],
)
def test_temperature_range_is_compared_with_a_range(
    printed: str, status: MatchStatus, valves: Ontology
) -> None:
    field = _prose(f"Temperature: {printed}")
    assert _compare("Design Temp. : -40 Deg. C to +40 Deg. C", [field], valves).status is status


def test_same_material_named_another_way_is_partial(valves: Ontology) -> None:
    row = _row(["Part", "Material"], ["Stem", "Stainless Steel"])
    result = _compare("STEM MOC : SS ASTM A276", [row], valves)
    assert result.status is MatchStatus.PARTIAL_MATCH
    assert result.needs_review is True


def test_another_material_is_still_missing(valves: Ontology) -> None:
    row = _row(["Part", "Material"], ["Stem", "Cast Iron"])
    result = _compare("STEM MOC : SS ASTM A276", [row], valves)
    assert result.status is MatchStatus.MISSING_CAPABILITY


def test_tender_naming_only_the_material_is_met_by_its_grade_code(valves: Ontology) -> None:
    row = _row(["Part", "Material"], ["Body", "Cast Iron EN 1561 Gr. 200"])
    assert _compare("BODY MOC : CI", [row], valves).status is MatchStatus.FULL_MATCH


def test_tender_naming_only_the_material_still_fails_on_another(valves: Ontology) -> None:
    row = _row(["Part", "Material"], ["Body", "Carbon Steel"])
    assert _compare("BODY MOC : CI", [row], valves).status is MatchStatus.MISSING_CAPABILITY


# ---------------------------------------------------------------- without material equivalents


@pytest.mark.parametrize(
    ("line", "value"),
    [
        ("BODY MOC : CI", "ASTM A216 Gr. WCB"),
        ("DISC MOC : SGI", "ASTM A536"),
        ("BODY MOC : CI", "Wafer"),
        ("BODY MOC : ASTM A126", "Cast Iron"),
    ],
)
def test_without_equivalents_a_word_never_fails(line: str, value: str) -> None:
    component = line.split()[0].title()
    result = _compare(line, [_row(["Part", "Material"], [component, value])], None)
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.needs_review is True


def test_without_equivalents_a_code_still_fails_against_a_code() -> None:
    row = _row(["Part", "Material"], ["Body", "ASTM A351 Gr. CF8M"])
    assert _compare("BODY MOC : ASTM A216", [row], None).status is MatchStatus.MISSING_CAPABILITY


# ---------------------------------------------------------------- impossible values

TEMPERATURE = "DESIGN TEMP. : -40 Deg. C to +40 Deg. C"


def test_a_temperature_below_absolute_zero_never_fails(valves: Ontology) -> None:
    result = _compare(TEMPERATURE, [_prose("Temperature: -460C")], valves)
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.needs_review is True
    assert "misread" in result.reason


def test_a_possible_temperature_still_fails(valves: Ontology) -> None:
    result = _compare(TEMPERATURE, [_prose("Temperature: 101C to 203C")], valves)
    assert result.status is MatchStatus.MISSING_CAPABILITY


def test_absolute_zero_itself_is_readable(valves: Ontology) -> None:
    result = _compare(TEMPERATURE, [_prose("Temperature: -273C to 103C")], valves)
    assert result.status is MatchStatus.FULL_MATCH
    assert result.needs_review is False


def test_a_readable_value_still_decides_and_the_misread_flags_it(valves: Ontology) -> None:
    chunks = [_prose("Temperature: -460C"), _prose("Temperature: -46C to 343C")]
    result = _compare(TEMPERATURE, chunks, valves)
    assert result.status is MatchStatus.FULL_MATCH
    assert result.needs_review is True
    assert "misread" in result.reason
    assert [e.value for e in result.evidence] == ["-46C to 343C"]  # ignored as evidence


def test_the_rule_is_only_for_attributes_with_a_limit(valves: Ontology) -> None:
    result = _compare("SIZE : 100 MM", [_prose("Size: -460 mm")], valves)
    assert "misread" not in result.reason


@pytest.mark.parametrize(
    "value", ["up to 4250C", "-460C to 3430C", "-290C to 6490C", "5400C", "4250C (800F)"]
)
def test_a_zero_glued_to_c_can_decide_nothing(value: str, valves: Ontology) -> None:
    line = "DESIGN TEMP. : 300 Deg. C"
    result = _compare(line, [_prose(f"Temperature: {value}")], valves)
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.needs_review is True
    assert "misread degree" in result.reason


@pytest.mark.parametrize("value", ["425 C", "425C", "-46C to 343C", "400 C", "400 °C"])
def test_other_temperatures_are_read(value: str, valves: Ontology) -> None:
    line = "DESIGN TEMP. : 300 Deg. C"
    result = _compare(line, [_prose(f"Temperature: {value}")], valves)
    assert "misread" not in result.reason
    assert result.status is MatchStatus.FULL_MATCH


def test_the_glued_zero_rule_is_for_temperatures_only(valves: Ontology) -> None:
    result = _compare("SIZE : 100 MM", [_prose("Size: 50 to 200C")], valves)
    assert "misread" not in result.reason


# ---------------------------------------------------------------- standards named by code

@pytest.mark.parametrize(
    ("line", "text"),
    [
        ("Product Standard : EN 593", "Butterfly valves that conform to EN 593, sizes 50 to 600"),
        ("Testing Standard : API 598", "Each valve is shell and seat tested to API 598."),
        ("Design Standard : BS EN 593", "ISO 5208 / EN 593"),  # no label on the line
    ],
)
def test_a_standard_code_names_its_attribute(valves: Ontology, line: str, text: str) -> None:
    result = _compare(line, [_prose(text)], valves)
    assert result.status is FULL
    assert [e.quote for e in result.evidence] == [text]


def test_another_standard_never_makes_a_standard_missing(valves: Ontology) -> None:
    result = _compare("Testing Standard : API 598", [_prose("Tested to ISO 5208.")], valves)
    assert result.status is UNSUPPORTED


def test_a_labelled_field_decides_before_a_standard_code(valves: Ontology) -> None:
    labelled = _row(["Item", "Standard"], ["Design", "API 609"], "Technical data")
    result = _compare("Design Standard : EN 593", [labelled, _prose("Also EN 593 flanges")], valves)
    assert result.status is MISSING


def test_a_standard_code_needs_the_ontology(valves: Ontology) -> None:
    result = _compare("Testing Standard : API 598", [_prose("Tested to API 598.")], None)
    assert result.status is UNSUPPORTED


def test_a_standard_code_on_another_products_line_is_not_evidence(valves: Ontology) -> None:
    several = replace(DOC, product_types=("butterfly_valve", "gate_valve"))
    chunk = ChunkDraft(
        page_number=2,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text="Gate valves are tested to API 598.",
        token_count=1,
    )
    untagged = CatalogueChunk(several, chunk)
    gate = CatalogueChunk(several, chunk.model_copy(update={"product_type": "gate_valve"}))
    own = CatalogueChunk(several, chunk.model_copy(update={"product_type": "butterfly_valve"}))
    requirement = _req("Testing Standard : API 598", valves).model_copy(
        update={"product_type": "butterfly_valve"}
    )
    ctx = MatchContext.for_ontology(valves)

    def status(chunks: list[CatalogueChunk]) -> MatchStatus:
        return compare(requirement, located_fields(chunks, OPTIONS.fields), ctx, OPTIONS).status

    assert status([untagged, gate]) is UNSUPPORTED
    assert status([own]) is FULL


def test_a_standard_code_in_the_label_cell_lifts_a_near_miss(valves: Ontology) -> None:
    # L&T ball valves p6: "Scope: Design* ISO 17292/ | Standard: API 6D".
    row = _row(["Scope", "Standard"], ["Design* ISO 17292/", "API 6D"], "Standards & Materials")
    result = _compare("Product Standard : BS EN ISO 17292", [row], valves)
    assert result.status is FULL
