"""Builders for ORM objects used in DB tests.

All names, URLs and quotes here are synthetic test data ("Acme" etc.), not real catalogue
content, and must never be presented as evidence.
"""

import uuid
from datetime import UTC, datetime

from app.db.models import (
    Chunk,
    Document,
    Evidence,
    MatchResult,
    Page,
    Requirement,
    Run,
    SpecFact,
    Tender,
)
from app.schemas.enums import (
    ChunkType,
    DecisionSource,
    DocType,
    DocumentSource,
    MatchStatus,
    Operator,
    RequirementType,
    RunStatus,
)

CATEGORY = "valves_pumps"
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)


def make_document(
    *, manufacturer: str = "AcmePumps", category: str = CATEGORY, pages: int = 2
) -> Document:
    doc = Document(
        source_kind=DocumentSource.MANUFACTURER,
        manufacturer=manufacturer,
        category=category,
        title=f"{manufacturer} test datasheet",
        doc_type=DocType.DATASHEET,
        url="https://example.invalid/test.pdf",
        file_path=f"data/manufacturers/{manufacturer}/test.pdf",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        fetched_at=FIXED_TIME,
        publish_date=None,
        page_count=pages,
    )
    doc.pages = [
        Page(page_number=n, text=f"synthetic page {n}", char_count=16) for n in range(1, pages + 1)
    ]
    return doc


def make_chunk(doc: Document, page: Page, *, position: int = 0, text: str = "c") -> Chunk:
    return Chunk(
        document=doc,
        page_id=page.id,
        page_number=page.page_number,
        position=position,
        chunk_type=ChunkType.PROSE,
        raw_text=text,
        search_text=text.lower(),
    )


def make_spec_fact(doc: Document, *, attribute: str, value_text: str, page: int = 1) -> SpecFact:
    return SpecFact(
        document_id=doc.id,
        page_number=page,
        manufacturer=doc.manufacturer,
        category=doc.category,
        product_family="TestFamily",
        attribute=attribute,
        value_text=value_text,
        quote=f"{attribute}: {value_text}",
        quote_match_score=1.0,
    )


def make_tender() -> Tender:
    return Tender(
        filename="test_tender.pdf",
        file_path="uploads/test_tender.pdf",
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        category=CATEGORY,
    )


def make_requirement(
    tender: Tender, *, position: int, parent: Requirement | None = None
) -> Requirement:
    return Requirement(
        tender=tender,
        parent=parent,
        position=position,
        page_number=1,
        raw_text=f"requirement {position}",
        req_type=RequirementType.TECHNICAL,
        operator=Operator.GTE,
        value_num=10.0,
        unit="bar",
        weight=1.0,
    )


def make_run(tender: Tender, *, status: RunStatus = RunStatus.QUEUED) -> Run:
    return Run(tender_id=tender.id, category=CATEGORY, manufacturers=["AcmePumps"], status=status)


def make_result(
    run: Run, requirement: Requirement, *, manufacturer: str = "AcmePumps"
) -> MatchResult:
    return MatchResult(
        run_id=run.id,
        requirement_id=requirement.id,
        manufacturer=manufacturer,
        status=MatchStatus.FULL_MATCH,
        score=1.0,
        confidence=0.9,
        decided_by=DecisionSource.RULE,
    )


def make_evidence(doc: Document, *, page: int = 1) -> Evidence:
    return Evidence(document_id=doc.id, page_number=page, quote="synthetic quote", match_score=1.0)
