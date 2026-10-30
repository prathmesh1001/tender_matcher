"""Cross-encoder reranking behind a small interface (the model can be swapped by config).

Default: cross-encoder/ms-marco-MiniLM-L-6-v2 (English, ~22M parameters, Apache-2.0), run
locally with sentence-transformers. On a labelled tender it put the correct chunk first more
often than BAAI/bge-reranker-v2-m3 (10/13 vs 6/13); larger models can run in fp16 on CUDA.
Scores pass through a sigmoid, so they are 0-1 and the evidence threshold
(`RERANK_MIN_SCORE`) means the same for every query.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from app.core.config import EmbeddingDevice, Settings
from app.core.exceptions import ExternalServiceError
from app.retrieval.embedder import resolve_device

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder


class Reranker(Protocol):
    """Scores how well each passage answers a query."""

    @property
    def model_name(self) -> str:
        """Identifier of the reranking model."""
        ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Relevance in [0, 1] per passage; output order matches input order."""
        ...


@dataclass(slots=True)
class CrossEncoderReranker:
    """Local sentence-transformers CrossEncoder, loaded lazily on first use."""

    name: str
    device: EmbeddingDevice
    fp16: bool
    batch_size: int
    max_tokens: int
    _model: CrossEncoder | None = field(default=None, init=False, repr=False)

    @property
    def model_name(self) -> str:
        """The configured model identifier."""
        return self.name

    def _load(self) -> CrossEncoder:
        """Load the model once (from the local Hugging Face cache after the first download)."""
        if self._model is None:
            from sentence_transformers import CrossEncoder

            device = resolve_device(self.device)
            try:
                model = CrossEncoder(self.name, device=device, max_length=self.max_tokens)
            except (OSError, ValueError) as exc:
                raise ExternalServiceError(
                    f"Cannot load reranker model {self.name}: {exc}"
                ) from exc
            if self.fp16 and device == "cuda":
                model.model.half()
            self._model = model
        return self._model

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Score (query, passage) pairs with a sigmoid over the model's logits.

        Raises:
            ExternalServiceError: If the model cannot be loaded or scoring fails.
        """
        if not passages:
            return []
        import torch

        model = self._load()
        try:
            scores = model.predict(
                [(query, p) for p in passages],
                batch_size=self.batch_size,
                activation_fn=torch.nn.Sigmoid(),
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except RuntimeError as exc:  # torch raises RuntimeError (e.g. CUDA out of memory)
            raise ExternalServiceError(f"Reranking failed: {exc}") from exc
        return [float(s) for s in scores]


def build_reranker(settings: Settings) -> Reranker:
    """Create the configured reranker (the model itself loads on first use)."""
    return CrossEncoderReranker(
        name=settings.RERANKER_MODEL,
        device=settings.RERANKER_DEVICE,
        fp16=settings.RERANKER_FP16,
        batch_size=settings.RERANKER_BATCH_SIZE,
        max_tokens=settings.RERANKER_MAX_TOKENS,
    )
