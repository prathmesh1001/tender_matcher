"""Follow the links inside a GeM tender so every spec/BOQ/ATC document is available locally.

`bid.pdf` is often only a wrapper: the requirements live in documents it links to (buyer spec,
BOQ spec and line items, ATC), which can themselves link further (CLAUDE.md D1). Starting from
the files already recorded in `tender.yaml`, links are found (PDF link annotations and URLs in
the text), classified by `config/tender_links.yaml`, and the ones marked `fetch` are downloaded
up to `TENDER_LINK_MAX_DEPTH` hops from bid.pdf.

Every response is recorded in `tender.yaml` with URL, fetch time, sha256, the file it was found
in and its depth. Empty responses and duplicates (same sha256 as a stored file) are recorded but
not stored twice.
"""

import hashlib
import io
import logging
import re
import zipfile
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Final

import httpx
import yaml
from pydantic import ValidationError

from app.core.exceptions import DocumentParseError, LinkFetchError, TenderParseError
from app.ingestion.pdf_parser import open_pdf
from app.schemas.tender_files import (
    DiscoveredLink,
    FetchedFile,
    LinkAction,
    LinkRule,
    LinkRules,
    ManifestFile,
    TenderManifest,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)

MANIFEST_FILENAME: Final = "tender.yaml"
BID_FILENAME: Final = "bid.pdf"
BID_ROLE: Final = "bid_document"
_URL_IN_TEXT: Final = re.compile(r"https?://[^\s\"'<>]+")
_HREF: Final = re.compile(r"""href\s*=\s*["'](https?://[^"']+)["']""", re.IGNORECASE)
_TRAILING_PUNCT: Final = ".,;:)]}"
_BID_NUMBER: Final = re.compile(r"GEM/\d{4}/B/\d+")
_PDF_MAGIC: Final = b"%PDF"
_ZIP_MAGIC: Final = b"PK\x03\x04"
_OOXML_PARTS: Final = (("word/", ".docx"), ("xl/", ".xlsx"), ("ppt/", ".pptx"))
_URL_SUFFIXES: Final = (".pdf", ".csv", ".xlsx", ".xls", ".docx", ".doc", ".html", ".htm")


@dataclass(frozen=True, slots=True)
class CompiledLinkRules:
    """Link rules with their patterns compiled, in file order."""

    default_action: LinkAction
    rules: tuple[tuple[re.Pattern[str], LinkRule], ...]

    def classify(self, url: str) -> tuple[str | None, LinkAction]:
        """Return (role, action) of the first matching rule, or (None, default_action)."""
        for pattern, rule in self.rules:
            if pattern.search(url):
                return rule.role, rule.action
        return None, self.default_action


def load_link_rules(path: Path) -> CompiledLinkRules:
    """Read and compile the link rules file.

    Args:
        path: The rules YAML.

    Returns:
        Compiled rules.

    Raises:
        TenderParseError: If the file is unreadable, invalid, or has a bad regex.
    """
    try:
        rules = LinkRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        compiled = tuple((re.compile(r.pattern), r) for r in rules.rules)
    except (OSError, yaml.YAMLError, ValidationError, re.error) as exc:
        raise TenderParseError(
            f"Invalid link rules {path}: {exc}", details={"path": str(path)}
        ) from exc
    return CompiledLinkRules(default_action=rules.default_action, rules=compiled)


def clean_url(url: str) -> str:
    """Strip whitespace and trailing sentence punctuation picked up from running text."""
    return url.strip().rstrip(_TRAILING_PUNCT)


def _dedupe(urls: Iterable[str]) -> list[str]:
    """Clean URLs, keep http(s) ones, drop duplicates, preserve first-seen order."""
    cleaned = (clean_url(u) for u in urls)
    return list(dict.fromkeys(u for u in cleaned if u.startswith(("http://", "https://"))))


def extract_pdf_links(path: Path) -> list[str]:
    """Return the URLs in a PDF: link annotations first, then URLs written in the text.

    Args:
        path: A PDF file.

    Returns:
        Unique http(s) URLs in page order.

    Raises:
        DocumentParseError: If the PDF cannot be opened.
    """
    urls: list[str] = []
    with open_pdf(path) as doc:
        for page in doc:
            urls.extend(str(link["uri"]) for link in page.get_links() if link.get("uri"))
            urls.extend(_URL_IN_TEXT.findall(page.get_text()))
    return _dedupe(urls)


def extract_links(path: Path) -> list[str]:
    """Return the URLs inside a tender file (PDF or HTML; other types have none).

    Args:
        path: A stored tender file.

    Returns:
        Unique http(s) URLs.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_links(path)
    if suffix in {".html", ".htm"}:
        return _dedupe(_HREF.findall(path.read_text(encoding="utf-8", errors="replace")))
    return []


def discover_links(
    path: Path, *, found_in: str, depth: int, rules: CompiledLinkRules
) -> list[DiscoveredLink]:
    """Find and classify the links in one file.

    Args:
        path: Local path of the file.
        found_in: Its file name, recorded as provenance.
        depth: Depth the links get (the file's own depth + 1).
        rules: Compiled link rules.

    Returns:
        One entry per unique URL.
    """
    links: list[DiscoveredLink] = []
    for url in extract_links(path):
        role, action = rules.classify(url)
        links.append(
            DiscoveredLink(url=url, found_in=found_in, depth=depth, role=role, action=action)
        )
    return links


def fetch_url(client: httpx.Client, url: str, max_bytes: int) -> FetchedFile:
    """Download one URL, refusing bodies larger than `max_bytes`.

    Args:
        client: HTTP client (timeouts, headers and redirects configured by the caller).
        url: The link.
        max_bytes: Size limit.

    Returns:
        The response body and content type.

    Raises:
        LinkFetchError: On network errors, non-2xx status, or an oversized body.
    """
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise LinkFetchError(f"{url} exceeds {max_bytes} bytes", details={"url": url})
            content_type = response.headers.get("content-type")
    except httpx.HTTPError as exc:
        raise LinkFetchError(f"Cannot fetch {url}: {exc}", details={"url": url}) from exc
    return FetchedFile(
        url=url, content=bytes(body), content_type=content_type, fetched_at=datetime.now(UTC)
    )


def office_extension(content: bytes) -> str | None:
    """Tell .docx from .xlsx/.pptx by the parts inside the ZIP (all three start with "PK").

    Args:
        content: File bytes starting with the ZIP signature.

    Returns:
        The Office extension, or None if it is not an Office Open XML file.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile:
        return None
    for prefix, ext in _OOXML_PARTS:
        if any(name.startswith(prefix) for name in names):
            return ext
    return None


def file_extension(fetched: FetchedFile) -> str:
    """Pick a file extension from the content (magic bytes), content type, then URL.

    GeM often serves attachments as `application/octet-stream` or even mislabels Word files as
    PDF, so the bytes are trusted first.

    Args:
        fetched: A downloaded file.

    Returns:
        An extension such as ".pdf", ".docx", ".xlsx", ".csv", ".html" or ".bin".
    """
    if fetched.content.startswith(_PDF_MAGIC):
        return ".pdf"
    if fetched.content.startswith(_ZIP_MAGIC):
        office = office_extension(fetched.content)
        if office is not None:
            return office
    content_type = (fetched.content_type or "").lower()
    if "html" in content_type:
        return ".html"
    if "csv" in content_type:
        return ".csv"
    url_path = PurePosixPath(fetched.url.split("?", 1)[0]).suffix.lower()
    return url_path if url_path in _URL_SUFFIXES else ".bin"


def unique_name(role: str, extension: str, taken: set[str]) -> str:
    """Return `role.ext`, or `role_2.ext`, `role_3.ext`, ... if already used."""
    name, n = f"{role}{extension}", 1
    while name in taken:
        n += 1
        name = f"{role}_{n}{extension}"
    return name


def sha256_hex(content: bytes) -> str:
    """Lower-case hex SHA-256 of `content`."""
    return hashlib.sha256(content).hexdigest()


def read_manifest(path: Path) -> TenderManifest:
    """Read and validate a `tender.yaml`.

    Raises:
        TenderParseError: If it is unreadable or invalid.
    """
    try:
        return TenderManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise TenderParseError(
            f"Invalid manifest {path}: {exc}", details={"path": str(path)}
        ) from exc


def write_manifest(path: Path, manifest: TenderManifest) -> None:
    """Write a manifest as YAML, omitting empty fields (but always keeping `file` and `url`)."""
    data = manifest.model_dump(mode="json", exclude_none=True)
    data["files"] = [
        {"file": f.file, "role": f.role, "url": f.url}
        | f.model_dump(mode="json", exclude_none=True, exclude={"file", "role", "url"})
        for f in manifest.files
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def bid_number_from_pdf(path: Path) -> str:
    """Read the GeM bid number ("GEM/2026/B/1234567") from the first page of bid.pdf.

    Raises:
        TenderParseError: If no bid number is printed on the first page.
    """
    with open_pdf(path) as doc:
        match = _BID_NUMBER.search(doc[0].get_text())
    if match is None:
        raise TenderParseError(
            f"No GeM bid number on page 1 of {path}", details={"path": str(path)}
        )
    return match.group(0)


def tender_folder(bid_number: str) -> str:
    """The folder of a tender ("GEM/2026/B/1234567" -> "GEM_2026_B_1234567")."""
    return bid_number.replace("/", "_")


def init_manifest(tender_dir: Path) -> TenderManifest:
    """Create a manifest for a tender folder that only has a user-provided bid.pdf.

    Args:
        tender_dir: Folder containing `bid.pdf`.

    Returns:
        A manifest with the bid document (URL unknown) as its only file.
    """
    bid = tender_dir / BID_FILENAME
    content = bid.read_bytes()
    entry = ManifestFile(
        file=BID_FILENAME,
        role=BID_ROLE,
        url=None,
        fetched_at=datetime.now(UTC),
        sha256=sha256_hex(content),
        content_type="application/pdf",
        depth=0,
        note="provided locally; source URL unknown",
    )
    return TenderManifest(
        bid_number=bid_number_from_pdf(bid), source="Provided locally", files=[entry]
    )


def _depth_of(entry: ManifestFile) -> int:
    """Recorded depth; files without one are bid.pdf (0) or direct attachments (1)."""
    if entry.depth is not None:
        return entry.depth
    return 0 if entry.role == BID_ROLE else 1


@dataclass(slots=True)
class CrawlReport:
    """What a crawl found and did.

    `missing` holds `fetch` links that are not recorded in the manifest and were not downloaded
    (offline check, or download failed).
    """

    discovered: list[DiscoveredLink] = field(default_factory=list)
    fetched: list[ManifestFile] = field(default_factory=list)
    missing: list[DiscoveredLink] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def mark_missing(self, link: DiscoveredLink) -> None:
        """Record a missing link once, even if several files link to it."""
        if all(m.url != link.url for m in self.missing):
            self.missing.append(link)


@dataclass(slots=True)
class LinkCrawler:
    """Breadth-first link following for one tender folder.

    With `client=None` nothing is downloaded: the crawl only reports which `fetch` links are
    missing from the manifest (offline check).
    """

    store: FileStore
    rules: CompiledLinkRules
    max_depth: int
    max_bytes: int
    client: httpx.Client | None = None

    def crawl(self, tender_ref: str, manifest: TenderManifest) -> CrawlReport:
        """Follow links from every stored file and add new downloads to `manifest.files`.

        Args:
            tender_ref: File-store reference of the tender folder.
            manifest: The tender's manifest; updated in place.

        Returns:
            The crawl report.
        """
        report = CrawlReport()
        queue = deque((f, _depth_of(f)) for f in manifest.stored_files())
        while queue:
            entry, depth = queue.popleft()
            if depth >= self.max_depth or entry.file is None:
                continue
            for link in self._links_in(tender_ref, entry.file, depth + 1, report):
                report.discovered.append(link)
                new = self._handle(tender_ref, manifest, link, report)
                if new is not None and new.file is not None:
                    queue.append((new, link.depth))
        return report

    def _links_in(
        self, tender_ref: str, name: str, depth: int, report: CrawlReport
    ) -> list[DiscoveredLink]:
        """Links in one stored file; an unreadable file is reported and yields no links."""
        try:
            with self.store.local_path(f"{tender_ref}/{name}") as path:
                return discover_links(path, found_in=name, depth=depth, rules=self.rules)
        except DocumentParseError as exc:
            logger.warning("cannot read links", extra={"file": name, "error": str(exc)})
            report.failed.append((name, str(exc)))
            return []

    def _handle(
        self,
        tender_ref: str,
        manifest: TenderManifest,
        link: DiscoveredLink,
        report: CrawlReport,
    ) -> ManifestFile | None:
        """Fetch one link if it is wanted and new; record the outcome in the manifest."""
        if link.action != "fetch" or link.url in manifest.known_urls():
            return None
        if self.client is None:
            report.mark_missing(link)
            return None
        try:
            fetched = fetch_url(self.client, link.url, self.max_bytes)
        except LinkFetchError as exc:
            logger.warning("link fetch failed", extra={"url": link.url, "error": str(exc)})
            report.failed.append((link.url, str(exc)))
            report.mark_missing(link)
            return None
        entry = self._record(tender_ref, manifest, link, fetched)
        manifest.files.append(entry)
        report.fetched.append(entry)
        logger.info("link fetched", extra={"url": link.url, "file": entry.file, "role": entry.role})
        return entry

    def _record(
        self, tender_ref: str, manifest: TenderManifest, link: DiscoveredLink, fetched: FetchedFile
    ) -> ManifestFile:
        """Store the download (unless empty or a duplicate) and build its manifest entry."""
        digest = sha256_hex(fetched.content)
        stored = {f.sha256: f.file for f in manifest.stored_files()}
        entry = ManifestFile(
            file=None,
            role=link.role or "attachment",
            url=link.url,
            fetched_at=fetched.fetched_at,
            sha256=digest,
            content_type=fetched.content_type,
            found_in=link.found_in,
            depth=link.depth,
        )
        if not fetched.content:
            return entry.model_copy(update={"note": "empty response; not stored"})
        if digest in stored:
            return entry.model_copy(update={"duplicate_of": stored[digest]})
        taken = {f.file for f in manifest.stored_files() if f.file}
        name = unique_name(entry.role, file_extension(fetched), taken)
        self.store.write(f"{tender_ref}/{name}", fetched.content)
        return entry.model_copy(update={"file": name})


def build_http_client(timeout_s: float, user_agent: str) -> httpx.Client:
    """HTTP client for document downloads (follows redirects, sends a clear User-Agent)."""
    return httpx.Client(
        timeout=timeout_s, follow_redirects=True, headers={"User-Agent": user_agent}
    )
