"""Use case: let the LLM judge decide what rules left open in a tender's items.

Runs after the catalogue is chosen, so the judge never changes which document an item uses, and
never mixes documents (B10). Retrieval comes first; with no evidence hit the LLM is not called.
A run has a call limit and stops calling after repeated failures, so an exhausted quota costs
some `unsupported` results instead of the run.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError
from app.db.models import Chunk
from app.db.repositories import ChunkRepository, EmbeddingRepository
from app.db.session import session_scope
from app.llm.base import LLMClient
from app.llm.factory import build_llm_client
from app.matching.comparator import ComparatorOptions, needs_judge
from app.matching.engine import ItemInput
from app.matching.evidence_verifier import PageTexts, verify_comparison
from app.matching.llm_judge import (
    JudgedItems,
    Passage,
    build_prompt,
    judged_comparison,
    undecided,
)
from app.matching.scorer import score
from app.ontology.loader import load_ontologies
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.query_builder import QueryVocabulary
from app.retrieval.reranker import Reranker, build_reranker
from app.retrieval.retriever import RetrievalParams, Retriever
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.enums import MatchStatus
from app.schemas.matching import Comparison, JudgeVerdict
from app.schemas.report import CandidateSummary, ItemReport, ManufacturerItemReport
from app.schemas.requirements import Requirement
from app.schemas.retrieval import RetrievalOutcome, RetrievalRequest
from app.tender.splitter import leaves

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FoundEvidence:
    """What retrieval found in one catalogue for one requirement."""

    outcome: RetrievalOutcome
    passages: tuple[Passage, ...] = ()


class EvidenceFinder(Protocol):
    """Finds passages for a requirement."""

    def find(self, request: RetrievalRequest) -> FoundEvidence:
        """Search the catalogue the request names."""
        ...


@dataclass(frozen=True, slots=True)
class JudgeOptions:
    """Limits and comparator settings of the judge."""

    comparator: ComparatorOptions
    status_scores: dict[MatchStatus, float]
    quote_threshold: float
    max_calls: int
    max_failures: int


@dataclass(frozen=True, slots=True)
class _Decision:
    """A verdict with what it may cite, or a note saying why there is none."""

    verdict: JudgeVerdict | None = None
    passages: tuple[Passage, ...] = ()
    note: str = ""


@dataclass(slots=True)
class _Progress:
    """One tender's calls, failures and verdicts (a repeated line is judged once)."""

    calls: int = 0
    failed: int = 0
    failures_in_a_row: int = 0
    decisions: dict[tuple[str, str, str], _Decision] = field(default_factory=dict)


def _passage(chunk: Chunk) -> Passage:
    """A stored chunk as the judge reads it."""
    return Passage(
        chunk_id=str(chunk.id),
        document=PurePosixPath(chunk.document.file_path).name,
        sha256=chunk.document.sha256,
        title=chunk.document.title,
        page_number=chunk.page_number,
        raw_text=chunk.raw_text,
        section=chunk.section,
    )


@dataclass(frozen=True, slots=True)
class DbEvidenceFinder:
    """Retrieval over the stored chunks, in one short transaction per requirement."""

    session_factory: sessionmaker[Session]
    embedder: Embedder
    reranker: Reranker
    vocabulary: QueryVocabulary
    params: RetrievalParams

    def find(self, request: RetrievalRequest) -> FoundEvidence:
        """Search one catalogue; only hits that count as evidence are returned.

        Args:
            request: The requirement and the catalogue to search.

        Returns:
            The outcome and the accepted passages, best first.

        Raises:
            ExternalServiceError: If the embedder or reranker fails.
        """
        with session_scope(self.session_factory) as session:
            chunks = ChunkRepository(session)
            retriever = Retriever(
                chunks=chunks,
                embeddings=EmbeddingRepository(session),
                embedder=self.embedder,
                reranker=self.reranker,
                vocabulary=self.vocabulary,
                params=self.params,
            )
            result = retriever.retrieve(request)
            ids = [hit.chunk_id for hit in retriever.evidence_hits(result)]
            found = {c.id: c for c in chunks.list_by_ids(ids)}
            return FoundEvidence(result.outcome, tuple(_passage(found[i]) for i in ids))


class JudgeService:
    """Decide free-text and unlabelled requirements with retrieval and the LLM."""

    def __init__(self, llm: LLMClient, finder: EvidenceFinder, options: JudgeOptions) -> None:
        self._llm = llm
        self._finder = finder
        self._options = options

    def judge_items(
        self, inputs: Sequence[ItemInput], reports: Sequence[ItemReport], pages: PageTexts
    ) -> JudgedItems:
        """Judge what rules left open in one tender's items.

        Args:
            inputs: The items, in the same order as `reports`.
            reports: Their rule results.
            pages: Page text of the catalogues, for quote verification.

        Returns:
            The reports with results and scores updated, and how they were decided.
        """
        progress = _Progress()
        judged = [
            report.model_copy(
                update={
                    "manufacturers": [
                        self._manufacturer(entry, report, m, pages, progress)
                        for m in report.manufacturers
                    ]
                }
            )
            for entry, report in zip(inputs, reports, strict=True)
        ]
        logger.info("judge done", extra={"calls": progress.calls, "failed": progress.failed})
        if not progress.calls:
            return JudgedItems(judged, "rules")
        summary = f"{progress.calls} calls, {progress.failed} failed"
        return JudgedItems(judged, f"rules + {self._llm.model_name} ({summary})")

    def _manufacturer(
        self,
        entry: ItemInput,
        report: ItemReport,
        result: ManufacturerItemReport,
        pages: PageTexts,
        progress: _Progress,
    ) -> ManufacturerItemReport:
        """One manufacturer's results; only a chosen catalogue is judged, never a tie."""
        if result.chosen is None or report.category is None or report.product_type is None:
            return result
        chosen, category, product = result.chosen, report.category, report.product_type
        comparisons: list[Comparison] = []
        for requirement, comparison in zip(leaves(entry.requirements), result.results, strict=True):
            if comparison.status is MatchStatus.UNSUPPORTED and needs_judge(requirement):
                request = RetrievalRequest(
                    manufacturer=result.manufacturer,
                    category=category,
                    product_type=product,
                    text=requirement.raw_text,
                    attribute=requirement.attribute,
                    document_sha256=chosen.sha256,
                )
                comparison = self._judged(entry, requirement, request, chosen, pages, progress)
            comparisons.append(comparison)
        return result.model_copy(
            update={
                "results": comparisons,
                "score": score(comparisons, self._options.status_scores),
            }
        )

    def _judged(
        self,
        entry: ItemInput,
        requirement: Requirement,
        request: RetrievalRequest,
        chosen: CandidateSummary,
        pages: PageTexts,
        progress: _Progress,
    ) -> Comparison:
        """One requirement's result; a line seen before in this catalogue reuses its verdict."""
        key = (chosen.sha256, chosen.product_heading or "", requirement.raw_text)
        decision = progress.decisions.get(key)
        if decision is None:
            decision = self._decide(requirement, request, chosen.product_heading, progress)
            progress.decisions[key] = decision
        if decision.verdict is None:
            return undecided(requirement, decision.note)
        options = self._options
        result = judged_comparison(
            requirement, decision.verdict, decision.passages, entry.ctx, options.comparator
        )
        return verify_comparison(result, pages, options.quote_threshold)

    def _decide(
        self,
        requirement: Requirement,
        request: RetrievalRequest,
        heading: str | None,
        progress: _Progress,
    ) -> _Decision:
        """Retrieve, then ask the model; failures and limits become a note, never an error."""
        options = self._options
        if progress.failures_in_a_row >= options.max_failures:
            return _Decision(note="LLM judge stopped: too many failures in a row")
        try:
            found = self._finder.find(request)
            if not found.passages:
                return _Decision(note=f"no evidence in this catalogue ({found.outcome.value})")
            if progress.calls >= options.max_calls:
                return _Decision(note="LLM judge skipped: call limit for this run reached")
            progress.calls += 1
            prompt = build_prompt(requirement, request.product_type, heading, found.passages)
            verdict = self._llm.generate_json(prompt, JudgeVerdict)
        except ExternalServiceError as exc:
            progress.failed += 1
            progress.failures_in_a_row += 1
            logger.warning("judge failed", extra={"error": exc.message})
            return _Decision(note=f"LLM judge failed: {exc.message}")
        progress.failures_in_a_row = 0
        return _Decision(verdict=verdict, passages=found.passages)


def build_judge_service(settings: Settings, session_factory: sessionmaker[Session]) -> JudgeService:
    """Wire the judge from settings; models load on first use.

    Args:
        settings: Application settings.
        session_factory: Opens the transactions retrieval reads in.

    Returns:
        A ready service.

    Raises:
        InvalidInputError: If the LLM provider's API key is not configured.
    """
    ontologies = load_ontologies(settings.ONTOLOGY_DIR)
    vocabulary = QueryVocabulary.from_ontologies(
        ontologies,
        SearchVocabulary.from_ontologies(ontologies),
        max_distance=settings.QUERY_TYPO_MAX_DISTANCE,
        min_length=settings.QUERY_TYPO_MIN_LENGTH,
    )
    return JudgeService(
        build_llm_client(settings),
        DbEvidenceFinder(
            session_factory,
            build_embedder(settings),
            build_reranker(settings),
            vocabulary,
            RetrievalParams.from_settings(settings),
        ),
        JudgeOptions(
            comparator=ComparatorOptions.from_settings(settings),
            status_scores={MatchStatus(k): v for k, v in settings.STATUS_SCORES.items()},
            quote_threshold=settings.QUOTE_MATCH_THRESHOLD,
            max_calls=settings.LLM_JUDGE_MAX_CALLS,
            max_failures=settings.LLM_JUDGE_MAX_FAILURES,
        ),
    )
