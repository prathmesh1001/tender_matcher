"""ChunkSource: product filter, sha256 cache, page lookup. Documents and chunks are synthetic."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core.exceptions import NotFoundError
from app.ingestion.processing import ProcessedDocument, ProcessedPage
from app.matching.chunk_source import (
    CatalogueDocument,
    InMemoryChunkSource,
    ProcessedCache,
    declared_types_named,
    evidence_chunks,
    fingerprint,
)
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import ChunkType, DocType
from app.schemas.sources import RegisteredSource

SHA_A = "a" * 64
SHA_B = "b" * 64


def _source(sha: str, product_types: list[str], manufacturer: str = "Acme") -> RegisteredSource:
    return RegisteredSource(
        manufacturer=manufacturer,
        category="valves_pumps",
        product_types=product_types,
        sub_types=[],
        title=f"Synthetic catalogue {sha[0]}",
        doc_type=DocType.CATALOGUE,
        url="https://example.com/synthetic.pdf",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        publish_date=None,
        sha256=sha,
        path=Path(f"synthetic_{sha[0]}.pdf"),
        registry_file=Path("sources.yaml"),
    )


def _chunk(text: str, product_type: str | None, page: int = 1) -> ChunkDraft:
    return ChunkDraft(
        page_number=page,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=text,
        search_text=text.lower(),
        product_type=product_type,
        token_count=1,
        meta={"header": ["Part", "Material"], "cells": text.split(" | ")},
    )


PROCESSED = ProcessedDocument(
    pages=[ProcessedPage(1, "Body | ASTM A216 WCB", ocr=False)],
    chunks=[
        _chunk("Body | ASTM A216 WCB", "butterfly_valve"),
        _chunk("Ball | SS 316", "ball_valve"),
        _chunk("General notes", None),
    ],
)


def test_declared_types_named_by_title() -> None:
    types = ["butterfly_valve", "ball_valve", "gate_valve"]
    assert declared_types_named("TRIPLE OFFSET BUTTERFLY VALVE,250MM", types) == {"butterfly_valve"}
    assert declared_types_named("BFV 250 MM", types) == set()  # no synonyms without ontology


def test_evidence_chunks_by_tag_keeps_untagged_of_declaring_document() -> None:
    doc = CatalogueDocument.from_source(_source(SHA_A, ["butterfly_valve", "ball_valve"]))
    got = evidence_chunks(doc, PROCESSED.chunks, "butterfly_valve", use_tags=True)
    assert [c.chunk.raw_text for c in got] == ["Body | ASTM A216 WCB", "General notes"]


def test_evidence_chunks_without_tags_takes_whole_declaring_document() -> None:
    doc = CatalogueDocument.from_source(_source(SHA_A, ["butterfly_valve"]))
    assert len(evidence_chunks(doc, PROCESSED.chunks, "butterfly_valve", use_tags=False)) == 3
    assert evidence_chunks(doc, PROCESSED.chunks, "gate_valve", use_tags=False) == []


def test_source_processes_only_declaring_documents_once() -> None:
    calls: list[str] = []

    def process(source: RegisteredSource) -> ProcessedDocument:
        calls.append(source.sha256)
        return PROCESSED

    source = InMemoryChunkSource(
        [_source(SHA_A, ["butterfly_valve"]), _source(SHA_B, ["gate_valve"])], process
    )
    for _ in range(2):
        source.chunks_for_product("Acme", "valves_pumps", "butterfly_valve", use_tags=True)
    assert calls == [SHA_A]
    assert [d.sha256 for d in source.documents("Acme", "valves_pumps", "butterfly_valve")] == [
        SHA_A
    ]
    assert source.manufacturers("valves_pumps") == ["Acme"]


def test_page_text_and_unknown_document() -> None:
    source = InMemoryChunkSource([_source(SHA_A, ["butterfly_valve"])], lambda _: PROCESSED)
    assert source.page_text(SHA_A, 1) == "Body | ASTM A216 WCB"
    assert source.page_text(SHA_A, 9) is None
    with pytest.raises(NotFoundError):
        source.page_text(SHA_B, 1)


def test_cache_round_trip_and_fingerprint_miss(tmp_path: Path) -> None:
    ProcessedCache(tmp_path, "v1").put(SHA_A, PROCESSED)
    assert ProcessedCache(tmp_path, "v1").get(SHA_A) == PROCESSED
    assert ProcessedCache(tmp_path, "v2").get(SHA_A) is None  # other code -> reprocess
    assert ProcessedCache(tmp_path, "v1").get(SHA_B) is None


def test_cache_corrupt_file_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / f"{SHA_A}.json").write_text("{not json", encoding="utf-8")
    assert ProcessedCache(tmp_path, "v1").get(SHA_A) is None


def test_source_uses_disk_cache(tmp_path: Path) -> None:
    ProcessedCache(tmp_path, "v1").put(SHA_A, PROCESSED)

    def never(_: RegisteredSource) -> ProcessedDocument:
        raise AssertionError("cached file was processed again")

    source = InMemoryChunkSource(
        [_source(SHA_A, ["butterfly_valve"])], never, ProcessedCache(tmp_path, "v1")
    )
    assert (
        len(source.chunks_for_product("Acme", "valves_pumps", "butterfly_valve", use_tags=True))
        == 2
    )


def test_fingerprint_changes_with_file_content(tmp_path: Path) -> None:
    code = tmp_path / "chunker.py"
    code.write_text("A = 1", encoding="utf-8")
    before = fingerprint([code], "{}")
    code.write_text("A = 2", encoding="utf-8")
    assert fingerprint([code], "{}") != before
    assert fingerprint([code], '{"X": 1}') != fingerprint([code], "{}")
