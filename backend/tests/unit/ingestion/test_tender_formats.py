"""CSV / XLSX / DOCX readers. All content is synthetic."""

import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.core.exceptions import DocumentParseError
from app.ingestion.chunker import ChunkerOptions
from app.ingestion.docx_reader import DocxTable, docx_page, read_docx_items
from app.ingestion.tabular import read_csv, read_xlsx, sheet_chunks
from app.schemas.enums import ChunkType
from tests.conftest import make_test_settings

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
RESTART = '<w:vMerge w:val="restart"/>'
CONTINUE = "<w:vMerge/>"
SPAN_2 = '<w:gridSpan w:val="2"/>'


@pytest.fixture
def options() -> ChunkerOptions:
    return ChunkerOptions.from_settings(make_test_settings())


def test_csv_rows_become_chunks_with_header(tmp_path: Path, options: ChunkerOptions) -> None:
    path = tmp_path / "boq_items.csv"
    path.write_text(
        "Item No,Item Title,Qty\n1,Gate valve 80 NB,4\n,,\n2,Ball valve,2\n", encoding="utf-8-sig"
    )
    [sheet] = read_csv(path)
    assert sheet.rows[0] == ["Item No", "Item Title", "Qty"]
    chunks = sheet_chunks(sheet, options)
    assert [c.raw_text for c in chunks] == [
        "boq_items.csv | Item No: 1; Item Title: Gate valve 80 NB; Qty: 4",
        "boq_items.csv | Item No: 2; Item Title: Ball valve; Qty: 2",
    ]
    assert all(c.chunk_type == ChunkType.TABLE_ROW and c.page_number == 1 for c in chunks)
    assert [c.position for c in chunks] == [0, 1]


def test_csv_falls_back_to_windows_1252(tmp_path: Path) -> None:
    path = tmp_path / "b.csv"
    path.write_bytes("Size,Temp\n3\x94,120 \xb0C\n".encode("latin-1"))
    [sheet] = read_csv(path)
    assert sheet.rows[1][1] == "120 °C"


def test_xlsx_sheets_are_pages(tmp_path: Path, options: ChunkerOptions) -> None:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    first.title = "Specs"
    first.append(["Parameter", "Value"])
    first.append(["Body", "SS316"])
    second = workbook.create_sheet("Terms")
    second.append(["Clause"])
    path = tmp_path / "atc.xlsx"
    workbook.save(path)
    sheets = read_xlsx(path)
    assert [(s.page_number, s.name) for s in sheets] == [(1, "Specs"), (2, "Terms")]
    assert sheets[0].text == "Parameter | Value\nBody | SS316"
    assert [c.raw_text for c in sheet_chunks(sheets[0], options)] == [
        "Specs | Parameter: Body; Value: SS316"
    ]
    assert sheet_chunks(sheets[1], options) == []  # header only


def test_xlsx_cell_lines_are_kept(tmp_path: Path, options: ChunkerOptions) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["Item No", "Description"])
    sheet.append(["10", "SIZE : 250 MM\n\nBODY MOC :  WCB"])
    sheet.append(["20", "SIZE : 300 MM"])
    path = tmp_path / "boq.xlsx"
    workbook.save(path)
    [read] = read_xlsx(path)
    multi, single = sheet_chunks(read, options)
    assert multi.meta["cells"] == ["10", "SIZE : 250 MM BODY MOC : WCB"]  # cells as before
    assert multi.meta["cell_lines"] == [["10"], ["SIZE : 250 MM", "BODY MOC : WCB"]]
    assert "cell_lines" not in single.meta  # one line per cell: nothing to keep


def test_bad_xlsx_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(DocumentParseError):
        read_xlsx(path)


def _cell(text: str, props: str = "") -> str:
    tc_pr = f"<w:tcPr>{props}</w:tcPr>" if props else ""
    return f"<w:tc>{tc_pr}{_para(text)}</w:tc>"


def _write_docx(path: Path, body: str) -> Path:
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return path


def _para(text: str) -> str:
    return f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"


def test_docx_reads_paragraphs_and_tables_in_order(tmp_path: Path) -> None:
    table = (
        "<w:tbl>"
        f"<w:tr>{_cell('Part')}{_cell('Grade A')}{_cell('Grade B')}</w:tr>"
        f"<w:tr>{_cell('Body', RESTART)}{_cell('WCB')}{_cell('CF8M')}</w:tr>"
        f"<w:tr>{_cell('', CONTINUE)}{_cell('Graphite', SPAN_2)}</w:tr>"
        "</w:tbl>"
    )
    path = _write_docx(
        tmp_path / "atc.docx",
        _para("TECHNICAL ANNEXURE") + table + _para("Delivery within 8 weeks."),
    )
    items = read_docx_items(path)
    assert items[0] == "TECHNICAL ANNEXURE"
    assert isinstance(items[1], DocxTable)
    # Vertical merge continues (None); horizontal span repeats its own text.
    assert items[1].rows[2] == [None, "Graphite", "Graphite"]
    assert items[2] == "Delivery within 8 weeks."


def test_docx_page_feeds_the_chunker(tmp_path: Path, options: ChunkerOptions) -> None:
    from app.ingestion.chunker import chunk_document

    table = (
        "<w:tbl>"
        f"<w:tr>{_cell('Parameter')}{_cell('Value')}</w:tr>"
        f"<w:tr>{_cell('Pressure')}{_cell('PN 16')}</w:tr>"
        "</w:tbl>"
    )
    path = _write_docx(
        tmp_path / "a.docx",
        _para("Technical Requirements") + table + _para("All valves hydro tested."),
    )
    page, tables = docx_page(read_docx_items(path))
    chunks = chunk_document([page], {1: tables}, options)
    assert [(c.chunk_type, c.section, c.raw_text) for c in chunks] == [
        (
            ChunkType.TABLE_ROW,
            "Technical Requirements",
            "Technical Requirements | Parameter: Pressure; Value: PN 16",
        ),
        (ChunkType.PROSE, "Technical Requirements", "All valves hydro tested."),
    ]
    assert "Parameter | Value" in page.text


def test_bad_docx_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("other.xml", "<x/>")
    with pytest.raises(DocumentParseError):
        read_docx_items(path)
