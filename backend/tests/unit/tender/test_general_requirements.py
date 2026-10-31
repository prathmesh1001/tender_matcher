"""Classifier, general requirements and routing. Every line is synthetic, shaped like GeM bids."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.matching.comparator import routed
from app.matching.engine import general_result
from app.matching.scorer import score
from app.ontology.loader import load_ontologies
from app.ontology.schema import Ontology
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DecisionSource, MatchStatus, Operator, RequirementType
from app.schemas.matching import Comparison, LabelBasis
from app.schemas.requirements import Requirement, ValueKind
from app.schemas.sections import EvidenceSource
from app.tender.classifier import RequirementClassifier
from app.tender.general import english_part, general_requirements, split_sentences
from app.tender.pipeline import (
    SIZE_DIFFERS_FROM_TITLE,
    RequirementOptions,
    product_type_for,
    size_conflict,
    tender_general_requirements,
    tender_requirements,
)

BACKEND = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def options() -> RequirementOptions:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+psycopg://u:p@localhost/db",  # type: ignore[arg-type]
        SECTION_CATEGORIES_FILE=BACKEND / "config" / "section_categories.yaml",
        REQUIREMENT_RULES_FILE=BACKEND / "config" / "requirement_rules.yaml",
    )
    return RequirementOptions.from_settings(settings)


@pytest.fixture(scope="module")
def classifier(options: RequirementOptions) -> RequirementClassifier:
    return options.classifier


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    [valves] = load_ontologies(BACKEND / "ontology")
    return valves


def _row(header: list[str], cells: list[str], page: int = 1) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=" | ".join(cells),
        token_count=1,
        meta={"header": header, "cells": cells},
    )


def _prose(text: str, section: str | None = None) -> ChunkDraft:
    return ChunkDraft(
        page_number=2,
        position=0,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        section=section,
        token_count=1,
    )


def _requirement(**fields: object) -> Requirement:
    return Requirement(
        id="1",
        item_no="1",
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text="v",
        document="b.pdf",
        page_number=1,
        **fields,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- A8: Hindi / bilingual


@pytest.mark.parametrize(
    ("printed", "english"),
    [
        ("बड सं?या/Bid Number: GEM/2026/B/1", "Bid Number: GEM/2026/B/1"),
        ("बड बंद होने क तार ख/समय /Bid End Date/Time", "Bid End Date/Time"),
        ("EMD Amount", "EMD Amount"),
        ("Yes/No", "Yes/No"),
        ("केवल हिंदी", ""),
    ],
)
def test_english_part(printed: str, english: str) -> None:
    assert english_part(printed) == english


# ---------------------------------------------------------------- classification (T8, D3, D4)


@pytest.mark.parametrize(
    ("line", "category", "evidence", "req_type"),
    [
        (
            "Average annual turnover of bidder: 50 Lakh",
            "financial_capability",
            EvidenceSource.VENDOR_DOCUMENTS,
            RequirementType.COMPLIANCE_DOC,
        ),
        (
            "EMD Amount: 24000",
            "costing_and_commercial",
            EvidenceSource.VENDOR_DOCUMENTS,
            RequirementType.COMMERCIAL,
        ),
        (
            "Warranty: 36 months from supply",
            "warranty_and_support",
            EvidenceSource.VENDOR_DOCUMENTS,
            RequirementType.COMMERCIAL,
        ),
        (
            "Bid End Date/Time: 23-10-2026",
            "submission_requirements",
            EvidenceSource.NONE,
            RequirementType.COMPLIANCE_DOC,
        ),
        (
            "Ministry/State Name: Ministry of Steel",
            "buyer_details",
            EvidenceSource.NONE,
            RequirementType.COMPLIANCE_DOC,
        ),
        (
            "Arbitration clause applies",
            "legal_and_contract",
            EvidenceSource.NONE,
            RequirementType.LEGAL,
        ),
        (
            "Seat leakage as per API 598",
            "quality_and_inspection",
            EvidenceSource.CATALOGUE,
            RequirementType.TECHNICAL,
        ),
    ],
)
def test_lines_get_category_evidence_and_type(
    classifier: RequirementClassifier,
    line: str,
    category: str,
    evidence: EvidenceSource,
    req_type: RequirementType,
) -> None:
    found = classifier.classify(line)
    assert (found.category, found.evidence, found.req_type) == (category, evidence, req_type)


def test_specific_does_not_mean_specification(classifier: RequirementClassifier) -> None:
    found = classifier.classify("Bidders may visit the plant", "11. Buyer Added Bid Specific ATC")
    assert found.category != "technical_specifications"


def test_unknown_line_has_no_evidence_source(classifier: RequirementClassifier) -> None:
    found = classifier.classify("Something nobody has a rule for")
    assert (found.category, found.evidence) == ("other", None)


def test_item_line_with_no_category_is_technical(classifier: RequirementClassifier) -> None:
    found = classifier.classify("FLUID: WATER", is_item=True)
    assert (found.category, found.req_type) == (
        "technical_specifications", RequirementType.TECHNICAL,
    )  # fmt: skip
    assert found.mandatory is True


@pytest.mark.parametrize("line", ["Valve of good quality", "Material as per standard"])
def test_vague_lines(classifier: RequirementClassifier, line: str) -> None:
    assert classifier.classify(line, is_item=True).req_type is RequirementType.VAGUE


def test_a_code_or_make_is_not_vague(classifier: RequirementClassifier) -> None:
    assert (
        classifier.classify("Good quality, API 609", is_item=True).req_type
        is not RequirementType.VAGUE
    )
    assert (
        classifier.classify("Reputed make like ACME", is_item=True).req_type
        is not RequirementType.VAGUE
    )


@pytest.mark.parametrize(
    ("line", "mandatory"),
    [
        ("The seller shall submit it", True),
        ("Upload is mandatory", True),
        ("The bidder may visit", False),
        ("Bidders are advised to check", False),
        ("The seller shall submit and may visit", True),
        ("Bid opens on Monday", None),
    ],
)
def test_mandatory_is_never_guessed(
    classifier: RequirementClassifier, line: str, mandatory: bool | None
) -> None:
    assert classifier.classify(line).mandatory is mandatory


@pytest.mark.parametrize(
    ("line", "make"),
    [
        ("Description of items (Make: M/s. Acme Valves Ltd)", "Acme Valves Ltd"),
        ("reputed make like ACME/BETA", "ACME/BETA"),
        ("reputed make like ACME/BETA MAKE.", "ACME/BETA"),
        ("THIS IS SINGLE TENDER ENQUIRY ON M/S ACME VALVES LIMITED .", "ACME VALVES LIMITED"),
        ("The make of the gear unit is not stated", None),
        ("M/s. Steel Authority of India Ltd, Bokaro", None),  # a buyer, not a make
    ],
)
def test_named_make(classifier: RequirementClassifier, line: str, make: str | None) -> None:
    assert classifier.named_make(line) == make


def test_or_equivalent_is_recorded(classifier: RequirementClassifier) -> None:
    assert classifier.classify("Body WCB or equivalent").or_equivalent is True
    assert classifier.classify("Body WCB").or_equivalent is False


# ---------------------------------------------------------------- general requirements


def test_bid_rows_become_label_and_value(classifier: RequirementClassifier) -> None:
    chunk = _row(
        ["Column 1", "Column 2"],
        ["बड क तार ख/Bid End Date/Time", "23-10-2026 16:00:00"],
    )
    [found] = general_requirements([chunk], "bid.pdf", classifier)
    assert (found.label, found.value_text) == ("Bid End Date/Time", "23-10-2026 16:00:00")
    assert (found.id, found.document, found.page_number) == ("bid.pdf#1", "bid.pdf", 1)
    assert found.raw_text == "बड क तार ख/Bid End Date/Time | 23-10-2026 16:00:00"  # verbatim
    assert found.evidence is EvidenceSource.NONE


def test_prose_gives_one_requirement_per_sentence(classifier: RequirementClassifier) -> None:
    text = "Warranty: 36 months from supply. The seller shall visit the site."
    first, second = general_requirements([_prose(text)], "spec.pdf", classifier)
    assert first.value_text == "Warranty: 36 months from supply."
    assert first.category == "warranty_and_support"
    assert second.mandatory is True


def test_abbreviations_do_not_end_a_sentence() -> None:
    text = "Enquiry on M/s. Acme Ltd. for valves. Next line here."
    assert split_sentences(text, ["m/s", "ltd"]) == [
        "Enquiry on M/s. Acme Ltd. for valves.", "Next line here.",
    ]  # fmt: skip


@pytest.mark.parametrize("text", ["1 / 9", "Download", "केवल हिंदी", "   "])
def test_lines_with_nothing_to_read_are_dropped(
    classifier: RequirementClassifier, text: str
) -> None:
    assert general_requirements([_prose(text)], "bid.pdf", classifier) == []


def test_repeats_within_a_file_are_dropped(classifier: RequirementClassifier) -> None:
    chunk = _prose("Warranty period is 36 months.")
    assert len(general_requirements([chunk, chunk], "bid.pdf", classifier)) == 1


def test_schedule_rows_are_not_general(options: RequirementOptions) -> None:
    header = ["Item No", "Item Title", "Description"]
    schedule = _row(header, ["10", "GATE VALVE", "SIZE : 100 MM"])
    note = _prose("Warranty period is 36 months.")
    found = tender_general_requirements([schedule, note], "spec.pdf", options)
    assert [r.value_text for r in found] == ["Warranty period is 36 months."]


# ---------------------------------------------------------------- items


def _schedule(title: str, description: str, header: str = "Description") -> ChunkDraft:
    return _row(["Item No", "Item Title", header], ["10", title, description])


def test_item_requirements_are_classified(options: RequirementOptions, ontology: Ontology) -> None:
    chunk = _schedule("BUTTERFLY VALVE,100MM", "BODY MOC : WCB\nWARRANTY : 36 MONTHS")
    chunk.meta["cell_lines"] = [[], [], ["BODY MOC : WCB", "WARRANTY : 36 MONTHS"]]
    [entry] = tender_requirements([chunk], "spec.pdf", options, [ontology])
    assert entry.product_type == "butterfly_valve"
    body, warranty = entry.requirements
    assert (body.evidence, body.mandatory) == (EvidenceSource.CATALOGUE, True)
    assert body.product_type == "butterfly_valve"
    assert warranty.evidence is EvidenceSource.VENDOR_DOCUMENTS


def test_make_in_the_column_header_applies_to_the_item(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _schedule(
        "BUTTERFLY VALVE", "BODY MOC : WCB", header="Description of items (Make: M/s. Acme Ltd)"
    )
    [entry] = tender_requirements([chunk], "spec.pdf", options, [ontology])
    make = entry.requirements[-1]
    assert (make.id, make.named_make, make.mandatory) == ("10.make", "Acme Ltd", True)
    assert routed(make) is not None


def test_two_items_keep_separate_requirements(options: RequirementOptions) -> None:
    first = _row(["Item No", "Item Title", "Description"], ["10", "VALVE A", "SIZE : 250 MM"])
    second = _row(["Item No", "Item Title", "Description"], ["20", "VALVE B", "SIZE : 300 MM"])
    entries = tender_requirements([first, second], "spec.pdf", options)
    assert [[r.value_text for r in e.requirements] for e in entries] == [["250 MM"], ["300 MM"]]


def test_product_type_needs_exactly_one_name(ontology: Ontology) -> None:
    assert product_type_for("GATE VALVE,100MM", ontology) == "gate_valve"
    assert product_type_for("GATE VALVE AND GLOBE VALVE", ontology) is None  # never guessed
    assert product_type_for("THERMOCOUPLE", ontology) is None
    assert product_type_for("GATE VALVE", None) is None


def _size(value: float, unit: str, attribute: str | None = "nominal_size") -> Requirement:
    return _requirement(attribute=attribute, value_num=value, unit=unit)


def test_size_against_the_title(ontology: Ontology) -> None:
    prefixes = ("dn",)
    assert size_conflict(_size(38, "dn"), "BALL VALVE DN50", ontology, prefixes)  # T1 bleed
    assert not size_conflict(_size(50, "dn"), "BALL VALVE DN50", ontology, prefixes)
    assert not size_conflict(_size(50, "mm"), "BALL VALVE DN50", ontology, prefixes)
    assert not size_conflict(_size(38, "dn"), "BALL VALVE", ontology, prefixes)  # title has none
    assert not size_conflict(_size(38, "dn"), "BALL VALVE DN50", None, prefixes)


def test_size_conflict_is_flagged_on_the_requirement(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _schedule("BUTTERFLY VALVE,100MM", "SIZE : 250 MM")
    [entry] = tender_requirements([chunk], "spec.pdf", options, [ontology])
    assert entry.requirements[0].flags == [SIZE_DIFFERS_FROM_TITLE]


# ---------------------------------------------------------------- routing and scores (T8)


@pytest.mark.parametrize(
    ("fields", "status", "review"),
    [
        ({"evidence": EvidenceSource.VENDOR_DOCUMENTS}, MatchStatus.UNSUPPORTED, True),
        ({"evidence": EvidenceSource.NONE}, MatchStatus.NOT_APPLICABLE, False),
        ({"evidence": None}, MatchStatus.UNSUPPORTED, True),
        ({"req_type": RequirementType.VAGUE}, MatchStatus.UNSUPPORTED, True),
        ({"named_make": "Acme"}, MatchStatus.UNSUPPORTED, True),
    ],
)
def test_routing_never_fails_a_requirement(
    fields: dict[str, object], status: MatchStatus, review: bool
) -> None:
    result = routed(_requirement(**fields))
    assert result is not None
    assert (result.status, result.needs_review) == (status, review)
    assert result.status is not MatchStatus.MISSING_CAPABILITY


def test_catalogue_requirement_goes_on_to_the_catalogues() -> None:
    requirement = _requirement(evidence=EvidenceSource.CATALOGUE)
    assert routed(requirement) is None
    assert general_result(requirement).status is MatchStatus.UNSUPPORTED  # waits for the judge


def _comparison(status: MatchStatus) -> Comparison:
    return Comparison(
        requirement_id="1",
        status=status,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.NONE,
        reason="",
    )


def test_not_applicable_does_not_count_in_coverage() -> None:
    scores = {MatchStatus.FULL_MATCH: 1.0}
    found = score(
        [
            _comparison(MatchStatus.FULL_MATCH),
            _comparison(MatchStatus.UNSUPPORTED),
            _comparison(MatchStatus.NOT_APPLICABLE),
        ],
        scores,
    )
    assert (found.coverage_pct, found.total) == (50.0, 2)
    assert found.counts[MatchStatus.NOT_APPLICABLE] == 1
