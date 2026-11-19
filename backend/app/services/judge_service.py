"""Use case: let the LLM judge decide what rules left open in a tender's items.

Runs after the catalogue is chosen, so the judge never changes which document an item uses, and
never mixes documents (B10). Retrieval comes first; with no evidence hit the LLM is not called.
A run has a call limit and stops calling after repeated failures, so an exhausted quota costs
some `unsupported` results instead of the run. Answers are cached by prompt and model, so a rerun
gives the same results and calls nothing; failures are never cached.

Catalogue searches run as one batch (CPU bound: shared chunk sets, one embedder call); LLM calls
run `workers` at a time. Which
lines get the limited calls is decided in document order before any call is made, so a run gives
the same results with 1 worker or many.
"""

import contextvars
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
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
from app.schemas.report import ItemReport, ManufacturerItemReport
from app.schemas.requirements import Requirement
from app.schemas.retrieval import (
    RetrievalHit,
    RetrievalOutcome,
    RetrievalRequest,
    RetrievalResult,
)
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

    def find_many(self, requests: Sequence[RetrievalRequest]) -> list[FoundEvidence]:
        """`find` for several requests at once, one outcome per request in order."""
        ...

    def warm_up(self, calls: int) -> None:
        """Load the search models with this many dummy queries."""
        ...


@dataclass(frozen=True, slots=True)
class JudgeOptions:
    """Limits and comparator settings of the judge."""

    comparator: ComparatorOptions
    status_scores: dict[MatchStatus, float]
    quote_threshold: float
    max_calls: int
    max_failures: int
    workers: int = 1
    warm_up_calls: int = 0


@dataclass(frozen=True, slots=True)
class _Decision:
    """A verdict with what it may cite, or a note saying why there is none."""

    verdict: JudgeVerdict | None = None
    passages: tuple[Passage, ...] = ()
    note: str = ""


type _Key = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class _Job:
    """One line to judge against one chosen catalogue (repeated lines share a job)."""

    requirement: Requirement
    request: RetrievalRequest
    heading: str | None


@dataclass(frozen=True, slots=True)
class _Question:
    """A job whose evidence was found and whose answer is not cached: it needs a call."""

    prompt: str
    key: str
    passages: tuple[Passage, ...]


@dataclass(slots=True)
class _Progress:
    """One tender's calls, failures and verdicts (a repeated line is judged once)."""

    calls: int = 0
    cached: int = 0
    failed: int = 0
    failures_in_a_row: int = 0
    decisions: dict[_Key, _Decision] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _run_all[T, R](work: Callable[[T], R], jobs: Sequence[T], workers: int) -> list[R]:
    """`work` over every job, `workers` at a time, results in the order of `jobs`.

    The first job runs alone, so models that load on first use load once. Each job runs in a copy
    of the caller's context, so its log lines keep the run's fields (run_id, tender_id).

    Args:
        work: Done once per job.
        jobs: The jobs.
        workers: Most jobs at a time.

    Returns:
        One result per job, in order.
    """
    if workers <= 1 or len(jobs) <= 1:
        return [work(job) for job in jobs]
    first = work(jobs[0])
    rest = jobs[1:]
    contexts = [contextvars.copy_context() for _ in rest]
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="judge") as pool:
        return [first, *pool.map(lambda ctx, job: ctx.run(work, job), contexts, rest)]


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

    def warm_up(self, calls: int) -> None:
        """Load the embedder and reranker and run a few dummy queries through them.

        The first calls after loading are much slower than later ones (measured on CPU: 125 s,
        12 s, 5 s, then 0.6 s per query embedding), so a few are made before any run.

        Args:
            calls: How many dummy queries (0 does nothing).

        Raises:
            ExternalServiceError: If the embedder or reranker fails.
        """
        for i in range(calls):
            query = f"warm up query {i}"
            self.embedder.embed([query])
            self.reranker.score(query, [f"warm up passage {i}"])

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
        return self.find_many([request])[0]

    def find_many(self, requests: Sequence[RetrievalRequest]) -> list[FoundEvidence]:
        """`find` for several requests in one session, with their queries embedded together.

        Args:
            requests: The searches.

        Returns:
            One outcome per request, in the same order.

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
            results = retriever.retrieve_many(requests)
            hit_ids = {h.chunk_id for result in results for h in result.hits}
            found = {c.id: c for c in chunks.list_by_ids(list(hit_ids))}
            return [
                self._evidence(request, result, retriever.evidence_hits(result), found)
                for request, result in zip(requests, results, strict=True)
            ]

    def _evidence(
        self,
        request: RetrievalRequest,
        result: RetrievalResult,
        evidence_hits: Sequence[RetrievalHit],
        found: Mapping[uuid.UUID, Chunk],
    ) -> FoundEvidence:
        """The passages one search gives the judge: accepted hits, else the best low ones."""
        allowed = _allowed(result.hits, found, request.exclude_passages)
        accepted = {h.chunk_id for h in evidence_hits}
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

    def warm_up(self) -> None:
        """Load the search models now, so the first run does not wait for them."""
        started = time.monotonic()
        self._finder.warm_up(self._options.warm_up_calls)
        logger.info(
            "judge models warm",
            extra={"calls": self._options.warm_up_calls, "seconds": time.monotonic() - started},
        )

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
        self._decide_all(self._collect(inputs, reports), progress)
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

    @staticmethod
    def _open_lines(
        report: ItemReport, result: ManufacturerItemReport, requirements: Sequence[Requirement]
    ) -> list[tuple[int, _Key, _Job]]:
        """The lines rules left open for one manufacturer, with their position and job.

        Only a chosen catalogue is judged, never a tie. The key is the catalogue, its product
        heading and the line's text, so a repeated line is judged once.
        """
        if result.chosen is None or report.category is None or report.product_type is None:
            return []
        chosen = result.chosen
        lines: list[tuple[int, _Key, _Job]] = []
        for position, (requirement, comparison) in enumerate(
            zip(requirements, result.results, strict=True)
        ):
            if comparison.status is not MatchStatus.UNSUPPORTED or not needs_judge(requirement):
                continue
            request = RetrievalRequest(
                manufacturer=result.manufacturer,
                category=report.category,
                product_type=report.product_type,
                text=requirement.raw_text,
                attribute=requirement.attribute,
                document_sha256=chosen.sha256,
                exclude_passages=chosen.other_product_passages,
            )
            key = (chosen.sha256, chosen.product_heading or "", requirement.raw_text)
            lines.append((position, key, _Job(requirement, request, chosen.product_heading)))
        return lines

    def _collect(
        self, inputs: Sequence[ItemInput], reports: Sequence[ItemReport]
    ) -> dict[_Key, _Job]:
        """Every line to judge in this tender, once each, in document order."""
        jobs: dict[_Key, _Job] = {}
        for entry, report in zip(inputs, reports, strict=True):
            requirements = list(leaves(entry.requirements))
            for result in report.manufacturers:
                for _, key, job in self._open_lines(report, result, requirements):
                    jobs.setdefault(key, job)
        return jobs

    def _manufacturer(
        self,
        entry: ItemInput,
        report: ItemReport,
        result: ManufacturerItemReport,
        pages: PageTexts,
        progress: _Progress,
    ) -> ManufacturerItemReport:
        """One manufacturer's results with the decided lines filled in and the score redone."""
        lines = self._open_lines(report, result, list(leaves(entry.requirements)))
        if not lines:
            return result
        comparisons: list[Comparison] = list(result.results)
        for position, key, job in lines:
            comparisons[position] = self._judged(
                entry, job.requirement, progress.decisions[key], pages
            )
        return result.model_copy(
            update={
                "results": comparisons,
                "score": score(comparisons, self._options.status_scores),
            }
        )

    def _judged(
        self, entry: ItemInput, requirement: Requirement, decision: _Decision, pages: PageTexts
    ) -> Comparison:
        """One requirement's result from its line's decision, its quote checked on the page."""
        if decision.verdict is None:
            return undecided(requirement, decision.note)
        options = self._options
        result = judged_comparison(
            requirement, decision.verdict, decision.passages, entry.ctx, options.comparator
        )
        return verify_comparison(result, pages, options.quote_threshold)

    def _decide_all(self, jobs: Mapping[_Key, _Job], progress: _Progress) -> None:
        """Search and ask for every job; the call limit goes to the first lines in order.

        Args:
            jobs: The lines to judge, in document order.
            progress: Receives the decisions and the counts.
        """
        keys = list(jobs)
        # Searches never run side by side: the embedder and reranker already use every CPU
        # core, and that measured 2x slower (2026-11-14). They run as one batch instead (shared
        # chunk sets, one embedder call); only the LLM calls overlap.
        searched = self._search_all([jobs[key] for key in keys])
        prepared = [
            self._prepare(jobs[key], found, progress)
            for key, found in zip(keys, searched, strict=True)
        ]
        questions: list[tuple[_Key, _Question]] = []
        for key, outcome in zip(keys, prepared, strict=True):
            if isinstance(outcome, _Decision):
                progress.decisions[key] = outcome
            elif len(questions) < self._options.max_calls:
                questions.append((key, outcome))
            else:
                progress.decisions[key] = _Decision(
                    note="LLM judge skipped: call limit for this run reached"
                )
        answers = _run_all(
            lambda pair: self._ask(pair[1], progress), questions, self._options.workers
        )
        for (key, _), decision in zip(questions, answers, strict=True):
            progress.decisions[key] = decision

    def _search_all(self, jobs: Sequence[_Job]) -> list[FoundEvidence | None]:
        """Every job's catalogue search in one batch; None = search again on its own.

        A failed batch falls back to one search per job, so a failure counts per line (and
        stops the judge after `max_failures` in a row) exactly as before batching.
        """
        if not jobs:
            return []
        try:
            return list(self._finder.find_many([job.request for job in jobs]))
        except ExternalServiceError as exc:
            logger.warning(
                "batched search failed, searching one by one", extra={"error": exc.message}
            )
            return [None] * len(jobs)

    def _prepare(
        self, job: _Job, searched: FoundEvidence | None, progress: _Progress
    ) -> _Question | _Decision:
        """Use (or redo) the catalogue search and look the prompt up in the cache; no LLM call."""
        if progress.failures_in_a_row >= self._options.max_failures:
            return _Decision(note="LLM judge stopped: too many failures in a row")
        try:
            found = searched if searched is not None else self._finder.find(job.request)
        except ExternalServiceError as exc:
            return self._failed(exc, progress)
        if not found.passages:
            return _Decision(note=f"no evidence in this catalogue ({found.outcome.value})")
        prompt = build_prompt(
            job.requirement, job.request.product_type, job.heading, found.passages
        )
        key = cache_key(prompt, self._llm.model_name)
        cached = self._cache.get(key) if self._cache else None
        if cached is not None:
            with progress.lock:
                progress.cached += 1
            return _Decision(verdict=cached, passages=found.passages)
        return _Question(prompt, key, found.passages)

    def _ask(self, question: _Question, progress: _Progress) -> _Decision:
        """Ask the model one question; a failure becomes a note, never an error."""
        with progress.lock:
            if progress.failures_in_a_row >= self._options.max_failures:
                return _Decision(note="LLM judge stopped: too many failures in a row")
            progress.calls += 1
        try:
            verdict = self._llm.generate_json(question.prompt, JudgeVerdict)
        except ExternalServiceError as exc:
            return self._failed(exc, progress)
        with progress.lock:
            progress.failures_in_a_row = 0
        if self._cache:
            self._cache.put(question.key, self._llm.model_name, verdict)
        return _Decision(verdict=verdict, passages=question.passages)

    @staticmethod
    def _failed(exc: ExternalServiceError, progress: _Progress) -> _Decision:
        """Count a failed search or call and say why the line stays open."""
        with progress.lock:
            progress.failed += 1
            progress.failures_in_a_row += 1
        logger.warning("judge failed", extra={"error": exc.message})
        return _Decision(note=f"LLM judge failed: {exc.message}")


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
            workers=settings.LLM_JUDGE_WORKERS,
            warm_up_calls=settings.MODEL_WARM_UP_CALLS,
        ),
        DbVerdictCache(session_factory),
    )
