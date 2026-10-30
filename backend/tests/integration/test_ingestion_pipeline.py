"""End-to-end ingestion against PostgreSQL with synthetic files (never real catalogue content)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import TenderParseError
from app.db.models import Document
from app.db.repositories import (
    ChunkRepository,
    DocumentRepository,
    SectionRepository,
    TenderRepository,
)
from app.ingestion.pipeline import (
    IngestStatus,
    ReprocessPolicy,
    ingest_manufacturer_source,
    rebuild_sections,
)
from app.ingestion.processing import ProcessingOptions
from app.ingestion.product_tagger import ProductMatcher
from app.ingestion.registry import load_registry
from app.ontology.loader import load_ontologies
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.enums import DocumentSource
from app.storage.file_store import LocalFileStore
from app.tender.documents import ingest_tender_folder
from tests.conftest import make_test_settings
from tests.unit.ingestion.conftest import write_catalogue_pdf, write_text_pdf

ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"


@pytest.fixture(scope="module")
def options() -> ProcessingOptions:
    settings = make_test_settings()
    ontologies = load_ontologies(ONTOLOGY_DIR)
    vocabulary = SearchVocabulary.from_ontologies(ontologies)
    matcher = ProductMatcher.from_ontologies(ontologies, vocabulary)
    return ProcessingOptions.from_settings(settings, vocabulary, matcher, ocr_engine=None)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manufacturer_root(root: Path) -> Path:
    folder = root / "manufacturers" / "Acme"
    folder.mkdir(parents=True)
    pdf = write_catalogue_pdf(folder / "acme_pumps.pdf")
    sources = {
        "manufacturer": "Acme",
        "documents": [
            {
                "file": pdf.name,
                "category": "valves_pumps",
                "product_types": ["centrifugal_pump"],
                "title": "Acme ETN pumps (synthetic)",
                "doc_type": "datasheet",
                "url": "https://example.invalid/acme.pdf",
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "sha256": _sha(pdf),
            }
        ],
    }
    (folder / "sources.yaml").write_text(yaml.safe_dump(sources), encoding="utf-8")
    return root / "manufacturers"


def test_manufacturer_ingest_is_idempotent(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    [source] = load_registry(_manufacturer_root(tmp_path))
    first = ingest_manufacturer_source(db_session, store, source, options)
    again = ingest_manufacturer_source(db_session, store, source, options)

    assert first.status == IngestStatus.INGESTED
    assert again.status == IngestStatus.SKIPPED_EXISTING
    assert again.document_id == first.document_id
    assert first.document_id is not None
    doc = DocumentRepository(db_session).get_or_raise(first.document_id)
    assert doc.file_path == "manufacturers/Acme/acme_pumps.pdf"
    assert doc.page_count == 1
    chunks = ChunkRepository(db_session).list_for_document(doc.id)
    assert len(chunks) == first.chunks > 0
    assert all(c.raw_text and c.search_text for c in chunks)
    assert {c.product_family for c in chunks} == {"Acme ETN pumps (synthetic)"}


def _tender_folder(root: Path, name: str, shared_spec: Path) -> Path:
    folder = root / "tenders" / name
    folder.mkdir(parents=True)
    bid = write_text_pdf(folder / "bid.pdf", [[f"Bid Number: GEM/2026/B/{name}", "Gate valve"]])
    spec = folder / "buyer_spec.pdf"
    spec.write_bytes(shared_spec.read_bytes())
    boq = folder / "boq_items.csv"
    boq.write_text("Item,Qty\nGate valve 80 NB,2\n", encoding="utf-8")
    html = folder / "gem_category_spec.html"
    html.write_text("<html/>", encoding="utf-8")
    files = [
        {"file": "bid.pdf", "role": "bid_document", "url": None, "sha256": _sha(bid)},
        {
            "file": spec.name,
            "role": "buyer_spec",
            "url": "https://example.invalid/s.pdf",
            "sha256": _sha(spec),
            "found_in": "bid.pdf",
            "depth": 1,
        },
        {"file": boq.name, "role": "boq_items", "url": None, "sha256": _sha(boq)},
        {"file": html.name, "role": "gem_category_spec", "url": None, "sha256": _sha(html)},
        {
            "file": None,
            "role": "mii_mse_doc",
            "url": "https://example.invalid/m.pdf",
            "note": "empty response; not stored",
        },
    ]
    manifest = {"bid_number": f"GEM/2026/B/{name}", "item_category": "Gate valve", "files": files}
    (folder / "tender.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return folder


def test_tender_ingest_stores_every_linked_file(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    shared = write_text_pdf(tmp_path / "spec.pdf", [["Body: SS 316", "Pressure: PN 16"]])
    t1 = ingest_tender_folder(db_session, store, _tender_folder(tmp_path, "1", shared), options)
    t2 = ingest_tender_folder(db_session, store, _tender_folder(tmp_path, "2", shared), options)

    statuses = {Path(o.file_ref).name: o.status for o in t1.files}
    assert statuses == {
        "bid.pdf": IngestStatus.INGESTED,
        "buyer_spec.pdf": IngestStatus.INGESTED,
        "boq_items.csv": IngestStatus.INGESTED,
        "gem_category_spec.html": IngestStatus.SKIPPED_UNSUPPORTED,
    }
    # The same spec bytes in tender 2 are stored once and linked to both tenders.
    spec_2 = next(o for o in t2.files if o.file_ref.endswith("buyer_spec.pdf"))
    assert spec_2.status == IngestStatus.SKIPPED_EXISTING
    links = TenderRepository(db_session).list_documents(t1.tender_id)
    assert {link.role for link in links} == {"bid_document", "buyer_spec", "boq_items"}
    spec_link = next(link for link in links if link.role == "buyer_spec")
    assert (spec_link.found_in, spec_link.depth) == ("bid.pdf", 1)

    chunks = ChunkRepository(db_session).list_for_tender(t2.tender_id)
    assert any("ss316" in c.search_text for c in chunks)
    assert any("Gate valve 80 NB" in c.raw_text for c in chunks)
    tender = TenderRepository(db_session).get_or_raise(t1.tender_id)
    assert tender.page_count == 1
    assert tender.bid_number == "GEM/2026/B/1"


def test_tender_reingest_adds_nothing(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    shared = write_text_pdf(tmp_path / "spec.pdf", [["Body: SS 316"]])
    folder = _tender_folder(tmp_path, "3", shared)
    first = ingest_tender_folder(db_session, store, folder, options)
    again = ingest_tender_folder(db_session, store, folder, options)
    assert again.created is False
    assert again.tender_id == first.tender_id
    assert all(o.status != IngestStatus.INGESTED for o in again.files)
    assert len(TenderRepository(db_session).list_documents(first.tender_id)) == 3


def test_changed_file_is_rejected(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    shared = write_text_pdf(tmp_path / "spec.pdf", [["Body: SS 316"]])
    folder = _tender_folder(tmp_path, "4", shared)
    (folder / "boq_items.csv").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(TenderParseError, match="sha256 mismatch"):
        ingest_tender_folder(db_session, store, folder, options)


def test_manufacturer_documents_require_manufacturer_fields(db_session: Session) -> None:
    db_session.add(
        Document(
            source_kind=DocumentSource.MANUFACTURER,
            title="missing fields",
            file_path="x.pdf",
            sha256="0" * 64,
            fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_reprocess_rebuilds_content_in_place(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    shared = write_text_pdf(tmp_path / "spec.pdf", [["Body: SS 316", "Pressure: PN 16"]])
    folders = [_tender_folder(tmp_path, n, shared) for n in ("5", "6")]
    first = [ingest_tender_folder(db_session, store, f, options) for f in folders]
    chunks = ChunkRepository(db_session)
    before = {
        o.document_id: chunks.count_for_document(o.document_id)
        for o in first[0].files
        if o.document_id
    }

    policy = ReprocessPolicy(enabled=True)
    again = [ingest_tender_folder(db_session, store, f, options, policy) for f in folders]

    spec_statuses = [
        next(o.status for o in t.files if o.file_ref.endswith("buyer_spec.pdf")) for t in again
    ]
    assert spec_statuses == [IngestStatus.REPROCESSED, IngestStatus.SKIPPED_EXISTING]
    after = {
        o.document_id: chunks.count_for_document(o.document_id)
        for o in again[0].files
        if o.document_id
    }
    assert after == before
    tenders = TenderRepository(db_session)
    assert all(len(tenders.list_documents(t.tender_id)) == 3 for t in again)


def test_ingest_stores_sections_and_chunk_categories(
    db_session: Session, tmp_path: Path, options: ProcessingOptions
) -> None:
    store = LocalFileStore(tmp_path)
    shared = write_text_pdf(tmp_path / "spec.pdf", [["Body: SS 316", "Pressure: PN 16"]])
    folder = _tender_folder(tmp_path, "7", shared)
    outcome = ingest_tender_folder(db_session, store, folder, options)
    sections = SectionRepository(db_session)
    chunks = ChunkRepository(db_session)
    for file in (o for o in outcome.files if o.document_id):
        assert file.document_id is not None
        stored = sections.list_for_document(file.document_id)
        assert stored
        assert [s.position for s in stored] == list(range(len(stored)))
        doc_chunks = chunks.list_for_document(file.document_id)
        assert all(c.section_id is not None and c.section_category for c in doc_chunks)
        assert sum(s.chunk_count for s in stored) == len(doc_chunks)

    # Rebuilding (backfill) and reprocessing replace sections instead of adding more.
    spec_id = next(o.document_id for o in outcome.files if o.file_ref.endswith("buyer_spec.pdf"))
    assert spec_id is not None
    count = len(sections.list_for_document(spec_id))
    document = DocumentRepository(db_session).get_or_raise(spec_id)
    assert rebuild_sections(db_session, document, options) == count
    ingest_tender_folder(db_session, store, folder, options, ReprocessPolicy(enabled=True))
    assert len(sections.list_for_document(spec_id)) == count
