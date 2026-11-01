"""Use case: keep a finished report, as rows to query and as the report itself."""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import InvalidInputError, NotFoundError
from app.db.repositories import DocumentRepository, ResultRepository, RunRepository
from app.reporting.report_rows import RowContext, cited_hashes, report_rows
from app.schemas.enums import MatchStatus
from app.schemas.report import TenderReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SavedCounts:
    """How many rows one report gave."""

    line_items: int
    requirements: int
    results: int


class ReportService:
    """Store reports. The caller's unit of work owns the transaction, so a save is all or none."""

    def __init__(
        self,
        runs: RunRepository,
        results: ResultRepository,
        documents: DocumentRepository,
        status_scores: Mapping[MatchStatus, float],
    ) -> None:
        self._runs = runs
        self._results = results
        self._documents = documents
        self._status_scores = status_scores

    def save(self, run_id: uuid.UUID, report: TenderReport) -> SavedCounts:
        """Store one run's report, with its line items, requirements, results and evidence.

        Args:
            run_id: The run the report belongs to.
            report: The finished report.

        Returns:
            How many rows were written.

        Raises:
            NotFoundError: If the run does not exist, or a cited catalogue is not stored: evidence
                must point to a real document.
            InvalidInputError: If this run already has a report.
        """
        run = self._runs.get_or_raise(run_id)
        if run.report is not None:
            raise InvalidInputError(
                f"Run {run_id} already has a report", details={"run_id": str(run_id)}
            )
        hashes = cited_hashes(report)
        document_ids = self._documents.ids_by_sha256(hashes)
        missing = sorted(hashes - document_ids.keys())
        if missing:
            raise NotFoundError(
                f"{len(missing)} cited catalogues are not stored", details={"sha256": missing}
            )
        rows = report_rows(report, RowContext(run_id, document_ids, self._status_scores))
        self._runs.add_rows(rows.line_items, rows.requirements)
        self._results.add_all(rows.results)
        run.report = report.model_dump(mode="json")
        self._runs.flush()
        counts = SavedCounts(len(rows.line_items), len(rows.requirements), len(rows.results))
        logger.info("report saved", extra={"run_id": str(run_id), "results": counts.results})
        return counts


def build_report_service(session: Session, settings: Settings) -> ReportService:
    """Wire the report service for one session.

    Args:
        session: The unit of work.
        settings: Application settings (status scores).

    Returns:
        A ready service.
    """
    return ReportService(
        RunRepository(session),
        ResultRepository(session),
        DocumentRepository(session),
        {MatchStatus(k): v for k, v in settings.STATUS_SCORES.items()},
    )
