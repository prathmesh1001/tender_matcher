"""Dense embeddings behind a small interface (the model can be swapped by config).

Default: BAAI/bge-m3 (multilingual, 1024-d, MIT licence), run locally with
sentence-transformers on the GPU in fp16 when CUDA is available, else on the CPU.

What is embedded is `embedding_text(raw_text)`: the verbatim chunk text with only unicode
folding and whitespace clean-up. Unlike `search_text` it keeps case and Hindi, which the
multilingual model can use; stray U+FFFD glyphs from broken PDF fonts are removed.
"""

import hashlib
import re
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from app.core.config import EmbeddingDevice, Settings
from app.core.exceptions import ExternalServiceError
from app.retrieval.text_normalizer import fold_unicode

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

_WHITESPACE: Final = re.compile(r"\s+")
_REPLACEMENT_CHAR: Final = "\N{REPLACEMENT CHARACTER}"


class Embedder(Protocol):
    """Turns texts into unit-length vectors."""

    @property
    def model_name(self) -> str:
        """Identifier stored with every vector (vectors of different models never mix)."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts; output order matches input order."""
        ...


def embedding_text(raw_text: str) -> str:
    """The exact text sent to the embedder for a chunk.

    Args:
        raw_text: Verbatim chunk text.

    Returns:
        Unicode-folded text without U+FFFD and with single spaces.
    """
    folded = fold_unicode(raw_text).replace(_REPLACEMENT_CHAR, " ")
    return _WHITESPACE.sub(" ", folded).strip()


def text_hash(text: str) -> str:
    """SHA-256 of the embedded text: the cache key shared by identical chunks."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_device(device: EmbeddingDevice) -> str:
    """Map the configured device to a torch device name ("auto" picks CUDA if present)."""
    if device != EmbeddingDevice.AUTO:
        return device.value
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


@dataclass(slots=True)
class SentenceTransformerEmbedder:
    """Local sentence-transformers model, loaded lazily on first use.

    Texts are encoded sorted by length (short texts are not padded to the longest one in a
    batch) and returned in the caller's order, L2-normalized for cosine similarity.
    """

    name: str
    device: EmbeddingDevice
    fp16: bool
    batch_size: int
    max_tokens: int
    _model: SentenceTransformer | None = field(default=None, init=False, repr=False)
    _load_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def model_name(self) -> str:
        """The configured model identifier."""
        return self.name

    def _load(self) -> SentenceTransformer:
        """Load the model once (from the local Hugging Face cache after the first download)."""
        with self._load_lock:  # a run and the warm-up thread may both ask
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                device = resolve_device(self.device)
                try:
                    model = SentenceTransformer(self.name, device=device)
                except (OSError, ValueError) as exc:
                    raise ExternalServiceError(
                        f"Cannot load embedding model {self.name}: {exc}"
                    ) from exc
                if self.fp16 and device == "cuda":
                    model.half()
                model.max_seq_length = self.max_tokens
                self._model = model
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts in length-sorted batches; output order matches input order.

        Raises:
            ExternalServiceError: If the model cannot be loaded or encoding fails.
        """
        if not texts:
            return []
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        model = self._load()
        try:
            vectors = model.encode(
                [texts[i] for i in order],
                batch_size=self.batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except RuntimeError as exc:  # torch raises RuntimeError (e.g. CUDA out of memory)
            raise ExternalServiceError(f"Embedding failed: {exc}") from exc
        result: list[list[float]] = [[] for _ in texts]
        for position, original in enumerate(order):
            result[original] = [float(v) for v in vectors[position]]
        return result


def build_embedder(settings: Settings) -> Embedder:
    """Create the configured embedder (the model itself loads on first use)."""
    return SentenceTransformerEmbedder(
        name=settings.EMBEDDING_MODEL,
        device=settings.EMBEDDING_DEVICE,
        fp16=settings.EMBEDDING_FP16,
        batch_size=settings.EMBEDDING_BATCH_SIZE,
        max_tokens=settings.EMBEDDING_MAX_TOKENS,
    )
