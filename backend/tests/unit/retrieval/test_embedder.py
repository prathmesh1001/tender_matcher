"""Embedder tests that need no model download (the real model is exercised by the CLI run)."""

from collections.abc import Sequence
from typing import Any

import numpy as np

from app.core.config import EmbeddingDevice
from app.retrieval.embedder import SentenceTransformerEmbedder, embedding_text, text_hash

FL = "\N{LATIN SMALL LIGATURE FL}"
BAD = "\N{REPLACEMENT CHARACTER}"


def test_embedding_text_folds_unicode_but_keeps_case_and_hindi() -> None:
    raw = f"Butter{FL}y  Valve\n{BAD}ववरण / गेट वाल्व"
    assert embedding_text(raw) == "Butterfly Valve ववरण / गेट वाल्व"


def test_text_hash_is_stable_sha256() -> None:
    assert text_hash("a") == text_hash("a")
    assert len(text_hash("a")) == 64
    assert text_hash("a") != text_hash("b")


class _FakeModel:
    """Stands in for SentenceTransformer: the vector encodes the text length."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def encode(self, texts: Sequence[str], **_: Any) -> np.ndarray:
        self.seen = list(texts)
        return np.array([[float(len(t)), 1.0] for t in texts])


def test_embed_sorts_by_length_but_returns_input_order() -> None:
    embedder = SentenceTransformerEmbedder(
        name="fake", device=EmbeddingDevice.CPU, fp16=False, batch_size=2, max_tokens=64
    )
    fake = _FakeModel()
    embedder._model = fake  # type: ignore[assignment]
    vectors = embedder.embed(["long text here", "a", "mid"])
    assert fake.seen == ["a", "mid", "long text here"]
    assert [v[0] for v in vectors] == [14.0, 1.0, 3.0]


def test_embed_nothing() -> None:
    embedder = SentenceTransformerEmbedder(
        name="fake", device=EmbeddingDevice.CPU, fp16=False, batch_size=2, max_tokens=64
    )
    assert embedder.embed([]) == []
