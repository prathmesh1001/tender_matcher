from pathlib import Path

import pytest

from app.core.exceptions import InvalidInputError, NotFoundError
from app.storage.file_store import LocalFileStore


def test_write_read_and_ref_round_trip(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    store.write("tenders/T1/bid.pdf", b"%PDF-1.7")
    assert store.exists("tenders/T1/bid.pdf")
    with store.local_path("tenders/T1/bid.pdf") as path:
        assert path.read_bytes() == b"%PDF-1.7"
        assert store.ref_for(path) == "tenders/T1/bid.pdf"


@pytest.mark.parametrize("ref", ["../escape.pdf", "/abs/path.pdf", "a/../../b", ""])
def test_refs_cannot_escape_the_root(tmp_path: Path, ref: str) -> None:
    with pytest.raises(InvalidInputError):
        LocalFileStore(tmp_path).exists(ref)


def test_missing_file(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path)
    assert not store.exists("nope.pdf")
    with pytest.raises(NotFoundError), store.local_path("nope.pdf"):
        pass


def test_path_outside_root_has_no_ref(tmp_path: Path) -> None:
    store = LocalFileStore(tmp_path / "root")
    with pytest.raises(InvalidInputError):
        store.ref_for(tmp_path / "elsewhere.pdf")
