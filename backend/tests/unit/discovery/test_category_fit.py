"""Category fit by shared words. Category names are real GeM search results (2026-11-22)."""

from pathlib import Path

import pytest

from app.discovery.category_fit import FitWords, ItemWords, best_categories
from app.discovery.gem_config import load_gem_config
from app.discovery.gem_parser import CategoryLink
from app.retrieval.text_normalizer import SearchVocabulary

BACKEND = Path(__file__).resolve().parents[3]
COVERAGE, PRECISION = 0.6, 0.5


@pytest.fixture(scope="module")
def fit(tools: tuple[SearchVocabulary, object]) -> FitWords:
    vocabulary, _ = tools
    config = load_gem_config(BACKEND / "config" / "gem_marketplace.yaml")
    return FitWords.build(
        vocabulary,
        config.word_equivalents,
        config.category_cut_words,
        config.category_filler_words,
    )


def _links(*names: str) -> list[CategoryLink]:
    return [CategoryLink(name, f"slug-{i}") for i, name in enumerate(names)]


def _names(links: list[CategoryLink]) -> list[str]:
    return [link.name for link in links]


def test_abbreviation_and_cut_words_find_the_category(fit: FitWords) -> None:
    offered = _links(
        "Hexagonal Socket Head Cap Screw",
        "Bolt / Bullet Seal",
        "Hexagonal Head Bolts (V2) ISI marked to IS 1363 (Part 1)",
        "Square Head Bolts",
    )
    found = best_categories(
        offered, ["SS hex head bolt set as per Annexure"], fit, COVERAGE, PRECISION
    )
    # hex = hexagonal; "Square Head Bolts" shares 2 words but ranks after; screws and a
    # one-word "Bolt" part never fit
    assert _names(found)[0] == "Hexagonal Head Bolts (V2) ISI marked to IS 1363 (Part 1)"
    assert "Hexagonal Socket Head Cap Screw" not in _names(found)
    assert "Bolt / Bullet Seal" not in _names(found)


def test_extra_words_in_between_are_allowed_and_kits_rank_last(fit: FitWords) -> None:
    offered = _links(
        "Lock Washer",
        "Hardwares (Hex Bolt & Spring Channel Nut, Anchor Fastener, Washer, Spring Washer) - BHEL",
        "Stainless Steel Spring Lock Washer - Bhel-IS:3063",
    )
    found = best_categories(
        offered, ["SS spring washer set as per Annexure A"], fit, COVERAGE, PRECISION
    )
    assert _names(found)[0] == "Stainless Steel Spring Lock Washer - Bhel-IS:3063"
    assert "Lock Washer" not in _names(found)  # shares only "washer"


@pytest.mark.parametrize(
    ("title", "offered"),
    [
        ("Brake Pad", ["Writing Pad Chair", "Mouse Pad (V2)"]),
        ("Fan Belt", ["Timing Belt", "Endless V Belt for Industrial Purposes"]),
        ("Door Glass RH", ["Wooden Display cabinet with Glass Doors", "Sliding Doors (V2)"]),
        (
            "COOLANT SHUT OFF VALVE 6612E155 HALF INCH",
            ["PHPA for Water Shut off (Low Temperature) (ONGC)", "Liquid Offtake Valve"],
        ),
    ],
)
def test_other_products_sharing_a_word_never_fit(
    fit: FitWords, title: str, offered: list[str]
) -> None:
    assert best_categories(_links(*offered), [title], fit, COVERAGE, PRECISION) == []


def test_a_one_word_item_is_left_to_the_exact_rule(fit: FitWords) -> None:
    offered = _links("Cadaverous Injector", "Fuel Injector")
    assert best_categories(offered, ["INJECTOR 74 HP"], fit, COVERAGE, PRECISION) == []


def test_item_phrase_is_the_title_before_its_cut_words(fit: FitWords) -> None:
    item = ItemWords.from_titles(["SS hex head bolt set as per Annexure A"], fit)
    # "set" is filler, "as per ..." is cut off, ss and hex are written out
    assert item.phrases == (("stainless", "steel", "hexagon", "head", "bolt"),)
