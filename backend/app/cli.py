"""Command-line entry point for ingestion (thin: parse args, call the service, print a summary).

Usage (from backend/):
    python -m app.cli ingest-manufacturers [--category valves_pumps]
    python -m app.cli check-links [--tender GEM_2026_B_8119613]   # offline, no downloads
    python -m app.cli fetch-links [--tender ...]                    # downloads missing links
    python -m app.cli ingest-tenders [--tender ...]
    python -m app.cli tag-products                                  # no re-parsing
    python -m app.cli embed [--source manufacturer|tender|all] [--manufacturer NAME]
    python -m app.cli ingest-all [--fetch] [--reprocess]            # catalogues + tenders

Exit code 1 if any file failed.
"""

import argparse
import sys
from collections.abc import Sequence

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.ingestion.pipeline import IngestOutcome
from app.schemas.enums import DocumentSource
from app.services.indexing_service import build_indexing_service
from app.services.ingestion_service import (
    BatchResult,
    IngestionService,
    build_ingestion_service,
)
from app.tender.documents import TenderIngestOutcome
from app.tender.link_fetcher import CrawlReport


def _out(line: str = "") -> None:
    """Write one line of the human-readable summary to stdout."""
    sys.stdout.write(line + "\n")


def _report_failures[T](result: BatchResult[T]) -> int:
    """Print failed items; return how many failed."""
    for item, error in result.failed:
        _out(f"  FAILED {item}: {error}")
    return len(result.failed)


def _report_files(outcomes: Sequence[IngestOutcome]) -> None:
    """Print one line per file outcome."""
    for o in outcomes:
        _out(f"  {o.status.value:20} {o.file_ref}  pages={o.pages} chunks={o.chunks}")


def run_manufacturers(
    service: IngestionService, category: str | None, *, reprocess: bool = False
) -> int:
    """Ingest catalogues and print one line per file."""
    result = service.ingest_manufacturers(category, reprocess=reprocess)
    _out(f"Manufacturer documents: {len(result.succeeded)} ok, {len(result.failed)} failed")
    _report_files(result.succeeded)
    return _report_failures(result)


def _report_crawl(result: BatchResult[tuple[str, CrawlReport]], *, offline: bool) -> int:
    """Print per-tender link counts and missing/fetched links; return the failure count."""
    for name, report in result.succeeded:
        fetchable = sum(1 for d in report.discovered if d.action == "fetch")
        _out(
            f"  {name}: links={len(report.discovered)} fetch-rule={fetchable} "
            f"fetched={len(report.fetched)} missing={len(report.missing)} "
            f"failed={len(report.failed)}"
        )
        for link in report.missing if offline else report.fetched:
            label = "MISSING" if offline else "fetched"
            _out(f"      {label} [{link.role}] {link.url}")
        for url, error in report.failed:
            _out(f"      FAILED {url}: {error}")
    return _report_failures(result) + sum(len(r.failed) for _, r in result.succeeded)


def run_check_links(service: IngestionService, tender: str | None) -> int:
    """Report links each tender should have that are missing from its manifest."""
    result = service.check_links(tender)
    _out(f"Link check (offline): {len(result.succeeded)} tenders")
    return _report_crawl(result, offline=True)


def run_fetch_links(service: IngestionService, tender: str | None) -> int:
    """Download missing links and update manifests."""
    result = service.fetch_links(tender)
    _out(f"Link fetch: {len(result.succeeded)} tenders")
    return _report_crawl(result, offline=False)


def _report_tenders(outcomes: Sequence[TenderIngestOutcome]) -> None:
    """Print each tender and its files."""
    for t in outcomes:
        _out(f"  {t.bid_number} ({'new' if t.created else 'existing'} tender)")
        _report_files(t.files)


def run_tenders(service: IngestionService, tender: str | None, *, reprocess: bool = False) -> int:
    """Ingest tender documents and print one line per file."""
    result = service.ingest_tenders(tender, reprocess=reprocess)
    _out(f"Tenders: {len(result.succeeded)} ok, {len(result.failed)} failed")
    _report_tenders(result.succeeded)
    return _report_failures(result)


def run_embed(settings: Settings, source: str, manufacturer: str | None) -> int:
    """Embed chunks that have no vector yet and print one line per document."""
    kind = None if source == "all" else DocumentSource(source)
    result = build_indexing_service(settings).embed_documents(
        source_kind=kind, manufacturer=manufacturer
    )
    _out(f"Embedded documents: {len(result.succeeded)} ok, {len(result.failed)} failed")
    for o in result.succeeded:
        s = o.stats
        _out(
            f"  {o.file_path}  chunks={s.chunks} texts={s.distinct_texts} "
            f"embedded={s.embedded} reused={s.reused}  {o.seconds:.1f}s"
        )
    return _report_failures(result)


def run_retag(service: IngestionService) -> int:
    """Re-tag product types on stored chunks and print what changed."""
    result = service.retag_products()
    changed = sum(n for _, n in result.succeeded)
    _out(f"Re-tagged {len(result.succeeded)} documents, {changed} chunk tags changed")
    for path, n in result.succeeded:
        if n:
            _out(f"  {path}: {n} changed")
    return _report_failures(result)


def run_classify_sections(service: IngestionService) -> int:
    """Rebuild sections and chunk categories of stored documents and print the counts."""
    result = service.classify_sections()
    total = sum(n for _, n in result.succeeded)
    _out(f"Classified {len(result.succeeded)} documents into {total} sections")
    return _report_failures(result)


def build_parser() -> argparse.ArgumentParser:
    """Argument parser with one sub-command per job."""
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    mfr = sub.add_parser("ingest-manufacturers", help="ingest registered catalogues")
    mfr.add_argument("--category", default=None)
    for name, text in [
        ("check-links", "offline: list linked documents missing from tender.yaml"),
        ("fetch-links", "download missing linked documents"),
        ("ingest-tenders", "store tender files as pages + chunks"),
    ]:
        sub.add_parser(name, help=text).add_argument("--tender", default=None)
    everything = sub.add_parser("ingest-all", help="catalogues + tenders")
    everything.add_argument("--fetch", action="store_true", help="fetch missing links first")
    sub.add_parser("tag-products", help="re-tag product types on stored chunks")
    sub.add_parser("classify-sections", help="rebuild sections and chunk categories")
    embed = sub.add_parser("embed", help="embed chunks that have no vector yet")
    embed.add_argument("--source", choices=["manufacturer", "tender", "all"], default="all")
    embed.add_argument("--manufacturer", default=None)
    for name in ("ingest-manufacturers", "ingest-tenders", "ingest-all"):
        sub.choices[name].add_argument(
            "--reprocess", action="store_true", help="re-parse files that are already stored"
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command.

    Args:
        argv: Arguments (defaults to `sys.argv[1:]`).

    Returns:
        Process exit code: 0 if everything succeeded, 1 otherwise.
    """
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.LOG_LEVEL, json_output=settings.LOG_JSON)
    if args.command == "embed":
        return 1 if run_embed(settings, args.source, args.manufacturer) else 0
    service = build_ingestion_service(settings)
    failures = 0
    match args.command:
        case "ingest-manufacturers":
            failures = run_manufacturers(service, args.category, reprocess=args.reprocess)
        case "tag-products":
            failures = run_retag(service)
        case "classify-sections":
            failures = run_classify_sections(service)
        case "check-links":
            failures = run_check_links(service, args.tender)
        case "fetch-links":
            failures = run_fetch_links(service, args.tender)
        case "ingest-tenders":
            failures = run_tenders(service, args.tender, reprocess=args.reprocess)
        case "ingest-all":
            if args.fetch:
                failures += run_fetch_links(service, None)
            failures += run_manufacturers(service, None, reprocess=args.reprocess)
            failures += run_tenders(service, None, reprocess=args.reprocess)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
