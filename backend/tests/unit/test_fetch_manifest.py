"""A crawl that fetched nothing leaves tender.yaml as it is. Nothing here is real data."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from app.schemas.tender_files import ManifestFile, TenderManifest
from app.services.ingestion_service import IngestionService
from app.tender.link_fetcher import CrawlReport
from tests.conftest import make_test_settings

MANIFEST = """bid_number: GEM/2099/B/1
source: test
files:
- file: bid.pdf
  role: bid_document
  url: null
  fetched_at: '2026-10-22T06:58:58+00:00'
"""


class _Crawler:
    def __init__(self, report: CrawlReport) -> None:
        self.report = report

    def crawl(self, tender_ref: str, manifest: TenderManifest) -> CrawlReport:
        return self.report


def _service(monkeypatch: pytest.MonkeyPatch, report: CrawlReport) -> IngestionService:
    monkeypatch.setattr(IngestionService, "_crawler", lambda self, client: _Crawler(report))
    return IngestionService(
        settings=make_test_settings(),
        session_factory=MagicMock(),
        store=MagicMock(),
        options=MagicMock(),
        link_rules=MagicMock(),
        http_client_factory=MagicMock(),
    )


def _fetched() -> ManifestFile:
    return ManifestFile(file="spec.pdf", role="buyer_spec", url=None, fetched_at=datetime.now(UTC))


def test_nothing_fetched_leaves_the_manifest_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tender.yaml").write_text(MANIFEST, encoding="utf-8")
    service = _service(monkeypatch, CrawlReport())
    service._fetch_one(tmp_path, MagicMock(spec=httpx.Client))
    assert (tmp_path / "tender.yaml").read_text(encoding="utf-8") == MANIFEST


def test_a_fetched_file_rewrites_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tender.yaml").write_text(MANIFEST, encoding="utf-8")
    service = _service(monkeypatch, CrawlReport(fetched=[_fetched()]))
    service._fetch_one(tmp_path, MagicMock(spec=httpx.Client))
    assert (tmp_path / "tender.yaml").read_text(encoding="utf-8") != MANIFEST
