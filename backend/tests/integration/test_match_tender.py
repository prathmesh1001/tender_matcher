"""End to end, no database: a small synthetic tender + one synthetic manufacturer -> report.

The tender is a spreadsheet schedule written by the test; the catalogue is a synthetic
processed document (pages + chunks) behind `InMemoryChunkSource`. Nothing here is real
evidence.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from openpyxl import Workbook

from app.core.config import Settings
from app.ingestion.chunker import table_row_text
from app.ingestion.processing import ProcessedDocument, ProcessedPage, ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.matching.chunk_source import InMemoryChunkSource
from app.matching.documents import SelectionOptions
from app.ontology.loader import load_ontologies
from app.reporting.json_report import write_json_report
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ChunkDraft
from app.schemas.enums import (
    ChunkType,
    DocType,
    ExtractionIssueCode,
    ExtractionStatus,
    MatchStatus,
)
from app.schemas.sources import RegisteredSource
from app.services.match_service import MatchService
from app.tender.pipeline import RequirementOptions
from tests.conftest import make_test_settings

BACKEND = Path(__file__).resolve().parents[2]
TENDER = "GEM_2099_B_0000001"
DESCRIPTION = "SIZE : 250 MM\nBODY MOC : ASTM A216 Gr. WCB\nPRESSURE RATING : PN 16\nFLUID : WATER"


def _tender(root: Path) -> None:
    folder = root / TENDER
    folder.mkdir(parents=True)
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Item No", "Item Title", "Description", "Quantity", "Unit"])
    sheet.append(["10", "TRIPLE OFFSET BUTTERFLY VALVE,250MM", DESCRIPTION, "4", "Nos"])
    book.save(folder / "boq.xlsx")
    manifest = {
        "bid_number": "GEM/2099/B/0000001",
        "files": [{"file": "boq.xlsx", "role": "boq_items", "url": None}],
    }
    (folder / "tender.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")


def _row(header: list[str], cells: list[str]) -> ChunkDraft:
    return ChunkDraft(
        page_number=2,
        position=0,
        chunk_type=ChunkType.TABLE_ROW,
        raw_text=table_row_text(header, cells, "Materials"),
        section="Materials",
        token_count=1,
        meta={"header": header, "cells": cells},
    )


CATALOGUE = ProcessedDocument(
    pages=[
        ProcessedPage(1, "Triple offset butterfly valves\nSize range: 80 mm to 1200 mm", ocr=False),
        ProcessedPage(
            2, "Components Materials\nBody A 216 Gr. WCB\nPressure Rating PN 10", ocr=False
        ),
    ],
    chunks=[
        ChunkDraft(
            page_number=1,
            position=0,
            chunk_type=ChunkType.PROSE,
            raw_text="Size range: 80 mm to 1200 mm",
            token_count=1,
        ),
        _row(["Components", "Materials"], ["Body", "A 216 Gr. WCB"]),
        _row(["Components", "Pressure Rating"], ["Body", "PN 10"]),
    ],
)


def _source() -> RegisteredSource:
    return RegisteredSource(
        manufacturer="Acme Valves",
        category="valves_pumps",
        product_types=["butterfly_valve"],
        sub_types=["triple_offset"],
        title="Synthetic triple offset catalogue",
        doc_type=DocType.CATALOGUE,
        url="https://example.com/synthetic.pdf",
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        publish_date=None,
        sha256="c" * 64,
        path=Path("synthetic.pdf"),
        registry_file=Path("sources.yaml"),
    )


def _service(tmp_path: Path, settings: Settings) -> MatchService:
    ontologies = load_ontologies(BACKEND / "ontology")
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    options = ProcessingOptions.from_settings(
        settings.model_copy(
            update={"SECTION_CATEGORIES_FILE": BACKEND / "config" / "section_categories.yaml"}
        ),
        vocabulary,
        ProductMatcher.from_ontologies(ontologies, vocabulary),
        None,
    )
    _tender(tmp_path)
    return MatchService(
        source=InMemoryChunkSource([_source()], lambda _: CATALOGUE),
        tender_options=options.for_tender(),
        requirement_options=RequirementOptions.from_settings(settings),
        selection=SelectionOptions.from_settings(settings),
        ontologies=ontologies,
        tender_root=tmp_path,
    )


@pytest.fixture
def service(tmp_path: Path) -> MatchService:
    return _service(tmp_path, make_test_settings())


def test_full_report_with_ontology(service: MatchService, tmp_path: Path) -> None:
    report = service.match_tender(TENDER)
    assert report.extraction_quality.status is ExtractionStatus.OK
    [item] = report.items
    assert (item.item.item_no, item.product_type, item.sub_type) == (
        "10", "butterfly_valve", "triple_offset",
    )  # fmt: skip
    [acme] = item.manufacturers
    assert acme.chosen is not None
    assert acme.chosen.document == "synthetic.pdf"
    statuses = {r.requirement_id: r.status for r in acme.results}
    assert statuses == {
        "10.1": MatchStatus.FULL_MATCH,  # 250 mm in 80..1200 mm
        "10.2": MatchStatus.FULL_MATCH,  # Body row WCB
        "10.3": MatchStatus.MISSING_CAPABILITY,  # PN 16 required, PN 10 stated
        "10.4": MatchStatus.UNSUPPORTED,  # fluid: no catalogue field
    }
    for result in acme.results:
        for evidence in result.evidence:
            assert evidence.verified is True
    assert acme.score.compatibility_pct == pytest.approx(200 / 3)
    assert acme.score.coverage_pct == pytest.approx(75.0)
    assert [r.manufacturer for r in report.ranking] == ["Acme Valves"]

    path = write_json_report(report, tmp_path / "out" / "report.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    evidence = data["items"][0]["manufacturers"][0]["results"][1]["evidence"][0]
    assert (evidence["page_number"], evidence["quote"]) == (
        2, "Components: Body; Materials: A 216 Gr. WCB",
    )  # fmt: skip


def test_report_without_ontology(service: MatchService) -> None:
    report = service.match_tender(TENDER, use_ontology=False)
    [acme] = report.items[0].manufacturers
    assert report.ontology_used is False
    assert report.items[0].category is None
    assert acme.results[0].status is MatchStatus.FULL_MATCH  # same unit "mm" printed


def test_unreliable_extraction_sends_everything_to_review_and_changes_no_status(
    service: MatchService, tmp_path: Path
) -> None:
    clean = service.match_tender(TENDER)
    settings = make_test_settings().model_copy(update={"EXTRACTION_MAX_ITEM_REQUIREMENTS": 1})
    flagged = _service(tmp_path / "strict", settings).match_tender(TENDER)

    assert flagged.extraction_quality.status is ExtractionStatus.UNRELIABLE
    [issue] = flagged.extraction_quality.reasons
    assert issue.code is ExtractionIssueCode.OVERSIZED_ITEM

    [before] = clean.items[0].manufacturers
    [after] = flagged.items[0].manufacturers
    assert [r.status for r in after.results] == [r.status for r in before.results]
    assert [r.evidence for r in after.results] == [r.evidence for r in before.results]
    assert not all(r.needs_review for r in before.results)  # the check is what changed them
    assert all(r.needs_review for r in after.results)
    assert all("extraction unreliable: oversized_item" in (r.reason or "") for r in after.results)
    assert all(r.needs_review for r in flagged.general_results)

    total = len(after.results)
    assert after.score.needs_review == total
    assert flagged.ranking[0].score.needs_review == total
    assert after.score.compatibility_pct == before.score.compatibility_pct
    assert flagged.ranking[0].rank == clean.ranking[0].rank

    keys = list(json.loads(write_json_report(flagged, tmp_path / "r.json").read_text("utf-8")))
    assert keys.index("extraction_quality") < keys.index("items")


def test_only_the_asked_manufacturers_are_matched(service: MatchService) -> None:
    assert [m.manufacturer for m in service.match_tender(TENDER).items[0].manufacturers] == [
        "Acme Valves"
    ]
    report = service.match_tender(TENDER, manufacturers=["Someone Else"])
    assert report.items[0].manufacturers == []
    assert report.ranking == []
