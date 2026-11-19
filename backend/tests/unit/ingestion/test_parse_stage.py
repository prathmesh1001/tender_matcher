"""Parse cache: a chunking or ontology change re-chunks from the stored parse, never re-parses."""

import ast
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from app.ingestion.parse_stage import (
    PARSE_CODE_FILES,
    ParseCache,
    options_json,
    parse_code_fingerprint,
    parse_with_tables,
)
from app.ingestion.pdf_parser import parse_pdf
from app.ingestion.processing import ProcessingOptions, parse_options, process_file
from app.ingestion.product_tagger import ProductMatcher
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from tests.conftest import make_test_settings
from tests.unit.ingestion.conftest import write_catalogue_pdf

BACKEND_DIR = Path(__file__).resolve().parents[3]
ONTOLOGY_DIR = BACKEND_DIR / "ontology"
# Modules whose own code never changes a parse result (settings readers, exception types).
_NOT_PARSE_OUTPUT = ("app.core.",)


@pytest.fixture(scope="module")
def options() -> ProcessingOptions:
    settings = make_test_settings()
    ontologies = load_ontologies(ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    return ProcessingOptions.from_settings(settings, vocabulary, matcher, ocr_engine=None)


def _cache(root: Path) -> ParseCache:
    return ParseCache(root, parse_code_fingerprint(BACKEND_DIR))


def _add_page(path: Path, text: str) -> None:
    doc = pymupdf.open(path)
    doc.new_page().insert_text((72, 60), text)
    data = doc.tobytes()
    doc.close()
    path.write_bytes(data)


def _count_parses(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []

    def counting(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(path)
        return parse_pdf(path, *args, **kwargs)

    monkeypatch.setattr("app.ingestion.parse_stage.parse_pdf", counting)
    return calls


def test_second_processing_reads_the_parse_from_disk(
    tmp_path: Path, options: ProcessingOptions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_parses(monkeypatch)
    cached = replace(options, parse_cache=_cache(tmp_path / "parsed"))
    pdf = write_catalogue_pdf(tmp_path / "c.pdf")

    first = process_file(pdf, cached)
    second = process_file(pdf, cached)

    assert len(calls) == 1
    assert second == first
    assert first == process_file(pdf, options)  # same output as without a cache


def test_chunker_change_reuses_the_parse(
    tmp_path: Path, options: ProcessingOptions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_parses(monkeypatch)
    cache = _cache(tmp_path / "parsed")
    pdf = write_catalogue_pdf(tmp_path / "c.pdf")
    process_file(pdf, replace(options, parse_cache=cache))

    rechunked = replace(options, chunker=replace(options.chunker, max_tokens=50))
    process_file(pdf, replace(rechunked, parse_cache=cache))

    assert len(calls) == 1


def test_parse_option_or_file_change_is_a_new_parse(
    tmp_path: Path, options: ProcessingOptions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_parses(monkeypatch)
    cache = _cache(tmp_path / "parsed")
    pdf = write_catalogue_pdf(tmp_path / "c.pdf")
    parse = parse_options(options)

    parse_with_tables(pdf, parse, cache)
    parse_with_tables(pdf, replace(parse, key_value_tables=True), cache)  # tender reading
    _add_page(pdf, "ETN 65")
    parse_with_tables(pdf, parse, cache)

    assert len(calls) == 3


def test_unreadable_cache_file_parses_again(
    tmp_path: Path, options: ProcessingOptions, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _count_parses(monkeypatch)
    cache = _cache(tmp_path / "parsed")
    pdf = write_catalogue_pdf(tmp_path / "c.pdf")
    parse = parse_options(options)
    parse_with_tables(pdf, parse, cache)
    for stored in (tmp_path / "parsed").iterdir():
        stored.write_text("{broken", encoding="utf-8")

    again = parse_with_tables(pdf, parse, cache)

    assert len(calls) == 2
    assert again.pages


def test_options_json_does_not_depend_on_set_order(options: ProcessingOptions) -> None:
    units = sorted(options.table_cleanup.units)
    forward = replace(options.table_cleanup, units=frozenset(units))
    backward = replace(options.table_cleanup, units=frozenset(reversed(units)))
    parse = parse_options(options)

    assert options_json(replace(parse, table_cleanup=forward)) == options_json(
        replace(parse, table_cleanup=backward)
    )


def _app_imports(module_file: Path) -> set[str]:
    tree = ast.parse(module_file.read_text(encoding="utf-8"))
    found = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    return {m for m in found if m.startswith("app.") and not m.startswith(_NOT_PARSE_OUTPUT)}


def test_every_module_the_parse_uses_is_in_the_fingerprint() -> None:
    listed = {f.removesuffix(".py").replace("/", ".") for f in PARSE_CODE_FILES}
    seen: set[str] = set()
    todo = ["app.ingestion.parse_stage"]
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        todo.extend(_app_imports(BACKEND_DIR / f"{module.replace('.', '/')}.py"))

    assert seen <= listed, sorted(seen - listed)
