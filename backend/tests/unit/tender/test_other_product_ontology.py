"""Items naming an ontology product without a type (GeM step C/D on 8157758 item 21)."""

from pathlib import Path

from app.matching.comparator import MatchContext
from app.matching.engine import item_product_type
from app.ontology.loader import load_ontologies
from app.tender.pipeline import ontology_for, product_type_for

ONTOLOGIES = load_ontologies(Path(__file__).resolve().parents[3] / "ontology")
TITLE = "PVC foot valve Ball type set as per Annexure A"


def test_a_foot_valve_belongs_to_the_valve_ontology_without_a_type() -> None:
    ontology = ontology_for(TITLE, ONTOLOGIES)
    assert ontology is not None
    assert ontology.category == "valves_pumps"  # its size table and attributes apply
    assert product_type_for(TITLE, ontology) is None  # a foot valve is no ball valve


def test_an_unknown_product_still_has_no_ontology() -> None:
    assert ontology_for("Hydraulic Hose Pipe 1/2 inch", ONTOLOGIES) is None


def test_matching_finds_the_title_key_its_gem_listings_declare() -> None:
    ontology = ontology_for(TITLE, ONTOLOGIES)
    ctx = MatchContext.for_ontology(ontology)
    declared = ["ball_valve", "gate_valve", "foot_valve"]  # foot_valve: GeM listings
    assert item_product_type(TITLE, declared, ctx) == "foot_valve"
    assert item_product_type("Ball valve 50 mm", declared, ctx) == "ball_valve"
