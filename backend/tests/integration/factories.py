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
    LineItem,
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
from app.schemas.matching import LabelBasis
from app.schemas.requirements import ValueKind

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


def make_line_item(run: Run, *, position: int = 0, item_no: str = "1") -> LineItem:
    item = LineItem(
        position=position,
        item_no=item_no,
        title="test valve",
        document="test_tender.pdf",
        page_number=1,
    )
    run.line_items.append(item)
    return item


def make_requirement(
    run: Run,
    *,
    position: int,
    parent: Requirement | None = None,
    line_item: LineItem | None = None,
    ref: str = "1.1",
) -> Requirement:
    requirement = Requirement(
        parent=parent,
        ref=ref,
        position=position,
        document="test_tender.pdf",
        page_number=1,
        raw_text=f"requirement {position}",
        req_type=RequirementType.TECHNICAL,
        kind=ValueKind.NUMERIC,
        operator=Operator.GTE,
        value_num=10.0,
        unit="bar",
        weight=1.0,
    )
    run.requirements.append(requirement)
    if line_item is not None:
        line_item.requirements.append(requirement)
    return requirement


def make_run(tender: Tender, *, status: RunStatus = RunStatus.QUEUED) -> Run:
    return Run(tender_id=tender.id, category=CATEGORY, manufacturers=["AcmePumps"], status=status)


def make_result(
    run: Run,
    requirement: Requirement,
    *,
    manufacturer: str | None = "AcmePumps",
    catalogue: str | None = "test.pdf",
    tied: bool = False,
) -> MatchResult:
    return MatchResult(
        run_id=run.id,
        requirement_id=requirement.id,
        manufacturer=manufacturer,
        catalogue=catalogue,
        tied=tied,
        status=MatchStatus.FULL_MATCH,
        score=1.0,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
    )


def make_evidence(doc: Document, *, page: int = 1) -> Evidence:
    return Evidence(
        document_id=doc.id,
        page_number=page,
        label="synthetic label",
        value="synthetic value",
        quote="synthetic quote",
        match_score=1.0,
    )
