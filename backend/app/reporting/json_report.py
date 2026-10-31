"""JSON rendering of a `TenderReport` (the report schema is the contract)."""

from pathlib import Path

from app.schemas.report import TenderReport


def report_json(report: TenderReport) -> str:
    """The report as indented JSON (UTF-8 text, quotes kept verbatim)."""
    return report.model_dump_json(indent=2)


def write_json_report(report: TenderReport, path: Path) -> Path:
    """Write the report to `path`, creating parent folders.

    Args:
        report: The match report.
        path: Output file.

    Returns:
        The path written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_json(report), encoding="utf-8")
    return path
