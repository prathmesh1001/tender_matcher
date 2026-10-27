"""Link fetcher tests. URLs are synthetic GeM-shaped paths; HTTP is mocked (no network)."""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pymupdf
import pytest

from app.core.exceptions import LinkFetchError, TenderParseError
from app.schemas.tender_files import FetchedFile, ManifestFile, TenderManifest
from app.storage.file_store import LocalFileStore
from app.tender.link_fetcher import (
    CompiledLinkRules,
    LinkCrawler,
    bid_number_from_pdf,
    clean_url,
    extract_pdf_links,
    fetch_url,
    file_extension,
    init_manifest,
    load_link_rules,
    read_manifest,
    sha256_hex,
    unique_name,
    write_manifest,
)

RULES_FILE = Path(__file__).resolve().parents[3] / "config" / "tender_links.yaml"
SPEC = "https://mkp.gem.gov.in/x/CatalogAttrs/SpecificationDocument/2026/spec_a.pdf"
SPEC_COPY = "https://bidplus.gem.gov.in/bidding/bid/documentdownload/1/2.pdf"
BOQ = "https://mkp.gem.gov.in/x/OrderItem/BoqLineItemsDocument/2026/boq.csv"
GTC = "https://admin.gem.gov.in/apis/v1/gtc/pdfByDate/?date=20260101"
MII = "https://bidplus.gem.gov.in/bidding/bid/downloadMseMiiDoc/1/3.pdf"
DEEP = "https://mkp.gem.gov.in/x/OrderItem/BoqDocument/2026/annexure.pdf"
TENDER_REF = "tenders/T1"
MAX_BYTES = 10_000_000

Responses = dict[str, tuple[int, bytes, str]]


@pytest.fixture(scope="module")
def rules() -> CompiledLinkRules:
    return load_link_rules(RULES_FILE)


def _pdf_with_links(path: Path, uris: list[str], text: str = "") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text, fontsize=9)
    for i, uri in enumerate(uris):
        rect = pymupdf.Rect(72, 100 + 20 * i, 300, 115 + 20 * i)
        page.insert_text((72, 112 + 20 * i), f"link {i}", fontsize=9)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": uri})
    data: bytes = doc.tobytes()
    doc.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _zip(part: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(part, "<x/>")
    return buffer.getvalue()


def _client(responses: Responses, calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        status, body, ctype = responses[str(request.url)]
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _tender(tmp_path: Path, bid_links: list[str]) -> tuple[LocalFileStore, TenderManifest]:
    data = _pdf_with_links(tmp_path / "tenders" / "T1" / "bid.pdf", bid_links)
    entry = ManifestFile(
        file="bid.pdf", role="bid_document", url=None, sha256=sha256_hex(data), depth=0
    )
    return LocalFileStore(tmp_path), TenderManifest(bid_number="GEM/2026/B/1", files=[entry])


def _crawler(
    store: LocalFileStore, rules: CompiledLinkRules, client: httpx.Client | None, depth: int = 3
) -> LinkCrawler:
    return LinkCrawler(
        store=store, rules=rules, max_depth=depth, max_bytes=MAX_BYTES, client=client
    )


@pytest.mark.parametrize(
    ("url", "role", "action"),
    [
        (SPEC, "buyer_spec", "fetch"),
        (BOQ, "boq_items", "fetch"),
        ("https://fulfilment.gem.gov.in/contract/slafds?fileDownloadPath=x.pdf", "atc", "fetch"),
        (MII, "mii_mse_doc", "fetch"),
        ("https://bidplus.gem.gov.in/bidding/downloadMseMiiDoc/1/2.pdf", "mii_mse_doc", "fetch"),
        (GTC, "gtc", "skip"),
        ("https://bidplus.gem.gov.in/bidding/downloadOmppdfile/", "omp_doc", "skip"),
        ("https://www.example.invalid/", None, "skip"),
    ],
)
def test_classify(rules: CompiledLinkRules, url: str, role: str | None, action: str) -> None:
    assert rules.classify(url) == (role, action)


def test_clean_url_strips_sentence_punctuation() -> None:
    assert clean_url(" https://gem.gov.in. ") == "https://gem.gov.in"


def test_extract_links_from_annotations_and_text(tmp_path: Path) -> None:
    path = tmp_path / "bid.pdf"
    _pdf_with_links(path, [SPEC, SPEC], text=f"See {BOQ}.")
    assert extract_pdf_links(path) == [SPEC, BOQ]


@pytest.mark.parametrize(
    ("content", "content_type", "url", "ext"),
    [
        (b"%PDF-1.7", "application/octet-stream", "https://x.invalid/a", ".pdf"),
        (_zip("word/document.xml"), "application/pdf", "https://x.invalid/a.pdf", ".docx"),
        (_zip("xl/workbook.xml"), None, "https://x.invalid/a", ".xlsx"),
        (b"<html></html>", "text/html; charset=UTF-8", "https://x.invalid/a", ".html"),
        (b"a,b\n1,2", "application/octet-stream", "https://x.invalid/boq.csv", ".csv"),
        (b"???", None, "https://x.invalid/blob", ".bin"),
    ],
)
def test_file_extension_trusts_bytes_first(
    content: bytes, content_type: str | None, url: str, ext: str
) -> None:
    fetched = FetchedFile(
        url=url, content=content, content_type=content_type, fetched_at=datetime.now(UTC)
    )
    assert file_extension(fetched) == ext


def test_unique_name() -> None:
    assert unique_name("buyer_spec", ".pdf", set()) == "buyer_spec.pdf"
    taken = {"buyer_spec.pdf", "buyer_spec_2.pdf"}
    assert unique_name("buyer_spec", ".pdf", taken) == "buyer_spec_3.pdf"


def test_fetch_url_errors() -> None:
    client = _client({SPEC: (404, b"", "text/html"), BOQ: (200, b"x" * 50, "text/csv")}, [])
    with pytest.raises(LinkFetchError):
        fetch_url(client, SPEC, 1000)
    with pytest.raises(LinkFetchError, match="exceeds"):
        fetch_url(client, BOQ, 10)


def test_crawl_fetches_dedupes_and_records(tmp_path: Path, rules: CompiledLinkRules) -> None:
    store, manifest = _tender(tmp_path, [SPEC, SPEC_COPY, BOQ, GTC, MII])
    spec = _pdf_with_links(tmp_path / "src" / "spec.pdf", [DEEP])
    annexure = _pdf_with_links(tmp_path / "src" / "annexure.pdf", [], text="annexure")
    calls: list[str] = []
    responses: Responses = {
        SPEC: (200, spec, "application/pdf"),
        SPEC_COPY: (200, spec, "application/pdf"),  # same bytes as SPEC
        BOQ: (200, b"Item,Qty\nValve,2\n", "application/octet-stream"),
        MII: (200, b"", "text/html"),  # GeM sometimes returns nothing
        DEEP: (200, annexure, "application/pdf"),
    }
    report = _crawler(store, rules, _client(responses, calls)).crawl(TENDER_REF, manifest)

    by_url = {f.url: f for f in manifest.files}
    assert GTC not in calls  # skip rule
    assert by_url[SPEC].file == "buyer_spec.pdf"
    assert by_url[SPEC_COPY].file is None
    assert by_url[SPEC_COPY].duplicate_of == "buyer_spec.pdf"
    assert by_url[BOQ].file == "boq_items.csv"
    assert by_url[MII].file is None
    assert by_url[MII].note
    assert by_url[DEEP].depth == 2
    assert by_url[DEEP].found_in == "buyer_spec.pdf"
    assert store.exists(f"{TENDER_REF}/boq_spec.pdf")
    assert report.missing == []
    assert report.failed == []


def test_crawl_respects_max_depth(tmp_path: Path, rules: CompiledLinkRules) -> None:
    store, manifest = _tender(tmp_path, [SPEC])
    spec = _pdf_with_links(tmp_path / "src" / "spec.pdf", [DEEP])
    calls: list[str] = []
    client = _client({SPEC: (200, spec, "application/pdf")}, calls)
    _crawler(store, rules, client, depth=1).crawl(TENDER_REF, manifest)
    assert calls == [SPEC]


def test_recorded_links_are_not_refetched(tmp_path: Path, rules: CompiledLinkRules) -> None:
    store, manifest = _tender(tmp_path, [SPEC])
    manifest.files.append(ManifestFile(file=None, role="buyer_spec", url=SPEC, note="empty"))
    calls: list[str] = []
    _crawler(store, rules, _client({}, calls)).crawl(TENDER_REF, manifest)
    assert calls == []


def test_offline_crawl_reports_missing_once(tmp_path: Path, rules: CompiledLinkRules) -> None:
    store, manifest = _tender(tmp_path, [SPEC, GTC])
    report = _crawler(store, rules, None).crawl(TENDER_REF, manifest)
    assert [m.url for m in report.missing] == [SPEC]
    assert len(manifest.files) == 1


def test_failed_download_is_reported_not_raised(tmp_path: Path, rules: CompiledLinkRules) -> None:
    store, manifest = _tender(tmp_path, [SPEC])
    client = _client({SPEC: (500, b"", "text/html")}, [])
    report = _crawler(store, rules, client).crawl(TENDER_REF, manifest)
    assert [url for url, _ in report.failed] == [SPEC]
    assert [m.url for m in report.missing] == [SPEC]


def test_manifest_round_trip_and_init(tmp_path: Path) -> None:
    folder = tmp_path / "T2"
    _pdf_with_links(folder / "bid.pdf", [], text="Bid Number: GEM/2026/B/1234567")
    manifest = init_manifest(folder)
    assert manifest.bid_number == "GEM/2026/B/1234567"
    assert manifest.files[0].url is None
    write_manifest(folder / "tender.yaml", manifest)
    assert read_manifest(folder / "tender.yaml") == manifest


def test_bid_number_missing(tmp_path: Path) -> None:
    path = tmp_path / "bid.pdf"
    _pdf_with_links(path, [], text="no number here")
    with pytest.raises(TenderParseError):
        bid_number_from_pdf(path)


def test_unreadable_download_is_reported_and_crawl_continues(
    tmp_path: Path, rules: CompiledLinkRules
) -> None:
    store, manifest = _tender(tmp_path, [SPEC, BOQ])
    responses: Responses = {
        SPEC: (200, b"%PDF-1.4 truncated", "application/pdf"),
        BOQ: (200, b"Item,Qty", "text/csv"),
    }
    report = _crawler(store, rules, _client(responses, [])).crawl(TENDER_REF, manifest)
    assert [name for name, _ in report.failed] == ["buyer_spec.pdf"]
    assert {f.file for f in report.fetched} == {"buyer_spec.pdf", "boq_items.csv"}
