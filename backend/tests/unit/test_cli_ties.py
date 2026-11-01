"""The match-tender summary on a tie. The report is synthetic; nothing is matched."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app import cli
from app.core.config import Settings
from app.matching.scorer import score
from app.schemas.enums import (
    DecisionSource,
    ExtractionIssueCode,
    ExtractionStatus,
    MatchStatus,
)
from app.schemas.matching import Comparison, LabelBasis, SubTypeFit
from app.schemas.report import (
    CandidateSummary,
    ExtractionIssue,
    ExtractionQuality,
    ItemReport,
    ManufacturerItemReport,
    TenderReport,
    TiedDocumentRows,
)
from app.schemas.requirements import LineItem
from tests.conftest import make_test_settings

SCORES = {MatchStatus.FULL_MATCH: 1.0, MatchStatus.PARTIAL_MATCH: 0.5}
ROW = Comparison(
    requirement_id="1.1",
    status=MatchStatus.FULL_MATCH,
    needs_review=True,
    decided_by=DecisionSource.RULE,
    basis=LabelBasis.ATTRIBUTE,
    reason="catalogues tie, none picked: alpha.pdf, beta.pdf",
)


def _candidate(document: str) -> CandidateSummary:
    return CandidateSummary(
        document=document,
        sha256="a" * 64,
        title=document,
        sub_type_fit=SubTypeFit.UNKNOWN,
        fit_score=1.0,
        decided=1,
    )


def _report() -> TenderReport:
    manufacturer = ManufacturerItemReport(
        manufacturer="Acme",
        chosen=None,
        candidates=[_candidate("alpha.pdf"), _candidate("beta.pdf")],
        tied_documents=["alpha.pdf", "beta.pdf"],
        tied_rows=[
            TiedDocumentRows(document="alpha.pdf", results=[ROW]),
            TiedDocumentRows(document="beta.pdf", results=[ROW]),
        ],
        results=[ROW],
        score=score([ROW], SCORES),
    )
    item = ItemReport(
        item=LineItem(
            item_no="1", title="VALVE", description_lines=[], document="b.pdf", page_number=1
        ),
        category=None,
        product_type="ball_valve",
        requirements=[],
        manufacturers=[manufacturer],
    )
    return TenderReport(
        tender="T",
        bid_number="B",
        tender_files=[],
        generated_at=datetime(2026, 1, 1, tzinfo=UTC),
        ontology_used=True,
        items=[item],
        ranking=[],
    )


class _Service:
    def match_tender(self, tender: str, *, use_ontology: bool = True) -> TenderReport:
        return _report()


def test_match_tender_shows_a_tie_with_its_shared_score(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "build_match_service", lambda settings: _Service())
    settings = Settings(_env_file=None, DATABASE_URL="postgresql+psycopg://u:p@localhost/db")  # type: ignore[call-arg]
    code = cli.run_match(settings, "T", use_ontology=True, out=str(tmp_path / "r.json"))
    line = next(x for x in capsys.readouterr().out.splitlines() if "Acme" in x)
    assert code == 0
    assert "TIED, needs review: alpha.pdf, beta.pdf" in line
    assert "100" in line  # the score the tied catalogues share, not a blank


def test_match_tender_prints_the_extraction_warning_first(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    quality = ExtractionQuality(
        status=ExtractionStatus.UNRELIABLE,
        reasons=[ExtractionIssue(code=ExtractionIssueCode.OVERSIZED_ITEM, reason="item 1 is big")],
    )

    class _Flagged:
        def match_tender(self, tender: str, *, use_ontology: bool = True) -> TenderReport:
            return _report().model_copy(update={"extraction_quality": quality})

    monkeypatch.setattr(cli, "build_match_service", lambda settings: _Flagged())
    cli.run_match(make_test_settings(), "T", use_ontology=True, out=str(tmp_path / "r.json"))
    first, second, *_ = capsys.readouterr().out.splitlines()
    assert first.startswith("WARNING: extraction unreliable")
    assert second == "  oversized_item: item 1 is big"
