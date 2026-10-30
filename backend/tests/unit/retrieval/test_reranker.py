"""Reranker tests that need no model download."""

from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from app.core.config import EmbeddingDevice
from app.core.exceptions import ExternalServiceError
from app.retrieval.reranker import CrossEncoderReranker, build_reranker
from tests.conftest import make_test_settings


class _FakeCrossEncoder:
    """Stands in for CrossEncoder: applies the given activation to passage lengths."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.pairs: list[tuple[str, str]] = []

    def predict(self, pairs: Sequence[tuple[str, str]], **kwargs: Any) -> np.ndarray:
        if self.fail:
            raise RuntimeError("CUDA out of memory")
        import torch

        self.pairs = list(pairs)
        logits = torch.tensor([float(len(p)) - 3.0 for _, p in pairs])
        scores: np.ndarray = kwargs["activation_fn"](logits).numpy()
        return scores


def _reranker(fake: _FakeCrossEncoder) -> CrossEncoderReranker:
    reranker = CrossEncoderReranker(
        name="fake", device=EmbeddingDevice.CPU, fp16=False, batch_size=4, max_tokens=64
    )
    reranker._model = fake  # type: ignore[assignment]
    return reranker


def test_scores_are_sigmoid_in_input_order() -> None:
    fake = _FakeCrossEncoder()
    scores = _reranker(fake).score("q", ["abc", "abcdef", "a"])
    assert scores[0] == pytest.approx(0.5)
    assert scores[1] > 0.9
    assert scores[2] < 0.2
    assert all(0 <= s <= 1 for s in scores)
    assert fake.pairs == [("q", "abc"), ("q", "abcdef"), ("q", "a")]


def test_no_passages() -> None:
    assert _reranker(_FakeCrossEncoder()).score("q", []) == []


def test_model_failure_is_an_external_service_error() -> None:
    with pytest.raises(ExternalServiceError):
        _reranker(_FakeCrossEncoder(fail=True)).score("q", ["a"])


def test_build_from_settings() -> None:
    reranker = build_reranker(make_test_settings())
    assert reranker.model_name == "cross-encoder/ms-marco-MiniLM-L-6-v2"
