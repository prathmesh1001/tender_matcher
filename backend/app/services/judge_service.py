"""Use case: let the LLM judge decide what rules left open in a tender's items.

Runs after the catalogue is chosen, so the judge never changes which document an item uses, and
never mixes documents (B10). Retrieval comes first; with no evidence hit the LLM is not called.
A run has a call limit and stops calling after repeated failures, so an exhausted quota costs
some `unsupported` results instead of the run. Answers are cached by prompt and model, so a rerun
gives the same results and calls nothing; failures are never cached.
"""

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError
from app.db.models import Chunk
from app.db.repositories import ChunkRepository, EmbeddingRepository, JudgeAnswerRepository
from app.db.session import session_scope
from app.llm.base import LLMClient
from app.llm.factory import build_llm_client
from app.matching.comparator import ComparatorOptions, needs_judge
from app.matching.engine import ItemInput
from app.matching.evidence_verifier import PageTexts, verify_comparison
from app.matching.llm_judge import (
    PROMPT_VERSION,
    JudgedItems,
    Passage,
    build_prompt,
    cache_key,
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
from app.schemas.retrieval import RetrievalHit, RetrievalOutcome, RetrievalRequest
from app.tender.splitter import leaves

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FoundEvidence:
    """What retrieval found in one catalogue for one requirement."""

    outcome: RetrievalOutcome
    passages: tuple[Passage, ...] = ()


class VerdictCache(Protocol):
    """Judge answers already given, by cache key."""

    def get(self, key: str) -> JudgeVerdict | None:
        """The stored answer, or None."""
        ...

    def put(self, key: str, model: str, verdict: JudgeVerdict) -> None:
        """Store an answer."""
        ...


@dataclass(frozen=True, slots=True)
class DbVerdictCache:
    """Judge answers in the `judge_answers` table, one short transaction each."""

    session_factory: sessionmaker[Session]

    def get(self, key: str) -> JudgeVerdict | None:
        """The stored answer, or None."""
        with session_scope(self.session_factory) as session:
            stored = JudgeAnswerRepository(session).verdict(key)
        return JudgeVerdict.model_validate(stored) if stored is not None else None

    def put(self, key: str, model: str, verdict: JudgeVerdict) -> None:
        """Store an answer under its key."""
        with session_scope(self.session_factory) as session:
            JudgeAnswerRepository(session).store(
                key, model, PROMPT_VERSION, verdict.model_dump(mode="json")
            )


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
    cached: int = 0
    failed: int = 0
    failures_in_a_row: int = 0
    decisions: dict[tuple[str, str, str], _Decision] = field(default_factory=dict)


def _passage(chunk: Chunk, code_only: bool, low_score: bool = False) -> Passage:
    """A stored chunk as the judge reads it."""
    return Passage(
        chunk_id=str(chunk.id),
        document=PurePosixPath(chunk.document.file_path).name,
        sha256=chunk.document.sha256,
        title=chunk.document.title,
        page_number=chunk.page_number,
        raw_text=chunk.raw_text,
        section=chunk.section,
        code_only=code_only,
        low_score=low_score,
    )


def _allowed(
    hits: Sequence[RetrievalHit],
    found: Mapping[uuid.UUID, Chunk],
    exclude: frozenset[tuple[int, str]],
) -> list[RetrievalHit]:
    """The hits whose chunk is not excluded (another product's), in the order given."""
    kept = []
    for hit in hits:
        chunk = found[hit.chunk_id]
        if (chunk.page_number, chunk.raw_text) not in exclude:
            kept.append(hit)
    return kept


@dataclass(frozen=True, slots=True)
class DbEvidenceFinder:
    """Retrieval over the stored chunks, in one short transaction per requirement."""

    session_factory: sessionmaker[Session]
    embedder: Embedder
    reranker: Reranker
    vocabulary: QueryVocabulary
    params: RetrievalParams
    low_score_hits: int = 0
    max_passages: int | None = None

    def find(self, request: RetrievalRequest) -> FoundEvidence:
        """Search one catalogue; the best hits that count as evidence, else the best few below it.

        Args:
            request: The requirement and the catalogue to search.

        Returns:
            The outcome and the passages, best first: the best `max_passages` accepted ones, or,
            when the product's passages all score below the threshold, the best `low_score_hits`
            marked low_score. Chunks in `request.exclude_passages` are never returned.

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
            found = {c.id: c for c in chunks.list_by_ids([h.chunk_id for h in result.hits])}
            allowed = _allowed(result.hits, found, request.exclude_passages)
            accepted = {h.chunk_id for h in retriever.evidence_hits(result)}
            hits = [h for h in allowed if h.chunk_id in accepted][: self.max_passages]
            low = not hits and result.outcome is RetrievalOutcome.BELOW_THRESHOLD
            if low:
                hits = allowed[: self.low_score_hits]
            passages = tuple(
                _passage(found[h.chunk_id], not low and h.rerank_score < self.params.min_score, low)
                for h in hits
            )
            return FoundEvidence(result.outcome, passages)


class JudgeService:
    """Decide free-text and unlabelled requirements with retrieval and the LLM."""

    def __init__(
        self,
        llm: LLMClient,
        finder: EvidenceFinder,
        options: JudgeOptions,
        cache: VerdictCache | None = None,
    ) -> None:
        self._llm = llm
        self._finder = finder
        self._options = options
        self._cache = cache

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
        logger.info(
            "judge done",
            extra={"calls": progress.calls, "cached": progress.cached, "failed": progress.failed},
        )
        if not progress.calls and not progress.cached:
            return JudgedItems(judged, "rules")
        summary = f"{progress.calls} calls, {progress.cached} cached, {progress.failed} failed"
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
                    exclude_passages=chosen.other_product_passages,
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
            prompt = build_prompt(requirement, request.product_type, heading, found.passages)
            key = cache_key(prompt, self._llm.model_name)
            cached = self._cache.get(key) if self._cache else None
            if cached is not None:
                progress.cached += 1
                return _Decision(verdict=cached, passages=found.passages)
            if progress.calls >= options.max_calls:
                return _Decision(note="LLM judge skipped: call limit for this run reached")
            progress.calls += 1
            verdict = self._llm.generate_json(prompt, JudgeVerdict)
        except ExternalServiceError as exc:
            progress.failed += 1
            progress.failures_in_a_row += 1
            logger.warning("judge failed", extra={"error": exc.message})
            return _Decision(note=f"LLM judge failed: {exc.message}")
        progress.failures_in_a_row = 0
        if self._cache:
            self._cache.put(key, self._llm.model_name, verdict)
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
        build_llm_client(settings, settings.LLM_JUDGE_THINKING_LEVEL),
        DbEvidenceFinder(
            session_factory,
            build_embedder(settings),
            build_reranker(settings),
            vocabulary,
            RetrievalParams.from_settings(settings),
            settings.LLM_JUDGE_LOW_SCORE_HITS,
            settings.LLM_JUDGE_MAX_PASSAGES,
        ),
        JudgeOptions(
            comparator=ComparatorOptions.from_settings(settings),
            status_scores={MatchStatus(k): v for k, v in settings.STATUS_SCORES.items()},
            quote_threshold=settings.QUOTE_MATCH_THRESHOLD,
            max_calls=settings.LLM_JUDGE_MAX_CALLS,
            max_failures=settings.LLM_JUDGE_MAX_FAILURES,
        ),
        DbVerdictCache(session_factory),
    )
