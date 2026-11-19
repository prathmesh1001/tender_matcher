"""The slow part of PDF processing (page text, OCR, vector tables), cached on disk on its own.

Parsing a catalogue is minutes per document (PyMuPDF's layout model inside `find_tables` is
about 7 s a page; OCR is slower), while chunking, tagging and search text take seconds. The
processed-file cache is keyed by everything that shapes chunks, so an ontology or chunker
change used to re-parse every PDF (20-50 min before the first run). This cache holds only the
parse result, keyed by the file's bytes, the parse code, the parse options and the PyMuPDF
version, so such changes re-chunk from it instead.

Kept outside it on purpose (cheap, and shaped by the ontology): boilerplate removal, tables
rebuilt from OCR words (`ocr_table`, uses ontology code rules), chunking and tagging.
"""

import dataclasses
import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Final

from app.ingestion.layout import LayoutOptions
from app.ingestion.ocr import OcrEngine
from app.ingestion.pdf_parser import ParserOptions, open_pdf, parse_pdf
from app.ingestion.table_cleanup import TableCleanupOptions
from app.ingestion.table_extractor import extract_page_tables, merge_continued_tables
from app.schemas.chunking import ExtractedTable
from app.schemas.parsing import ParsedPage

logger = logging.getLogger(__name__)

PARSE_CACHE_SUFFIX: Final = ".parsed.json"
_HASH_BLOCK_BYTES: Final = 1 << 20
# Code whose output this cache holds, relative to the backend folder. A test checks that every
# app module the parser and table extractor import is listed here.
PARSE_CODE_FILES: Final = (
    "app/ingestion/parse_stage.py",
    "app/ingestion/pdf_parser.py",
    "app/ingestion/ocr.py",
    "app/ingestion/glyph_repair.py",
    "app/ingestion/table_extractor.py",
    "app/ingestion/table_cleanup.py",
    "app/ingestion/layout.py",
    "app/retrieval/text_normalizer.py",
    "app/schemas/parsing.py",
    "app/schemas/chunking.py",
    "app/schemas/enums.py",
    "app/ontology/schema.py",
)
# Installed packages whose version changes what is parsed.
_PARSE_PACKAGES: Final = ("pymupdf", "pymupdf-layout", "pytesseract")


@dataclass(frozen=True, slots=True)
class ParseOptions:
    """What decides a parse result besides the file and the code."""

    parser: ParserOptions
    ocr_engine: OcrEngine | None
    table_edge_fraction: float
    key_value_tables: bool
    table_cleanup: TableCleanupOptions
    layout: LayoutOptions | None  # rule-grid tables; None = off


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Parsed pages and the vector tables found on them, keyed by 1-based page number."""

    pages: list[ParsedPage]
    tables: dict[int, list[ExtractedTable]]


def extract_tables(
    path: Path,
    pages: Iterable[ParsedPage],
    edge: float,
    key_value: bool = False,
    cleanup: TableCleanupOptions | None = None,
    layout: LayoutOptions | None = None,
) -> dict[int, list[ExtractedTable]]:
    """Find vector tables on every non-OCR page and link those that continue across pages.

    Rows holding several stacked items (no dividing line) are split in every file: in
    catalogues they would otherwise mix two parts' materials ("01 02 | Body Body Seat Ring").

    Args:
        path: The PDF.
        pages: Its parsed pages (OCR pages are skipped: they have no vector tables).
        edge: Page-edge fraction for continuation detection.
        key_value: Tender file: read two-column tables as label/value pairs.
        cleanup: Row and header repairs (unit spellings, caption detection).
        layout: Rule-grid thresholds, for tables drawn without stroked lines.

    Returns:
        Tables keyed by 1-based page number.
    """
    page_list = list(pages)
    text_pages = {p.page_number for p in page_list if not p.ocr}
    with open_pdf(path) as doc:
        found = {
            page.number + 1: extract_page_tables(page, key_value, True, cleanup, layout)
            for page in doc
            if page.number + 1 in text_pages
        }
    heights = {p.page_number: p.height for p in page_list}
    return merge_continued_tables(found, heights, edge)


def parse_with_tables(
    path: Path, options: ParseOptions, cache: ParseCache | None = None
) -> ParseResult:
    """Parse a PDF and find its vector tables, from the cache when it holds this exact parse.

    Args:
        path: The PDF.
        options: Parse options.
        cache: Disk cache, or None to always parse.

    Returns:
        Pages and tables.

    Raises:
        DocumentParseError: If the PDF cannot be opened.
        ExternalServiceError: If OCR is needed and fails.
    """
    key = cache.key(path, options) if cache is not None else None
    if cache is not None and key is not None:
        hit = cache.get(key)
        if hit is not None:
            return hit
    logger.info("parsing pdf", extra={"file": path.name})
    parsed = parse_pdf(path, options.parser, options.ocr_engine)
    tables = extract_tables(
        path,
        parsed.pages,
        options.table_edge_fraction,
        options.key_value_tables,
        options.table_cleanup,
        options.layout,
    )
    result = ParseResult(pages=list(parsed.pages), tables=tables)
    if cache is not None and key is not None:
        cache.put(key, result)
    return result


class ParseCache:
    """Parse results on disk, one JSON file per key (file bytes + code + options).

    A changed parser, option or PyMuPDF version gives a new key, so stale parses are never
    read; old files are left behind and can be deleted with the folder at any time.
    """

    def __init__(self, root: Path, code_fingerprint: str) -> None:
        self._root = root
        self._code = code_fingerprint

    def key(self, path: Path, options: ParseOptions) -> str:
        """Cache key of one file under these options."""
        digest = hashlib.sha256(self._code.encode("utf-8"))
        digest.update(_file_sha256(path).encode("utf-8"))
        digest.update(options_json(options).encode("utf-8"))
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self._root / f"{key}{PARSE_CACHE_SUFFIX}"

    def get(self, key: str) -> ParseResult | None:
        """The stored result, or None if absent or unreadable."""
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ParseResult(
                pages=[ParsedPage.model_validate(p) for p in data["pages"]],
                tables={
                    int(page): [ExtractedTable.model_validate(t) for t in tables]
                    for page, tables in data["tables"].items()
                },
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("parse cache unreadable", extra={"file": path.name, "error": str(exc)})
            return None

    def put(self, key: str, result: ParseResult) -> None:
        """Store a result; written to a temporary file first so readers never see half of it."""
        self._root.mkdir(parents=True, exist_ok=True)
        data = {
            "pages": [p.model_dump(mode="json") for p in result.pages],
            "tables": {
                str(page): [t.model_dump(mode="json") for t in tables]
                for page, tables in result.tables.items()
            },
        }
        target = self._path(key)
        temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)


def parse_code_fingerprint(backend_dir: Path) -> str:
    """sha256 over the parse code (CRLF folded, named relative to the backend) and versions.

    Args:
        backend_dir: The backend folder.

    Returns:
        Hex digest.
    """
    digest = hashlib.sha256()
    for name in sorted(PARSE_CODE_FILES):
        digest.update(name.encode("utf-8"))
        digest.update((backend_dir / name).read_bytes().replace(b"\r\n", b"\n"))
    for package in _PARSE_PACKAGES:
        digest.update(f"{package}={_version(package)}".encode())
    return digest.hexdigest()


def options_json(options: ParseOptions) -> str:
    """The options as stable JSON (sets sorted, objects by class name and fields)."""
    return json.dumps(_plain(options), sort_keys=True)


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "absent"


def _plain(value: object) -> object:
    """JSON-ready form whose text does not depend on hash order or memory addresses."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, re.Pattern):
        return value.pattern
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, set | frozenset):
        return sorted((_plain(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    fields = _object_fields(value)
    return {"type": type(value).__qualname__, **{k: _plain(v) for k, v in fields.items()}}


def _object_fields(value: object) -> dict[str, object]:
    """Fields of a dataclass or plain object (an OCR engine's language and config)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    return dict(sorted(vars(value).items())) if hasattr(value, "__dict__") else {}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(_HASH_BLOCK_BYTES):
            digest.update(block)
    return digest.hexdigest()
