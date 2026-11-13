"""Postgres file store and the one-off import: same references, content stored once."""

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Connection
from sqlalchemy.orm import Session, sessionmaker

from app.core.exceptions import InvalidInputError, NotFoundError
from app.db.repositories.stored_file_repository import StoredFileRepository
from app.services.file_import_service import FileImportService
from app.storage.file_store import PostgresFileStore


@pytest.fixture
def factory(db_connection: Connection) -> sessionmaker[Session]:
    """Sessions inside the test's rolled-back transaction."""
    return sessionmaker(bind=db_connection, join_transaction_mode="create_savepoint")


def test_write_then_read_through_a_temporary_copy(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    store = PostgresFileStore(factory, tmp_path)
    ref = "tenders/GEM_X/bid.pdf"
    assert store.exists(ref) is False
    store.write(ref, b"%PDF-1.7 one")
    assert store.exists(ref) is True
    with store.local_path(ref) as path:
        assert path.name == "bid.pdf"  # parsers read the suffix
        assert path.read_bytes() == b"%PDF-1.7 one"
    assert not path.exists()  # the copy is gone afterwards


def test_rewriting_a_reference_replaces_its_content(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    store = PostgresFileStore(factory, tmp_path)
    store.write("a/x.pdf", b"old")
    store.write("a/x.pdf", b"new")
    with store.local_path("a/x.pdf") as path:
        assert path.read_bytes() == b"new"


def test_same_content_under_two_references_is_stored_once(
    factory: sessionmaker[Session], db_session: Session, tmp_path: Path
) -> None:
    store = PostgresFileStore(factory, tmp_path)
    store.write("t1/spec.pdf", b"shared spec")
    store.write("t2/spec.pdf", b"shared spec")
    refs, blobs = StoredFileRepository(db_session).count()
    assert (refs, blobs) == (2, 1)


def test_missing_and_unsafe_references(factory: sessionmaker[Session], tmp_path: Path) -> None:
    store = PostgresFileStore(factory, tmp_path)
    with pytest.raises(NotFoundError), store.local_path("nope/x.pdf"):
        pass
    with pytest.raises(InvalidInputError):
        store.write("../escape.pdf", b"x")


def test_references_match_the_local_store(factory: sessionmaker[Session], tmp_path: Path) -> None:
    store = PostgresFileStore(factory, tmp_path)
    assert store.ref_for(tmp_path / "manufacturers" / "KSB" / "cat.pdf") == (
        "manufacturers/KSB/cat.pdf"
    )


def test_import_copies_files_once_and_keeps_yaml_on_disk(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    (tmp_path / "manufacturers" / "Acme").mkdir(parents=True)
    (tmp_path / "manufacturers" / "Acme" / "cat.pdf").write_bytes(b"catalogue")
    (tmp_path / "manufacturers" / "Acme" / "sources.yaml").write_text("x: 1")
    (tmp_path / "tenders" / "GEM_X").mkdir(parents=True)
    (tmp_path / "tenders" / "GEM_X" / "bid.pdf").write_bytes(b"bid")
    folders = [tmp_path / "manufacturers", tmp_path / "tenders", tmp_path / "missing"]
    service = FileImportService(factory, tmp_path)

    first = service.import_files(folders)
    assert first.copied == ["manufacturers/Acme/cat.pdf", "tenders/GEM_X/bid.pdf"]
    assert first.new_blobs == 2
    again = service.import_files(folders)
    assert (again.copied, len(again.unchanged)) == ([], 2)  # idempotent

    store = PostgresFileStore(factory, tmp_path)
    assert store.exists("manufacturers/Acme/sources.yaml") is False
    with store.local_path("tenders/GEM_X/bid.pdf") as path:
        assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(b"bid").hexdigest()
