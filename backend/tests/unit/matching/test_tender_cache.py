"""Tender file cache in MatchService: a file is processed once per content, then read back."""

from pathlib import Path
from typing import Any, cast

import pytest

import app.services.match_service as match_service
from app.ingestion.processing import ProcessedDocument, ProcessedPage, ProcessingOptions
from app.matching.chunk_source import ProcessedCache
from app.services.match_service import MatchService

PROCESSED = ProcessedDocument(pages=[ProcessedPage(1, "Item | Thermocouple", ocr=False)], chunks=[])


def _service(tmp_path: Path, cache: ProcessedCache | None) -> MatchService:
    unused = cast(Any, None)  # parse-only: matching collaborators are never touched
    return MatchService(
        source=unused,
        tender_options=cast(ProcessingOptions, None),
        requirement_options=unused,
        selection=unused,
        confidence=unused,
        ontologies=[],
        tender_root=tmp_path,
        tender_cache=cache,
    )


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def fake_process(path: Path, _: ProcessingOptions) -> ProcessedDocument:
        seen.append(path.name)
        return PROCESSED

    monkeypatch.setattr(match_service, "process_file", fake_process)
    return seen


def test_a_tender_file_is_processed_once_then_read_from_the_cache(
    tmp_path: Path, calls: list[str]
) -> None:
    pdf = tmp_path / "atc.pdf"
    pdf.write_bytes(b"%PDF one")
    service = _service(tmp_path, ProcessedCache(tmp_path / "cache", "fp"))
    first = service._process_tender_file(pdf)
    again = service._process_tender_file(pdf)
    assert calls == ["atc.pdf"]
    assert again.pages[0].text == first.pages[0].text == "Item | Thermocouple"


def test_changed_content_or_code_is_processed_again(tmp_path: Path, calls: list[str]) -> None:
    pdf = tmp_path / "atc.pdf"
    pdf.write_bytes(b"%PDF one")
    service = _service(tmp_path, ProcessedCache(tmp_path / "cache", "fp"))
    service._process_tender_file(pdf)
    pdf.write_bytes(b"%PDF two")  # a new revision of the file
    service._process_tender_file(pdf)
    newer_code = _service(tmp_path, ProcessedCache(tmp_path / "cache", "fp2"))
    newer_code._process_tender_file(pdf)
    assert calls == ["atc.pdf", "atc.pdf", "atc.pdf"]


def test_without_a_cache_every_read_processes(tmp_path: Path, calls: list[str]) -> None:
    pdf = tmp_path / "bid.pdf"
    pdf.write_bytes(b"%PDF")
    service = _service(tmp_path, None)
    service._process_tender_file(pdf)
    service._process_tender_file(pdf)
    assert calls == ["bid.pdf", "bid.pdf"]
