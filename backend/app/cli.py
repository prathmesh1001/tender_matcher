"""Command-line entry point for ingestion (thin: parse args, call the service, print a summary).

Usage (from backend/):
    python -m app.cli ingest-manufacturers [--category valves_pumps]
    python -m app.cli check-links [--tender GEM_2026_B_8119613]   # offline, no downloads
    python -m app.cli fetch-links [--tender ...]                    # downloads missing links
    python -m app.cli ingest-tenders [--tender ...]
    python -m app.cli tag-products                                  # no re-parsing
    python -m app.cli embed [--source manufacturer|tender|all] [--manufacturer NAME]
    python -m app.cli index [--category valves_pumps] [--reprocess]  # catalogues + their vectors
    python -m app.cli index-status                                  # chunks, vectors, facts
    python -m app.cli ingest-all [--fetch] [--reprocess]            # catalogues + tenders
    python -m app.cli match-tender --tender GEM_2026_B_8119613 [--no-ontology] [--out FILE]
    python -m app.cli parse-tender --tender GEM_2026_B_8119613      # requirements, no matching
    python -m app.cli gold-diff --gold ../eval/gold/8119613.yaml [--out FILE]
    python -m app.cli extract-specs [--manufacturer NAME] [--reprocess]   # calls the LLM

Exit code 1 if any file failed.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.exceptions import InvalidInputError
from app.core.logging import configure_logging
from app.ingestion.pipeline import IngestOutcome
from app.reporting.gold_diff import gold_diff, load_gold, render_gold_diff
from app.reporting.json_report import write_json_report
from app.schemas.enums import DocumentSource
from app.schemas.report import SingleFirmWarning
from app.schemas.requirements import Requirement
from app.services.indexing_service import build_indexing_service
from app.services.ingestion_service import (
    BatchResult,
    IngestionService,
    build_ingestion_service,
)
from app.services.match_service import build_match_service
from app.services.spec_service import build_spec_service
from app.tender.documents import TenderIngestOutcome
from app.tender.link_fetcher import CrawlReport
from app.tender.splitter import leaves


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


def run_match(settings: Settings, tender: str, *, use_ontology: bool, out: str | None) -> int:
    """Match one tender, write the JSON report, print the ranking and item summaries."""
    report = build_match_service(settings).match_tender(tender, use_ontology=use_ontology)
    suffix = "" if use_ontology else "_no_ontology"
    path = write_json_report(
        report, Path(out) if out else settings.REPORT_DIR / f"{tender}{suffix}.json"
    )
    _report_single_firm(report.single_firm)
    _out(f"Tender {report.bid_number}: {len(report.items)} items, ontology={use_ontology}")
    for item in report.items:
        _out(f"  item {item.item.item_no} {item.item.title} -> {item.product_type}")
        for m in item.manufacturers:
            doc = m.chosen.document if m.chosen else "no catalogue"
            _out(
                f"    {m.manufacturer:20} {_pct(m.score.compatibility_pct)} cov "
                f"{m.score.coverage_pct:5.1f}%  {doc}"
            )
    _out("Ranking:")
    for r in report.ranking:
        _out(
            f"  {r.rank}. {r.manufacturer:20} {_pct(r.score.compatibility_pct)} cov "
            f"{r.score.coverage_pct:5.1f}%  review={r.score.needs_review}"
            f"{'  LOW COVERAGE' if r.low_coverage else ''}"
        )
    _out(f"Report: {path}")
    return 0


def _report_single_firm(warning: SingleFirmWarning | None) -> None:
    """Print the single-firm warning, if any, before everything else."""
    if warning is None:
        return
    firm = ", ".join(warning.makes) or "not named"
    _out(f"WARNING: tender says only one firm is acceptable: {firm}")
    for citation in warning.citations:
        _out(f"  {citation.document} p{citation.page_number}: {citation.quote[:100]}")


def run_parse_tender(settings: Settings, tender: str) -> int:
    """Print one tender's items, its requirements per category and the makes it names."""
    parsed = build_match_service(settings).parse_tender(tender)
    _report_single_firm(parsed.single_firm)
    _out(
        f"Tender {parsed.bid_number}: {len(parsed.files)} files, {len(parsed.items)} items "
        f"from {parsed.item_source.value}"
    )
    atomic: list[Requirement] = []
    for entry in parsed.items:
        found = leaves(entry.requirements)
        atomic.extend(found)
        _out(f"  item {entry.item.item_no} {entry.item.title}: {len(found)} requirements")
    everything = [*atomic, *parsed.general]
    _out(f"Requirements: {len(atomic)} on items, {len(parsed.general)} general")
    for name, count in Counter(r.category or "other" for r in everything).most_common():
        _out(f"  {count:5} {name}")
    _out(f"Named makes: {', '.join(parsed.named_makes) or 'none'}")
    return 0


def run_index_status(settings: Settings) -> int:
    """Print chunks, vectors and spec facts per stored catalogue."""
    rows = build_indexing_service(settings).status()
    for r in rows:
        _out(f"  {r.file_path}  chunks={r.chunks} vectors={r.with_vector} facts={r.spec_facts}")
    chunks, vectors = sum(r.chunks for r in rows), sum(r.with_vector for r in rows)
    facts = sum(r.spec_facts for r in rows)
    _out(f"Index: {len(rows)} catalogues, {chunks} chunks, {vectors} with vector, {facts} facts")
    return 0


def run_index(
    settings: Settings, service: IngestionService, category: str | None, *, reprocess: bool
) -> int:
    """Ingest catalogues, embed their new chunks and print the resulting index; returns failures."""
    failures = run_manufacturers(service, category, reprocess=reprocess)
    failures += run_embed(settings, DocumentSource.MANUFACTURER.value, None)
    run_index_status(settings)
    return failures


def run_extract_specs(settings: Settings, manufacturer: str | None, *, reprocess: bool) -> int:
    """Extract verified spec facts with the LLM and print one line per document."""
    try:
        service = build_spec_service(settings)
    except InvalidInputError as exc:
        _out(f"Cannot extract: {exc.message}")
        return 1
    result = service.extract(manufacturer=manufacturer, reprocess=reprocess)
    _out(f"Spec facts: {len(result.succeeded)} documents ok, {len(result.failed)} failed")
    for o in result.succeeded:
        dropped = ", ".join(f"{r.value}={n}" for r, n in sorted(o.dropped.items()))
        _out(f"  {o.file_path}  proposed={o.proposed} stored={o.stored}  dropped: {dropped or '-'}")
    return _report_failures(result)


def run_gold_diff(settings: Settings, gold_file: str, out: str | None) -> int:
    """Match the gold's tender with and without the ontology and write the diff as markdown."""
    gold = load_gold(Path(gold_file))
    service = build_match_service(settings)
    runs = {
        "ontology": service.match_tender(gold.tender),
        "no ontology": service.match_tender(gold.tender, use_ontology=False),
    }
    path = Path(out) if out else settings.REPORT_DIR / f"{gold.tender}_gold_diff.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_gold_diff(gold_diff(gold, runs)), encoding="utf-8")
    _out(f"Gold diff: {path}")
    return 0


def _pct(value: float | None) -> str:
    """A percentage for the summary, or a dash when nothing was decided."""
    return f"{value:5.1f}%" if value is not None else "    - "


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
    index = sub.add_parser("index", help="ingest catalogues and embed their chunks")
    index.add_argument("--category", default=None)
    index.add_argument("--reprocess", action="store_true", help="re-parse stored catalogues")
    sub.add_parser("index-status", help="chunks, vectors and spec facts per catalogue")
    match = sub.add_parser("match-tender", help="rules-only match of one tender -> JSON report")
    match.add_argument("--tender", required=True, help="tender folder, e.g. GEM_2026_B_8119613")
    match.add_argument("--no-ontology", action="store_true", help="run without any ontology")
    match.add_argument("--out", default=None, help="report path (default: REPORT_DIR)")
    parse = sub.add_parser("parse-tender", help="print a tender's requirements, no matching")
    parse.add_argument("--tender", required=True, help="tender folder, e.g. GEM_2026_B_8119613")
    diff = sub.add_parser("gold-diff", help="compare match results with a gold file -> markdown")
    diff.add_argument("--gold", required=True, help="gold file, e.g. ../eval/gold/8119613.yaml")
    diff.add_argument("--out", default=None, help="diff path (default: REPORT_DIR)")
    specs = sub.add_parser("extract-specs", help="LLM spec facts from stored catalogues")
    specs.add_argument("--manufacturer", default=None)
    specs.add_argument("--reprocess", action="store_true", help="replace facts already stored")
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
    if args.command == "match-tender":
        return run_match(settings, args.tender, use_ontology=not args.no_ontology, out=args.out)
    if args.command == "parse-tender":
        return run_parse_tender(settings, args.tender)
    if args.command == "gold-diff":
        return run_gold_diff(settings, args.gold, args.out)
    if args.command == "extract-specs":
        return run_extract_specs(settings, args.manufacturer, reprocess=args.reprocess)
    if args.command == "index-status":
        return run_index_status(settings)
    service = build_ingestion_service(settings)
    failures = 0
    match args.command:
        case "index":
            failures = run_index(settings, service, args.category, reprocess=args.reprocess)
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
