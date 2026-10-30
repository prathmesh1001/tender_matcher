"""Vocabularies compiled from the real ontology, shared by the retrieval tests."""

from pathlib import Path

import pytest

from app.ontology.loader import load_ontologies
from app.retrieval.query_builder import QueryVocabulary
from app.retrieval.text_normalizer import SearchVocabulary

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"


@pytest.fixture(scope="session")
def search_vocab() -> SearchVocabulary:
    return SearchVocabulary.from_ontologies(load_ontologies(ONTOLOGY_DIR))


@pytest.fixture(scope="session")
def query_vocab(search_vocab: SearchVocabulary) -> QueryVocabulary:
    return QueryVocabulary.from_ontologies(
        load_ontologies(ONTOLOGY_DIR), search_vocab, max_distance=2, min_length=5
    )
