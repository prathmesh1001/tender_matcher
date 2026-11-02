"""The report as an Excel workbook a bid manager can read: nothing here decides anything.

Four sheets: Summary (warnings, ranking, how the % is worked out), Requirements (one row per
requirement x manufacturer), By status (counts and lists) and General (outside the score). Text
comes from tender and catalogue PDFs, so every cell is written as plain text, never a formula.
"""

from collections.abc import Iterable, Mapping, Sequence
from io import BytesIO
from typing import Final

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE, Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.schemas.enums import ExtractionStatus, MatchStatus
from app.schemas.matching import Comparison
from app.schemas.report import ManufacturerItemReport, TenderReport
from app.schemas.requirements import Requirement

Value = str | int | float | None

_FORMULA_TYPE: Final = "s"
_WRAP: Final = Alignment(wrap_text=True, vertical="top")
_BOLD: Final = Font(bold=True)
_TITLE: Final = Font(bold=True, size=14)
_WARNING: Final = Font(bold=True, color="B91C1C")
_HEADER_FILL: Final = PatternFill("solid", fgColor="E7E8EE")
_DECIMALS: Final = 1
_NOT_EVIDENCE: Final = "No evidence. Cited for review only, this decides nothing:"
# Same colours as the report screen (frontend/src/index.css), as pale backgrounds.
_STATUS_FILL: Final[Mapping[MatchStatus, PatternFill]] = {
    MatchStatus.FULL_MATCH: PatternFill("solid", fgColor="DCFCE7"),
    MatchStatus.PARTIAL_MATCH: PatternFill("solid", fgColor="FEF3C7"),
    MatchStatus.MISSING_CAPABILITY: PatternFill("solid", fgColor="FEE2E2"),
    MatchStatus.UNSUPPORTED: PatternFill("solid", fgColor="F3F4F6"),
    MatchStatus.NOT_APPLICABLE: PatternFill("solid", fgColor="F1F5F9"),
}
_REQUIREMENT_COLUMNS: Final = (
    ("Item", 10),
    ("Requirement", 50),
    ("Manufacturer", 18),
    ("Capability", 36),
    ("Status", 20),
    ("Score", 8),
    ("Document", 34),
    ("Page", 8),
    ("Quote", 60),
    ("Needs review", 12),
    ("Reason", 44),
)
_BY_STATUS_COLUMNS: Final = (
    ("Manufacturer", 18),
    ("Status", 20),
    ("Item", 10),
    ("Requirement", 70),
    ("Needs review", 12),
)
_GENERAL_COLUMNS: Final = (
    ("Category", 22),
    ("Requirement", 70),
    ("Status", 20),
    ("Document", 22),
    ("Page", 8),
    ("Mandatory", 10),
    ("Needs review", 12),
    ("Reason", 44),
)


def _clean(value: Value) -> Value:
    """A value Excel can store: control characters in PDF text would make it refuse the file."""
    return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def _write_row(sheet: Worksheet, values: Sequence[Value]) -> list[Cell]:
    """Append one row of wrapped cells; text stays text even if it starts with "="."""
    sheet.append([_clean(v) for v in values])
    cells: list[Cell] = []
    for cell in sheet[sheet.max_row]:
        assert isinstance(cell, Cell)  # noqa: S101 - rows of a normal sheet hold normal cells
        cell.alignment = _WRAP
        if isinstance(cell.value, str):
            cell.data_type = _FORMULA_TYPE
        cells.append(cell)
    return cells


def _table(sheet: Worksheet, columns: Sequence[tuple[str, int]]) -> None:
    """Start a filterable table at the top of a sheet."""
    for cell in _write_row(sheet, [name for name, _ in columns]):
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
    for number, (_, width) in enumerate(columns, start=1):
        sheet.column_dimensions[get_column_letter(number)].width = width
    sheet.freeze_panes = "A2"


def _finish_table(sheet: Worksheet) -> None:
    sheet.auto_filter.ref = sheet.dimensions


def _pct(value: float | None) -> Value:
    return "not decided" if value is None else round(value, _DECIMALS)


def _warnings(report: TenderReport) -> list[str]:
    """What a reader must know before trusting the numbers."""
    lines: list[str] = []
    quality = report.extraction_quality
    if quality.status is ExtractionStatus.UNRELIABLE:
        lines.append("Extraction unreliable: every result below needs review.")
        lines.extend(f"{issue.code.value}: {issue.reason}" for issue in quality.reasons)
    if report.single_firm is not None:
        firm = ", ".join(report.single_firm.makes) or "not named"
        lines.append(f"The tender says only one firm is acceptable: {firm}")
        lines.extend(
            f"{c.document} p{c.page_number}: {c.quote}" for c in report.single_firm.citations
        )
    return lines


def _summary_sheet(sheet: Worksheet, report: TenderReport) -> None:
    sheet.title = "Summary"
    sheet.column_dimensions["A"].width = 26
    for column in "BCDEFG":
        sheet.column_dimensions[column].width = 16
    _write_row(sheet, [f"Tender {report.bid_number}"])[0].font = _TITLE
    facts: list[tuple[str, Value]] = [
        ("Revision of bid.pdf", report.tender_revision or "not recorded"),
        ("bid.pdf sha256", report.bid_sha256 or "not recorded"),
        ("Files read", ", ".join(report.tender_files)),
        ("Generated", report.generated_at.isoformat(timespec="seconds")),
        ("Decided by", report.decided_by),
        ("Extraction", report.extraction_quality.status.value),
    ]
    for label, value in facts:
        _write_row(sheet, [label, value])[0].font = _BOLD
    warnings = _warnings(report)
    if warnings:
        sheet.append([])
        _write_row(sheet, ["Warnings"])[0].font = _WARNING
        for line in warnings:
            _write_row(sheet, [line])[0].font = _WARNING
            _span(sheet)
    _ranking_block(sheet, report)
    sheet.append([])
    _write_row(sheet, ["How the % is calculated"])[0].font = _BOLD
    _write_row(sheet, [report.score_explanation])
    _span(sheet, height=120)


def _span(sheet: Worksheet, height: int | None = None) -> None:
    """Let the last row's text use the sheet's width."""
    sheet.merge_cells(start_row=sheet.max_row, start_column=1, end_row=sheet.max_row, end_column=7)
    if height is not None:
        sheet.row_dimensions[sheet.max_row].height = height


def _ranking_block(sheet: Worksheet, report: TenderReport) -> None:
    sheet.append([])
    _write_row(sheet, ["Ranking"])[0].font = _BOLD
    header = ("Rank", "Manufacturer", "Compatibility %", "Coverage %", "Decided", "Total", "Note")
    for cell in _write_row(sheet, header):
        cell.font = _BOLD
        cell.fill = _HEADER_FILL
    for entry in report.ranking:
        score = entry.score
        _write_row(
            sheet,
            [
                entry.rank,
                entry.manufacturer,
                _pct(score.compatibility_pct),
                round(score.coverage_pct, _DECIMALS),
                score.decided,
                score.total,
                "low coverage" if entry.low_coverage else "",
            ],
        )


def _result_sets(m: ManufacturerItemReport) -> Iterable[tuple[str | None, Sequence[Comparison]]]:
    """Each catalogue's own results: every tied one, else the chosen one."""
    if m.tied_rows:
        for tied in m.tied_rows:
            yield f"{tied.document} (tied)", tied.results
    else:
        yield (m.chosen.document if m.chosen else None), m.results


def _quotes(result: Comparison) -> str:
    """The cited text; on an undecided row it is marked, because it proves nothing."""
    quotes = "\n".join(e.quote for e in result.evidence)
    if quotes and result.status is MatchStatus.UNSUPPORTED:
        return f"{_NOT_EVIDENCE}\n{quotes}"
    return quotes


def _requirement_row(
    item_no: str,
    requirement: Requirement,
    manufacturer: str,
    catalogue: str | None,
    result: Comparison,
    scores: Mapping[MatchStatus, float],
) -> list[Value]:
    return [
        item_no,
        requirement.raw_text,
        manufacturer,
        result.capability or "",
        result.status.value,
        scores.get(result.status),
        catalogue or "",
        ", ".join(str(page) for page in sorted({e.page_number for e in result.evidence})),
        _quotes(result),
        "yes" if result.needs_review else "no",
        result.reason,
    ]


def _status_cell(cells: Sequence[Cell], index: int, status: MatchStatus) -> None:
    cells[index].fill = _STATUS_FILL[status]


def _requirements_sheet(sheet: Worksheet, report: TenderReport) -> None:
    sheet.title = "Requirements"
    _table(sheet, _REQUIREMENT_COLUMNS)
    status_column = [name for name, _ in _REQUIREMENT_COLUMNS].index("Status")
    for item in report.items:
        requirements = {r.id: r for r in item.requirements}
        for m in item.manufacturers:
            for catalogue, results in _result_sets(m):
                for result in results:
                    cells = _write_row(
                        sheet,
                        _requirement_row(
                            item.item.item_no,
                            requirements[result.requirement_id],
                            m.manufacturer,
                            catalogue,
                            result,
                            report.status_scores,
                        ),
                    )
                    _status_cell(cells, status_column, result.status)
    _finish_table(sheet)


def _by_status_sheet(sheet: Worksheet, report: TenderReport) -> None:
    sheet.title = "By status"
    _table(sheet, _BY_STATUS_COLUMNS)
    for block in report.status_lists:
        for status, entries in block.lists.items():
            for entry in entries:
                cells = _write_row(
                    sheet,
                    [
                        block.manufacturer,
                        status.value,
                        entry.item_no,
                        entry.requirement,
                        "yes" if entry.needs_review else "no",
                    ],
                )
                _status_cell(cells, 1, status)
    _finish_table(sheet)


def _general_sheet(sheet: Worksheet, report: TenderReport) -> None:
    sheet.title = "General"
    _table(sheet, _GENERAL_COLUMNS)
    pairs = zip(report.general_requirements, report.general_results, strict=True)
    for requirement, result in pairs:
        mandatory = {True: "yes", False: "no", None: ""}[requirement.mandatory]
        cells = _write_row(
            sheet,
            [
                requirement.category or "",
                requirement.raw_text,
                result.status.value,
                requirement.document,
                requirement.page_number,
                mandatory,
                "yes" if result.needs_review else "no",
                result.reason,
            ],
        )
        _status_cell(cells, 2, result.status)
    _finish_table(sheet)


def excel_report(report: TenderReport) -> bytes:
    """Render a report as an .xlsx file.

    Args:
        report: The report.

    Returns:
        The workbook's bytes.
    """
    book = Workbook()
    summary = book.active
    assert isinstance(summary, Worksheet)  # noqa: S101 - a new workbook has one normal sheet
    _summary_sheet(summary, report)
    _requirements_sheet(book.create_sheet(), report)
    _by_status_sheet(book.create_sheet(), report)
    _general_sheet(book.create_sheet(), report)
    output = BytesIO()
    book.save(output)
    return output.getvalue()
