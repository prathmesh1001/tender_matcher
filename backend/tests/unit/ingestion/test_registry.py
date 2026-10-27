import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.exceptions import SourceRegistryError
from app.ingestion.registry import (
    compute_sha256,
    discover_sources_files,
    load_registry,
    parse_sources_file,
    verify_source_file,
)
from app.schemas.enums import DocType

PDF_BYTES = b"%PDF-1.7 synthetic test bytes"


def _entry(file: str, sha256: str, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "file": file,
        "category": "valves_pumps",
        "title": "Synthetic datasheet",
        "doc_type": "datasheet",
        "url": "https://example.invalid/datasheet.pdf",
        "fetched_at": "2026-10-22T10:00:00+05:30",
        "publish_date": "2024-03-01",
        "sha256": sha256,
    }
    entry.update(overrides)
    return entry


def _write_manufacturer(
    root: Path, name: str, docs: list[tuple[str, bytes]], **overrides: Any
) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    entries = []
    for filename, content in docs:
        (folder / filename).write_bytes(content)
        entry = _entry(filename, hashlib.sha256(content).hexdigest())
        entry.update(overrides)
        entries.append(entry)
    registry = folder / "sources.yaml"
    registry.write_text(yaml.safe_dump({"manufacturer": name, "documents": entries}))
    return registry


def test_compute_sha256_matches_hashlib(tmp_path: Path) -> None:
    path = tmp_path / "f.pdf"
    path.write_bytes(PDF_BYTES * 100_000)  # bigger than one read chunk
    assert compute_sha256(path) == hashlib.sha256(PDF_BYTES * 100_000).hexdigest()


def test_load_registry_resolves_paths_and_attaches_manufacturer(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", b"1"), ("b.pdf", b"2")])
    _write_manufacturer(tmp_path, "Beta", [("c.pdf", b"3")], category="motors")

    sources = load_registry(tmp_path)
    assert [(s.manufacturer, s.path.name) for s in sources] == [
        ("Acme", "a.pdf"),
        ("Acme", "b.pdf"),
        ("Beta", "c.pdf"),
    ]
    first = sources[0]
    assert first.doc_type is DocType.DATASHEET
    assert first.path.is_absolute()
    assert first.fetched_at.utcoffset() is not None
    assert [s.manufacturer for s in load_registry(tmp_path, category="motors")] == ["Beta"]


def test_discover_ignores_folders_without_registry(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", b"1")])
    (tmp_path / "Unregistered").mkdir()
    (tmp_path / "Unregistered" / "x.pdf").write_bytes(b"x")
    assert [p.parent.name for p in discover_sources_files(tmp_path)] == ["Acme"]


def test_missing_root_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SourceRegistryError, match="not found"):
        load_registry(tmp_path / "missing")


@pytest.mark.parametrize(
    ("override", "bad_field"),
    [
        ({"doc_type": "flyer"}, "doc_type"),
        ({"sha256": "ABC"}, "sha256"),
        ({"url": "not-a-url"}, "url"),
        ({"fetched_at": "2026-10-22T10:00:00"}, "fetched_at"),  # no timezone
        ({"category": "Valves & Pumps"}, "category"),
        ({"unexpected": "field"}, "unexpected"),
    ],
)
def test_invalid_entries_are_rejected_with_location(
    tmp_path: Path, override: dict[str, Any], bad_field: str
) -> None:
    registry = _write_manufacturer(tmp_path, "Acme", [("a.pdf", b"1")], **override)
    with pytest.raises(SourceRegistryError) as info:
        parse_sources_file(registry)
    assert any(bad_field in e for e in info.value.details["errors"])  # type: ignore[attr-defined]


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "Acme"
    folder.mkdir()
    entry = _entry("a.pdf", "0" * 64)
    del entry["url"]
    (folder / "sources.yaml").write_text(
        yaml.safe_dump({"manufacturer": "Acme", "documents": [entry]})
    )
    with pytest.raises(SourceRegistryError, match="Invalid"):
        load_registry(tmp_path)


def test_malformed_yaml_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "Acme"
    folder.mkdir()
    (folder / "sources.yaml").write_text("manufacturer: [unclosed")
    with pytest.raises(SourceRegistryError, match="Cannot read"):
        load_registry(tmp_path)


def test_same_pdf_registered_twice_is_rejected(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", b"same")])
    _write_manufacturer(tmp_path, "Beta", [("b.pdf", b"same")])
    with pytest.raises(SourceRegistryError, match="same sha256"):
        load_registry(tmp_path)


def test_verify_source_file_accepts_matching_hash(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", PDF_BYTES)])
    verify_source_file(load_registry(tmp_path)[0])


def test_verify_source_file_detects_changed_file(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", PDF_BYTES)])
    source = load_registry(tmp_path)[0]
    source.path.write_bytes(PDF_BYTES + b" newer edition")
    with pytest.raises(SourceRegistryError, match="sha256 mismatch"):
        verify_source_file(source)


def test_verify_source_file_detects_missing_file(tmp_path: Path) -> None:
    _write_manufacturer(tmp_path, "Acme", [("a.pdf", PDF_BYTES)])
    source = load_registry(tmp_path)[0]
    source.path.unlink()
    with pytest.raises(SourceRegistryError, match="missing"):
        verify_source_file(source)
