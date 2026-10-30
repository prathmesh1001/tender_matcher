"""Section categories from the real rules file (synthetic text shaped like real GeM rows)."""

from pathlib import Path

import pytest
import yaml

from app.core.exceptions import ConfigFileError
from app.ingestion.section_classifier import SectionClassifier, load_section_rules
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary, normalize_for_search
from app.schemas.sections import CategorySource

BACKEND = Path(__file__).resolve().parents[3]
RULES_FILE = BACKEND / "config" / "section_categories.yaml"


@pytest.fixture(scope="module")
def vocab() -> SearchVocabulary:
    return SearchVocabulary.from_ontologies(load_ontologies(BACKEND / "ontology"))


@pytest.fixture(scope="module")
def classifier(vocab: SearchVocabulary) -> SectionClassifier:
    return SectionClassifier.from_rules(load_section_rules(RULES_FILE), vocab)


def _classify(
    classifier: SectionClassifier, vocab: SearchVocabulary, heading: str, text: str
) -> tuple[str, CategorySource]:
    result = classifier.classify(
        normalize_for_search(heading, vocab), normalize_for_search(text, vocab)
    )
    return result.category, result.source


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bid Details | EMD Amount: 24000", "costing_and_commercial"),
        ("Bid Details | Estimated Bid Value: 1223299", "costing_and_commercial"),
        ("Bid Details | Payment Timelines: within 30 days of CRAC", "costing_and_commercial"),
        (
            "Bid Details | Minimum Average Annual Turnover of the bidder (For 3 Years): 50 Lakh",
            "financial_capability",
        ),
        ("Bid Details | OEM Average Turnover (Last 3 Years): 100 Lakh", "financial_capability"),
        (
            "Bid Details | Years of Past Experience Required for same/similar service: 3",
            "past_experience",
        ),
        # MSE relaxation is an eligibility rule, not a turnover requirement (order breaks the tie).
        (
            "Bid Details | MSE Relaxation for Years of Experience and Turnover: Yes",
            "eligibility_criteria",
        ),
        ("Bid Details | Bid End Date/Time: 16-10-2026 15:00:00", "submission_requirements"),
        ("Bid Details | Organisation Name: Nhpc Limited", "buyer_details"),
        (
            "Consignee Reporting/Officer: A. Kumar; Quantity: 15; Delivery Days: 90",
            "delivery_and_timeline",
        ),
        ("Bid Details | Arbitration Clause: No", "legal_and_contract"),
        ("Primary product category: DUAL PLATE WAFER TYPE CHECK VALVE", "technical_specifications"),
    ],
)
def test_headingless_bid_rows_are_classified_by_their_label(
    classifier: SectionClassifier, vocab: SearchVocabulary, text: str, expected: str
) -> None:
    assert _classify(classifier, vocab, "", text) == (expected, CategorySource.TEXT)


@pytest.mark.parametrize(
    ("heading", "expected"),
    [
        ("Dimensions - PN 16", "size_and_dimensions"),
        ("Trim Materials", "material_and_construction"),
        ("Pressure Rating", "performance_requirements"),
        ("Valve Torque Data (in Nm)", "performance_requirements"),
        ("TESTS & INSPECTION:", "quality_and_inspection"),
        ("3. Warranty", "warranty_and_support"),
        ("5. Certificates", "certifications_and_licenses"),
        ("( XIV) ARBITRATION (14.2):", "legal_and_contract"),
        ("Specific Technical Parameter (Project Specific)", "technical_specifications"),
        ("PREPARATION FOR SHIPMENT", "delivery_and_timeline"),
    ],
)
def test_heading_decides_before_text(
    classifier: SectionClassifier, vocab: SearchVocabulary, heading: str, expected: str
) -> None:
    # The text names another category; the heading still wins.
    category, source = _classify(classifier, vocab, heading, "EMD Amount: 24000")
    assert (category, source) == (expected, CategorySource.HEADING)


def test_variants_match_after_normalization(
    classifier: SectionClassifier, vocab: SearchVocabulary
) -> None:
    # Plural/case/ligature differences do not matter (same normalizer as search_text).
    assert _classify(classifier, vocab, "DIMENSION", "")[0] == "size_and_dimensions"
    assert _classify(classifier, vocab, "Warranties", "")[0] == "warranty_and_support"


def test_noise_headings_fall_back_to_text_then_default(
    classifier: SectionClassifier, vocab: SearchVocabulary
) -> None:
    assert _classify(classifier, vocab, "Dear Sirs,", "Warranty period: 18 months") == (
        "warranty_and_support",
        CategorySource.TEXT,
    )
    assert _classify(classifier, vocab, "Dear Sirs,", "We hereby confirm the above.") == (
        "other",
        CategorySource.DEFAULT,
    )


def test_bank_guarantee_is_commercial_not_warranty(
    classifier: SectionClassifier, vocab: SearchVocabulary
) -> None:
    assert _classify(classifier, vocab, "", "Bank Guarantee format")[0] == "costing_and_commercial"


def test_every_category_declares_kind_and_evidence(classifier: SectionClassifier) -> None:
    rules = classifier.rules
    assert classifier.category("financial_capability") is not None
    assert classifier.category(rules.default) is None
    technical = [c.name for c in rules.categories if c.kind == "technical"]
    assert all(classifier.category(n).evidence == "catalogue" for n in technical)  # type: ignore[union-attr]


def _write_rules(path: Path, rules: object) -> Path:
    path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "categories",
    [
        [
            {"name": "a", "kind": "technical", "evidence": "catalogue"},
            {"name": "a", "kind": "technical", "evidence": "catalogue"},
        ],
        [{"name": "a", "kind": "technical", "evidence": "catalogue", "vendor_categories": ["zz"]}],
        [{"name": "a", "kind": "pricing", "evidence": "catalogue"}],
    ],
)
def test_invalid_rules_are_rejected(tmp_path: Path, categories: list[dict[str, object]]) -> None:
    path = _write_rules(tmp_path / "rules.yaml", {"default": "other", "categories": categories})
    with pytest.raises(ConfigFileError):
        load_section_rules(path)


def test_missing_rules_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigFileError):
        load_section_rules(tmp_path / "absent.yaml")
