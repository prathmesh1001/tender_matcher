"""Command-line entry point for ingestion (thin: parse args, call the service, print a summary).

Usage (from backend/):
    python -m app.cli ingest-manufacturers [--category valves_pumps]
    python -m app.cli check-links [--tender GEM_2026_B_8119613]   # offline, no downloads
    python -m app.cli fetch-links [--tender ...]                    # downloads missing links
    python -m app.cli ingest-tenders [--tender ...]
    python -m app.cli ingest-all [--fetch]                          # catalogues + tenders

Exit code 1 if any file failed.
"""

import argparse
import sys
from collections.abc import Sequence

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.ingestion.pipeline import IngestOutcome
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


def run_manufacturers(service: IngestionService, category: str | None) -> int:
    """Ingest catalogues and print one line per file."""
    result = service.ingest_manufacturers(category)
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


def run_tenders(service: IngestionService, tender: str | None) -> int:
    """Ingest tender documents and print one line per file."""
    result = service.ingest_tenders(tender)
    _out(f"Tenders: {len(result.succeeded)} ok, {len(result.failed)} failed")
    _report_tenders(result.succeeded)
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
    service = build_ingestion_service(settings)
    failures = 0
    match args.command:
        case "ingest-manufacturers":
            failures = run_manufacturers(service, args.category)
        case "check-links":
            failures = run_check_links(service, args.tender)
        case "fetch-links":
            failures = run_fetch_links(service, args.tender)
        case "ingest-tenders":
            failures = run_tenders(service, args.tender)
        case "ingest-all":
            if args.fetch:
                failures += run_fetch_links(service, None)
            failures += run_manufacturers(service, None)
            failures += run_tenders(service, None)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
