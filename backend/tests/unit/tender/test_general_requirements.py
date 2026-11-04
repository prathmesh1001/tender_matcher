"""Classifier, general requirements and routing. Every line is synthetic, shaped like GeM bids."""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.matching.comparator import MatchContext, routed
from app.matching.engine import general_result, item_product_type
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
from app.tender.items import TenderFile, parse_tender_files
from app.tender.pipeline import (
    SIZE_DIFFERS_FROM_TITLE,
    ItemRequirements,
    RequirementOptions,
    product_type_for,
    product_types_named,
    size_conflict,
    tender_general_requirements,
    tender_requirements,
)
from app.tender.splitter import leaves

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


# ---------------------------------------------------------------- a title's size


def _with_title(title: str, description: str) -> ChunkDraft:
    return _row(["Item No", "Item Title", "Description"], ["10", title, description])


def _parsed(
    chunk: ChunkDraft, options: RequirementOptions, ontology: Ontology
) -> list[ItemRequirements]:
    files = [TenderFile("spec.pdf", "buyer_spec", [chunk])]
    return parse_tender_files(files, options, [ontology]).items


def test_title_size_becomes_a_requirement_when_nothing_else_states_it(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _with_title("BALL VALVE DN25 MM", "BODY MOC : WCB")
    [entry] = _parsed(chunk, options, ontology)
    size = entry.requirements[-1]
    assert (size.attribute, size.value_num, size.unit) == ("nominal_size", 25.0, "mm")
    assert (size.raw_text, size.item_no, size.page_number) == ("BALL VALVE DN25 MM", "10", 1)


def test_a_stated_size_is_not_repeated_from_the_title(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _with_title("BALL VALVE DN25 MM", "SIZE : 25 MM")
    [entry] = _parsed(chunk, options, ontology)
    assert [r.value_text for r in entry.requirements] == ["25 MM"]


def test_size_needs_a_known_product(options: RequirementOptions, ontology: Ontology) -> None:
    chunk = _with_title("THERMOWELL DN25 MM", "BODY MOC : WCB")
    [entry] = _parsed(chunk, options, ontology)
    assert [r.value_text for r in entry.requirements] == ["WCB"]


# ---------------------------------------------------------------- acceptable makes


MAKE_LINE = (
    "a) Only the following make valves shall be considered acceptable for further processing of "
    "the bid: i) M/s. Acme Valves ii) M/s. Beta iii) M/s. Key-Tech Engineering Co. iv) M/s. Delta "
    "GmbH v) M/s. Omega Ltd. b) Only the manufacturer is allowed to submit a bid."
)


def test_acceptable_make_list(classifier: RequirementClassifier) -> None:
    assert classifier.acceptable_makes(MAKE_LINE) == (
        "Acme Valves", "Beta", "Key-Tech Engineering Co", "Delta GmbH", "Omega Ltd",
    )  # fmt: skip


@pytest.mark.parametrize(
    "line",
    ["NIT issued to the following parties: 1) M/s. Acme", "Make: Acme", "the following: i) item"],
)
def test_other_lines_are_no_make_list(classifier: RequirementClassifier, line: str) -> None:
    assert classifier.acceptable_makes(line) == ()


def test_make_list_reaches_the_requirement_and_is_not_judged(
    classifier: RequirementClassifier,
) -> None:
    [found] = general_requirements([_prose(MAKE_LINE)], "spec.pdf", classifier)
    assert found.acceptable_makes[0] == "Acme Valves"
    result = routed(found)
    assert result is not None
    assert result.status is MatchStatus.UNSUPPORTED
    assert "Omega Ltd" in result.reason


# ---------------------------------------------------------------- duplicates


def test_exact_duplicates_are_dropped_within_an_item(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _with_title(
        "GATE VALVE",
        "SIZE : 100 MM\nRating/Class : Class 800 or higher\nRating/Class : Class 800 or higher,",
    )
    chunk.meta["cell_lines"] = [
        [], [], ["SIZE : 100 MM", "Rating/Class : Class 800 or higher",
                 "Rating/Class : Class 800 or higher,"],
    ]  # fmt: skip
    [entry] = _parsed(chunk, options, ontology)
    assert [r.value_text for r in leaves(entry.requirements)].count("Class 800 or higher") == 1


def test_same_value_under_another_label_is_kept(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _with_title("GATE VALVE", "BODY : WCB\nDISC : WCB")
    chunk.meta["cell_lines"] = [[], [], ["BODY : WCB", "DISC : WCB"]]
    [entry] = _parsed(chunk, options, ontology)
    assert [r.label for r in leaves(entry.requirements)] == ["BODY", "DISC"]


def test_two_items_may_state_the_same_thing(
    options: RequirementOptions, ontology: Ontology
) -> None:
    first = _row(["Item No", "Item Title", "Description"], ["10", "GATE A", "BODY : WCB"])
    second = _row(["Item No", "Item Title", "Description"], ["20", "GATE B", "BODY : WCB"])
    files = [TenderFile("spec.pdf", "buyer_spec", [first, second])]
    a, b = parse_tender_files(files, options, [ontology]).items
    assert (len(a.requirements), len(b.requirements)) == (1, 1)


# ---------------------------------------------------------------- word order of product types


@pytest.mark.parametrize(
    ("title", "product"),
    [
        ("CENTRIFUGAL HORIZONTAL PUMP WITH SEAL", "centrifugal_pump"),
        ("Submersible Sewerage Pump (Q3)", "submersible_pump"),
        ("PUMP, CENTRIFUGAL", "centrifugal_pump"),
        ("VALVE, BALL", "ball_valve"),
        ("Centrifugal Pumps", "centrifugal_pump"),
        ("GATE VALVE,100MM", "gate_valve"),
        ("SUBMERSIBLE CENTRIFUGAL HORIZONTAL PUMP", None),  # two types: never guessed
        ("WATER CIRCULATION PUMP", None),
        ("THERMOWELL", None),
    ],
)
def test_product_type_regardless_of_word_order(
    title: str, product: str | None, ontology: Ontology
) -> None:
    assert product_type_for(title, ontology) == product


def test_an_exact_phrase_goes_before_any_order(ontology: Ontology) -> None:
    # "valve" and "gate" also appear, but the title names a globe valve as a phrase.
    assert product_types_named("GLOBE VALVE FOR GATE HOUSE", ontology) == ["globe_valve"]


@pytest.mark.parametrize(
    ("title", "product"),
    [
        ("PVC foot valve Ball type set as per Annexure A", None),  # a foot valve (8157758)
        ("SS Y strainer set as per Annexure A", None),
        ("Check valve with Y strainer", "check_valve"),  # the phrase still names the type
        ("Valve, Ball type, SS", "ball_valve"),  # no other product named: any order holds
    ],
)
def test_a_title_naming_another_product_is_not_a_type_sharing_its_words(
    title: str, product: str | None, ontology: Ontology
) -> None:
    assert product_type_for(title, ontology) == product
    declared = sorted(p.name for p in ontology.product_types)
    assert item_product_type(title, declared, MatchContext.for_ontology(ontology)) == product


def test_engine_finds_the_type_in_any_order(ontology: Ontology) -> None:
    ctx = MatchContext.for_ontology(ontology)
    declared = ["centrifugal_pump", "submersible_pump"]
    assert item_product_type("CENTRIFUGAL HORIZONTAL PUMP", declared, ctx) == "centrifugal_pump"
    assert item_product_type("GATE VALVE", declared, ctx) is None  # no document declares it


# ---------------------------------------------------------------- single firm


@pytest.mark.parametrize(
    "line",
    [
        "THIS IS SINGLE TENDER ENQUIRY ON M/S ACME VALVES LIMITED .",
        "OFFER FROM ONLY THIS FIRM SHALL BE ACCEPTABLE AND ON SINGLE TENDER BASIS.",
        "Single Tender Applicable: Yes",
        "MSE Purchase Preference is not applicable since it is a Proprietary Tender",
    ],
)
def test_single_firm_lines(classifier: RequirementClassifier, line: str) -> None:
    assert classifier.classify(line).single_firm is True


@pytest.mark.parametrize(
    "line",
    [
        "except in case of Single Bid / Proprietary Article Certificate (PAC) Buying.",
        "In case Primary product of only one OEM is left in contention for participation in RA",
        "Single Tender Applicable: No",
        "Make: M/s. Acme",
    ],
)
def test_lines_that_only_look_like_it(classifier: RequirementClassifier, line: str) -> None:
    assert classifier.classify(line).single_firm is False


def test_make_in_the_column_header_is_one_firm(
    options: RequirementOptions, ontology: Ontology
) -> None:
    chunk = _schedule(
        "BUTTERFLY VALVE", "BODY MOC : WCB", header="Description of items (Make: M/s. Acme Ltd)"
    )
    [entry] = tender_requirements([chunk], "spec.pdf", options, [ontology])
    assert entry.requirements[-1].single_firm is True
