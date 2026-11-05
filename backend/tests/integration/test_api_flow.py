"""The API against Postgres: upload -> run -> status -> report, and how runs fail.

The PDFs and the report are synthetic. Matching is faked, so nothing here is real evidence;
everything else (routes, services, queue, database) is real.
"""

import hashlib
import uuid
from collections.abc import Callable, Iterator, Sequence
from datetime import UTC, datetime
from functools import cache
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.api.deps import get_manufacturer_service
from app.main import create_app
from app.schemas.enums import DecisionSource, DocType, MatchStatus, Operator
from app.schemas.matching import Comparison, Evidence, LabelBasis
from app.schemas.report import ItemReport, ManufacturerItemReport, Score, TenderReport
from app.schemas.requirements import LineItem, Requirement, ValueKind
from app.schemas.sources import RegisteredSource
from app.services.manufacturer_service import ManufacturerService
from app.services.run_service import Matcher, RunService
from tests.conftest import make_test_settings
from tests.integration.factories import make_document

BID = "GEM/2099/B/1234567"
SCORE = Score(compatibility_pct=None, coverage_pct=0, decided=0, total=0, needs_review=0, counts={})


def _pdf(bid_number: str | None = BID, pages: int = 2, extra: str = "") -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc[0].insert_text((72, 72), f"Bid Number: {bid_number or 'none'} {extra}", fontsize=10)
    content = bytes(doc.tobytes())
    doc.close()
    return content


PDF = _pdf()  # generated once: PyMuPDF writes different bytes each time


def _source(manufacturer: str = "Acme") -> RegisteredSource:
    return RegisteredSource(
        manufacturer=manufacturer,
        category="valves_pumps",
        product_types=["ball_valve", "gate_valve"],
        sub_types=[],
        title="Synthetic catalogue",
        doc_type=DocType.CATALOGUE,
        url="https://example.invalid/c.pdf",  # type: ignore[arg-type]
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        publish_date=None,
        sha256="c" * 64,
        path=Path("c.pdf"),
        registry_file=Path("sources.yaml"),
    )


def _report(citing: str | None = None) -> TenderReport:
    evidence = (
        [
            Evidence(
                document="c.pdf",
                sha256=citing,
                title="c",
                page_number=1,
                label="Body",
                value="WCB",
                quote="Body: WCB",
                quote_parts=["Body", "WCB"],
            )
        ]
        if citing
        else []
    )
    result = Comparison(
        requirement_id="1.1",
        status=MatchStatus.FULL_MATCH if citing else MatchStatus.UNSUPPORTED,
        needs_review=True,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="why",
        evidence=evidence,
    )
    requirement = Requirement(
        id="1.1",
        item_no="1",
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text="line",
        document="bid.pdf",
        page_number=1,
    )
    item = ItemReport(
        item=LineItem(
            item_no="1", title="BALL VALVE", description_lines=[], document="bid.pdf", page_number=1
        ),
        category=None,
        product_type=None,
        requirements=[requirement],
        manufacturers=[
            ManufacturerItemReport(
                manufacturer="Acme", chosen=None, candidates=[], results=[result], score=SCORE
            )
        ],
    )
    return TenderReport(
        tender="T",
        bid_number=BID,
        tender_files=[],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ontology_used=True,
        items=[item],
        ranking=[],
    )


class _Inline:
    """Runs jobs at once, so a test sees the finished run."""

    def enqueue[T](self, job: Callable[[T], None], argument: T) -> None:
        job(argument)


class _Hold:
    """Never runs jobs, so a test sees the queued run."""

    def enqueue[T](self, job: Callable[[T], None], argument: T) -> None:
        return None


class _Matcher:
    def __init__(self, report: TenderReport | Exception) -> None:
        self.report = report
        self.calls: list[tuple[str, Sequence[str] | None, Sequence[str]]] = []

    def match_tender(
        self,
        tender: str,
        *,
        use_ontology: bool = True,
        manufacturers: Sequence[str] | None = None,
        unfetched: Sequence[str] = (),
    ) -> TenderReport:
        self.calls.append((tender, manufacturers, unfetched))
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


@pytest.fixture
def app(tmp_path: Path, db_engine: Engine) -> Iterator[FastAPI]:
    settings = make_test_settings().model_copy(
        update={
            "FILE_STORE_ROOT": tmp_path,
            "TENDER_DATA_DIR": tmp_path / "tenders",
            "TENDER_UPLOAD_MAX_MB": 1,
        }
    )
    application = create_app(settings)
    service = ManufacturerService([_source()])
    application.dependency_overrides[get_manufacturer_service] = lambda: service
    yield application
    with db_engine.begin() as conn:
        conn.execute(text("DELETE FROM tenders"))
        conn.execute(text("DELETE FROM documents"))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _use(
    app: FastAPI,
    matcher: Matcher,
    queue: _Inline | _Hold | None = None,
    unfetched: Sequence[str] = (),
) -> None:
    service = ManufacturerService([_source()])
    app.state.run_service = RunService(
        app.state.session_factory,
        make_test_settings(),
        queue or _Inline(),
        cache(lambda: matcher),
        lambda _: list(unfetched),
        lambda: service,
    )


def _upload(client: TestClient, content: bytes | None = None) -> str:
    response = client.post("/api/v1/tenders", files={"file": ("bid.pdf", content or PDF)})
    assert response.status_code == 201, response.text
    tender_id: str = response.json()["tender_id"]
    return tender_id


def _start(client: TestClient, tender_id: str) -> dict[str, object]:
    response = client.post(
        "/api/v1/runs",
        json={"tender_id": tender_id, "category": "valves_pumps", "manufacturers": ["Acme"]},
    )
    assert response.status_code == 202, response.text
    body: dict[str, object] = response.json()
    return body


def test_manufacturers_list_what_catalogues_cover(client: TestClient) -> None:
    response = client.get("/api/v1/manufacturers")
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "Acme",
            "category": "valves_pumps",
            "product_types": ["ball_valve", "gate_valve"],
            "documents": 1,
        }
    ]


def test_upload_stores_the_file_manifest_and_tender(client: TestClient, tmp_path: Path) -> None:
    content = _pdf(pages=3)
    response = client.post("/api/v1/tenders", files={"file": ("mine.pdf", content)})
    assert response.status_code == 201
    body = response.json()
    assert (body["filename"], body["pages"], body["bid_number"]) == ("mine.pdf", 3, BID)
    assert body["created"] is True
    folder = tmp_path / "tenders" / "GEM_2099_B_1234567"
    assert (folder / "bid.pdf").read_bytes() == content
    assert (folder / "tender.yaml").is_file()


def test_uploading_the_same_file_reuses_its_tender(client: TestClient) -> None:
    first = client.post("/api/v1/tenders", files={"file": ("bid.pdf", PDF)}).json()
    second = client.post("/api/v1/tenders", files={"file": ("bid.pdf", PDF)}).json()
    assert second["tender_id"] == first["tender_id"]
    assert second["created"] is False


def test_a_different_file_for_the_same_bid_is_a_new_revision_and_the_old_run_stays(
    app: FastAPI, client: TestClient, tmp_path: Path
) -> None:
    matcher = _Matcher(_report())
    _use(app, matcher)
    first_id = _upload(client)
    first_run = _start(client, first_id)["run_id"]
    report_before = client.get(f"/api/v1/reports/{first_run}").json()
    assert report_before["tender_revision"] == 1

    other = _pdf(extra="corrigendum")
    response = client.post("/api/v1/tenders", files={"file": ("bid.pdf", other)})
    assert response.status_code == 201
    body = response.json()
    assert (body["bid_number"], body["revision"], body["created"]) == (BID, 2, True)
    assert body["tender_id"] != first_id

    folder = tmp_path / "tenders"
    assert (folder / "GEM_2099_B_1234567" / "bid.pdf").read_bytes() == PDF
    assert (folder / "GEM_2099_B_1234567_r2" / "bid.pdf").read_bytes() == other
    assert (folder / "GEM_2099_B_1234567_r2" / "tender.yaml").is_file()

    second_run = _start(client, body["tender_id"])["run_id"]
    assert matcher.calls[-1][0] == "GEM_2099_B_1234567_r2"
    report = client.get(f"/api/v1/reports/{second_run}").json()
    assert report["tender_revision"] == 2
    assert report["bid_sha256"] == hashlib.sha256(other).hexdigest()
    assert client.get(f"/api/v1/reports/{first_run}").json() == report_before


def test_uploading_an_old_revision_again_reuses_it(client: TestClient) -> None:
    first_id = _upload(client)
    other = _pdf(extra="corrigendum")
    client.post("/api/v1/tenders", files={"file": ("bid.pdf", other)})
    again = client.post("/api/v1/tenders", files={"file": ("bid.pdf", PDF)}).json()
    assert (again["tender_id"], again["revision"], again["created"]) == (first_id, 1, False)


def test_a_file_on_disk_is_never_overwritten_even_without_its_row(
    client: TestClient, tmp_path: Path
) -> None:
    orphan = tmp_path / "tenders" / "GEM_2099_B_1234567" / "bid.pdf"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"%PDF an older copy")
    body = client.post("/api/v1/tenders", files={"file": ("bid.pdf", PDF)}).json()
    assert body["revision"] == 2
    assert orphan.read_bytes() == b"%PDF an older copy"


def test_a_file_that_is_not_a_pdf_is_refused(client: TestClient) -> None:
    response = client.post("/api/v1/tenders", files={"file": ("t.txt", b"hello")})
    assert response.status_code == 422
    assert "not a PDF" in response.json()["error"]["message"]


def test_catalogue_warm_up_goes_through_the_run_queue(app: FastAPI) -> None:
    warmed: list[int] = []

    def warm_up() -> int:
        warmed.append(1)
        return 7

    def service(warm_up: Callable[[], int] | None) -> RunService:
        return RunService(
            app.state.session_factory,
            make_test_settings(),
            _Inline(),
            cache(lambda: _Matcher(_report())),
            lambda _: [],
            lambda: ManufacturerService([]),
            warm_up,
        )

    assert service(warm_up).warm_catalogues() is True
    assert warmed == [1]
    assert service(None).warm_catalogues() is False


def test_excel_downloads_for_a_finished_run_only(app: FastAPI, client: TestClient) -> None:
    _use(app, _Matcher(ValueError("broken")))
    failed = _start(client, _upload(client))["run_id"]
    response = client.get(f"/api/v1/reports/{failed}/excel")
    assert response.status_code == 409
    assert "broken" in response.json()["error"]["details"]["error"]

    _use(app, _Matcher(_report()))
    done = _start(client, _upload(client))["run_id"]
    response = client.get(f"/api/v1/reports/{done}/excel")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert f"_{done}.xlsx" in response.headers["content-disposition"]
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames == ["Summary", "Requirements", "By status", "General"]


def test_a_stored_document_opens_inline(
    app: FastAPI, client: TestClient, tmp_path: Path, db_engine: Engine
) -> None:
    (tmp_path / "cat.pdf").write_bytes(PDF)
    with Session(db_engine) as session:
        document = make_document()
        document.file_path = "cat.pdf"
        session.add(document)
        session.commit()
        sha256 = document.sha256
    response = client.get(f"/api/v1/documents/{sha256}/file")
    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'inline; filename="cat.pdf"'


@pytest.mark.parametrize(
    ("sha256", "code"), [("f" * 64, 404), ("..%2F..%2Fsecret", 404), ("ABC", 422)]
)
def test_only_a_stored_hash_gives_a_file(client: TestClient, sha256: str, code: int) -> None:
    assert client.get(f"/api/v1/documents/{sha256}/file").status_code == code


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"not a pdf", "invalid_input"),
        (b"%PDF" + b"0" * (1024 * 1024), "invalid_input"),  # over the limit
        (_pdf(bid_number=None), "tender_parse_error"),
    ],
    ids=["not_a_pdf", "too_large", "no_bid_number"],
)
def test_an_unusable_upload_is_refused(client: TestClient, content: bytes, code: str) -> None:
    response = client.post("/api/v1/tenders", files={"file": ("bid.pdf", content)})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code


def test_run_goes_from_upload_to_report(app: FastAPI, client: TestClient) -> None:
    matcher = _Matcher(_report())
    _use(app, matcher)
    tender_id = _upload(client)
    run_id = _start(client, tender_id)["run_id"]

    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["status"] == "done"
    assert status["error"] is None
    assert status["started_at"] is not None
    assert status["finished_at"] is not None
    assert matcher.calls == [("GEM_2099_B_1234567", ["Acme"], [])]

    report = client.get(f"/api/v1/reports/{run_id}")
    assert report.status_code == 200
    got = TenderReport.model_validate(report.json())
    assert got.tender_revision == 1
    assert got.model_copy(update={"tender_revision": None, "bid_sha256": None}) == _report()
    assert [r["run_id"] for r in client.get("/api/v1/runs").json()] == [run_id]


def test_files_that_could_not_be_fetched_reach_matching(app: FastAPI, client: TestClient) -> None:
    matcher = _Matcher(_report())
    _use(app, matcher, unfetched=["https://example.invalid/spec.pdf"])
    _start(client, _upload(client))
    assert matcher.calls[0][2] == ["https://example.invalid/spec.pdf"]


def test_a_queued_run_has_no_report_yet(app: FastAPI, client: TestClient) -> None:
    _use(app, _Matcher(_report()), _Hold())
    run_id = _start(client, _upload(client))["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["status"] == "queued"
    response = client.get(f"/api/v1/reports/{run_id}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["status"] == "queued"


def test_a_crash_while_matching_fails_the_run_with_the_reason(
    app: FastAPI, client: TestClient
) -> None:
    _use(app, _Matcher(ValueError("page 7 is broken")))
    run_id = _start(client, _upload(client))["run_id"]

    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["status"] == "failed"
    assert status["error"] == "ValueError: page 7 is broken"
    assert status["finished_at"] is not None


def test_a_refused_save_fails_the_run_and_stores_nothing(
    app: FastAPI, client: TestClient, db_engine: Engine
) -> None:
    missing = "f" * 64
    _use(app, _Matcher(_report(citing=missing)))
    run_id = _start(client, _upload(client))["run_id"]

    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["status"] == "failed"
    assert "cited catalogues are not stored" in status["error"]
    assert missing in status["error"]

    response = client.get(f"/api/v1/reports/{run_id}")
    assert response.status_code == 409
    details = response.json()["error"]["details"]
    assert details["status"] == "failed"
    assert missing in details["error"]
    with db_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM requirements")) == 0
        assert conn.scalar(text("SELECT count(*) FROM line_items")) == 0


def test_a_run_of_an_unknown_tender_is_not_found(app: FastAPI, client: TestClient) -> None:
    _use(app, _Matcher(_report()))
    response = client.post(
        "/api/v1/runs",
        json={
            "tender_id": str(uuid.uuid7()),
            "category": "valves_pumps",
            "manufacturers": ["Acme"],
        },
    )
    assert response.status_code == 404


def test_a_manufacturer_without_catalogues_is_refused(app: FastAPI, client: TestClient) -> None:
    _use(app, _Matcher(_report()))
    response = client.post(
        "/api/v1/runs",
        json={
            "tender_id": _upload(client),
            "category": "valves_pumps",
            "manufacturers": ["Nobody"],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["details"]["unknown"] == ["Nobody"]
    assert client.get("/api/v1/runs").json() == []


def test_an_empty_manufacturer_list_is_refused(app: FastAPI, client: TestClient) -> None:
    response = client.post(
        "/api/v1/runs",
        json={"tender_id": _upload(client), "category": "valves_pumps", "manufacturers": []},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/api/v1/runs", "/api/v1/reports"])
def test_an_unknown_run_is_not_found(client: TestClient, path: str) -> None:
    assert client.get(f"{path}/{uuid.uuid7()}").status_code == 404


def test_a_restart_fails_runs_it_lost(app: FastAPI, client: TestClient) -> None:
    _use(app, _Matcher(_report()), _Hold())
    run_id = _start(client, _upload(client))["run_id"]
    service: RunService = app.state.run_service

    assert service.fail_interrupted() == 1
    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert (status["status"], status["error"]) == (
        "failed",
        "The server restarted before this run finished",
    )
    assert service.fail_interrupted() == 0
