"""Scanned tender tables rebuilt from OCR word boxes (CLAUDE.md C5, A7).

Layouts are synthetic, shaped like the two scanned GeM specs seen (8011173 read column by
column; 8169451 with label and value on one OCR line and a separate serial column).
"""

import re
from dataclasses import replace
from pathlib import Path

import pymupdf
import pytest

from app.core.config import Settings
from app.ingestion.ocr import OcrLine, OcrResult, OcrWord
from app.ingestion.ocr_table import (
    OcrTableOptions,
    is_serial_word,
    ocr_tables,
    repair_codes,
    row_bands,
    serial_prefix,
)
from app.ingestion.pdf_parser import ParserOptions, ocr_words_to_blocks, parse_pdf
from app.ingestion.processing import scanned_tables
from app.retrieval.text_normalizer import SearchVocabulary
from app.schemas.chunking import ExtractedTable
from app.schemas.parsing import BBox, ParsedPage, TextBlock

H = 10.0  # word height in points
PITCH = 16.0  # row pitch
LABEL_X = 60.0
VALUE_X = 300.0
CHAR_W = 5.0

CODE_RULES = (
    (re.compile(r"\b(?:ss|aisi)\s*-?\s*304\b|\b304\s*ss\b"), "ss304"),
    (re.compile(r"\b(?:ss|aisi)\s*-?\s*410\b|\b410\s*ss\b"), "ss410"),
    (re.compile(r"\bpn\s*-?\s*(\d+)\b"), r"pn\1"),
    (re.compile(r"\bis\s*[:-]?\s*(\d{3,5})\b"), r"is\1"),
)
CONFUSIONS = {"5": "s", "s": "5", "0": "o", "o": "0", "1": "li", "l": "1", "i": "1"}


def options(**overrides: object) -> OcrTableOptions:
    values: dict[str, object] = {
        "cell_gap_factor": 2.5,
        "max_row_height_factor": 2.0,
        "min_rows": 3,
        "serial_max_chars": 3,
        "prose_width_fraction": 0.6,
        "code_rules": CODE_RULES,
        "confusions": CONFUSIONS,
    }
    values.update(overrides)
    return OcrTableOptions(**values)  # type: ignore[arg-type]


def words(text: str, x: float, y: float, conf: float = 0.95, height: float = H) -> list[TextBlock]:
    """Words of `text` from x at top y, one space (a char width) apart."""
    out: list[TextBlock] = []
    for word in text.split():
        width = len(word) * CHAR_W
        out.append(
            TextBlock(
                text=word, bbox=BBox(x0=x, y0=y, x1=x + width, y1=y + height), confidence=conf
            )
        )
        x += width + CHAR_W
    return out


def row_texts(tables: list[ExtractedTable]) -> list[list[str]]:
    return [row.cells for table in tables for row in table.rows]


def test_labels_and_values_read_column_by_column_are_paired_by_row() -> None:
    labels = ["Type", "No. of stages", "Rated Capacity at duty Point", "Head @ duty point"]
    values = ["SS Submersible pump", "Single", "1500 lpm", "54 mwe"]
    page = [w for i, t in enumerate(labels) for w in words(t, LABEL_X, 100 + i * PITCH)]
    # Values come after all labels, as Tesseract emits a separate block, and sit 1 pt lower.
    page += [w for i, t in enumerate(values) for w in words(t, VALUE_X, 101 + i * PITCH)]
    assert row_texts(ocr_tables(page, 1, options())) == [
        [label, value] for label, value in zip(labels, values, strict=True)
    ]


def test_label_and_value_on_one_ocr_line_are_split_at_the_wide_gap() -> None:
    page: list[TextBlock] = []
    for i, (label, value) in enumerate(
        [("Casing", "CAST IRON IS 210"), ("impeller", "BRONZE"), ("shaft", "SS ASTM A276")]
    ):
        page += words(f"{label}", LABEL_X, 100 + i * PITCH) + words(value, VALUE_X, 100 + i * PITCH)
    assert row_texts(ocr_tables(page, 1, options())) == [
        ["Casing", "CAST IRON IS 210"],
        ["impeller", "BRONZE"],
        ["shaft", "SS ASTM A276"],
    ]


def test_separate_serial_column_and_tall_noise_are_dropped() -> None:
    page: list[TextBlock] = []
    rows = [
        ("1", "Pump Description", "Centrifugal"),
        ("if", "Pump Seal", "Mechanical Seal"),
        ("00", "Liquid", "Water"),
    ]
    for i, (serial, label, value) in enumerate(rows):
        y = 100 + i * PITCH
        page += words(serial, 20, y) + words(label, LABEL_X, y) + words(value, VALUE_X, y)
    # Merged serial numbers read as one word spanning all rows.
    page += words("Diu}", 20, 100, conf=0.27, height=3 * PITCH)
    assert row_texts(ocr_tables(page, 1, options())) == [
        ["Pump Description", "Centrifugal"],
        ["Pump Seal", "Mechanical Seal"],
        ["Liquid", "Water"],
    ]


def test_serial_number_glued_to_the_label_is_stripped_but_short_labels_stay() -> None:
    page: list[TextBlock] = []
    rows = [
        ("7. | Type of encloser", "TEFC"),
        ("4. | RPM", "2900 RPM"),
        ("RPM", "2900"),
        ("S| End connection", "100mm Flange end"),
    ]
    for i, (label, value) in enumerate(rows):
        page += words(label, LABEL_X, 100 + i * PITCH) + words(value, VALUE_X, 100 + i * PITCH)
    assert row_texts(ocr_tables(page, 1, options())) == [
        ["Type of encloser", "TEFC"],
        ["RPM", "2900 RPM"],
        ["RPM", "2900"],
        ["End connection", "100mm Flange end"],
    ]


def test_wrapped_value_lines_continue_their_row() -> None:
    page = words("Spare along with pump", LABEL_X, 100) + words("Bronze Impeller -01", VALUE_X, 100)
    page += words("Mechanical Seal -01", VALUE_X, 100 + PITCH)
    page += words("Pump make", LABEL_X, 100 + 2 * PITCH) + words("KBL", VALUE_X, 100 + 2 * PITCH)
    page += words("Pump Seal", LABEL_X, 100 + 3 * PITCH) + words("Gland", VALUE_X, 100 + 3 * PITCH)
    (table,) = ocr_tables(page, 1, options())
    assert table.rows[0].cells == [
        "Spare along with pump",
        "Bronze Impeller -01 Mechanical Seal -01",
    ]
    assert table.rows[0].cell_lines == [
        ["Spare along with pump"],
        ["Bronze Impeller -01", "Mechanical Seal -01"],
    ]
    assert len(table.rows) == 3


def test_value_centred_over_two_lines_joins_the_label_below_it() -> None:
    page = words("RPM", LABEL_X, 100) + words("2900 RPM", VALUE_X, 100)
    page += words("Dry Type induction motor", VALUE_X, 100 + PITCH)
    page += words("1. Type of motor", LABEL_X, 100 + 2 * PITCH)
    page += words("confirming to IS:325", VALUE_X, 100 + 3 * PITCH)
    page += words("Duty", LABEL_X, 100 + 4 * PITCH) + words("S1", VALUE_X, 100 + 4 * PITCH)
    page += words("Cooling", LABEL_X, 100 + 5 * PITCH) + words("Oil", VALUE_X, 100 + 5 * PITCH)
    assert row_texts(ocr_tables(page, 1, options())) == [
        ["RPM", "2900 RPM"],
        ["Type of motor", "Dry Type induction motor confirming to IS:325"],
        ["Duty", "S1"],
        ["Cooling", "Oil"],
    ]


def test_full_width_sentence_ends_the_table_and_short_runs_are_no_table() -> None:
    page: list[TextBlock] = []
    for i in range(3):
        page += words(f"Label{i}", LABEL_X, 100 + i * PITCH) + words("v", VALUE_X, 100 + i * PITCH)
    page += words(
        "Bidder shall provide the data sheet of items along with the bid " * 2,
        LABEL_X,
        100 + 3 * PITCH,
    )
    for i in range(2):  # only two rows after the sentence: not a table
        y = 100 + (4 + i) * PITCH
        page += words(f"Other{i}", LABEL_X, y) + words("w", VALUE_X, y)
    tables = ocr_tables(page, 1, options())
    assert row_texts(tables) == [["Label0", "v"], ["Label1", "v"], ["Label2", "v"]]
    assert tables[0].key_value
    assert tables[0].header == ["", ""]


def test_page_without_table_gives_nothing() -> None:
    page = [
        w for i in range(4) for w in words("One sentence of prose " * 4, LABEL_X, 100 + i * PITCH)
    ]
    assert ocr_tables(page, 1, options()) == []
    assert ocr_tables([], 1, options()) == []


def test_a_word_read_twice_is_kept_once() -> None:
    page: list[TextBlock] = []
    for i in range(3):
        y = 100 + i * PITCH
        page += (
            words("Pump", LABEL_X, y) + words("Pump", LABEL_X + 1, y + 1) + words("x", VALUE_X, y)
        )
    assert [row.cells[0] for row in ocr_tables(page, 1, options())[0].rows] == ["Pump"] * 3


def test_row_confidence_is_the_mean_of_its_words() -> None:
    page: list[TextBlock] = []
    for i in range(3):
        y = 100 + i * PITCH
        page += words("Motor", LABEL_X, y, conf=0.9) + words("35HP", VALUE_X, y, conf=0.3)
    (table,) = ocr_tables(page, 1, options())
    assert table.rows[0].ocr_confidence == pytest.approx(0.6)


def test_row_bands_group_overlapping_words() -> None:
    page = words("a b", 0, 0) + words("c", 100, 3) + words("d", 0, PITCH)
    assert [[w.text for w in band] for band in row_bands(page, 2.0)] == [["a", "b", "c"], ["d"]]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7.", True),
        ("10", True),
        ("|", True),
        ("S|", True),
        ("|_1.", True),
        ("RPM", False),
        ("ie", False),
        ("Type", False),
    ],
)
def test_serial_words(text: str, expected: bool) -> None:
    assert is_serial_word(text, 3) is expected


def test_serial_prefix_reaches_the_last_serial_word() -> None:
    assert serial_prefix(words("ie 1. | Motor cooling", 0, 0), 3) == 3
    assert serial_prefix(words("RPM", 0, 0), 3) == 0


@pytest.mark.parametrize(
    ("text", "expected", "repairs"),
    [
        ("55304", "SS304", ["55304 -> SS304"]),
        ("| 55410/55304", "| SS410/SS304", ["55410 -> SS410", "55304 -> SS304"]),
        ("PN1O flanged", "PN10 flanged", ["PN1O -> PN10"]),
        ("1S:325", "IS:325", ["1S:325 -> IS:325"]),
        ("1500 lpm", "1500 lpm", []),
        ("SS304", "SS304", []),
        ("Body SS 304", "Body SS 304", []),
    ],
)
def test_repair_codes_only_into_ontology_codes(
    text: str, expected: str, repairs: list[str]
) -> None:
    assert repair_codes(text, CODE_RULES, CONFUSIONS) == (expected, repairs)


def test_repair_codes_skips_tokens_with_too_many_confusable_characters() -> None:
    assert repair_codes("5050505050", CODE_RULES, CONFUSIONS) == ("5050505050", [])


def test_codes_are_repaired_inside_rebuilt_rows() -> None:
    page: list[TextBlock] = []
    for i, part in enumerate(["Diffuser", "Strainer", "Fasteners"]):
        y = 100 + i * PITCH
        page += words(part, LABEL_X, y) + words("55304", VALUE_X, y)
    assert [row.cells[1] for row in ocr_tables(page, 1, options())[0].rows] == ["SS304"] * 3


def test_options_from_settings_use_the_ontology_code_rules(settings: Settings) -> None:
    vocabulary = SearchVocabulary(code_rules=CODE_RULES)
    built = OcrTableOptions.from_settings(settings, vocabulary.code_rules)
    assert built.code_rules == CODE_RULES
    assert built.min_rows == settings.OCR_TABLE_MIN_ROWS
    assert built.confusions["5"] == "s"


def test_ocr_words_become_blocks_in_pdf_points() -> None:
    word = OcrWord(text="SS304", left=300, top=600, right=600, bottom=900, confidence=0.5)
    line = OcrLine(
        text="SS304", left=300, top=600, right=600, bottom=900, confidence=0.5, words=[word]
    )
    (block,) = ocr_words_to_blocks(OcrResult(lines=[line]), dpi=300)
    assert (block.bbox.x0, block.bbox.y0, block.bbox.x1, block.bbox.y1) == (72, 144, 144, 216)
    assert block.confidence == 0.5


def test_scanned_tables_read_only_ocr_pages() -> None:
    page_words: list[TextBlock] = []
    for i in range(3):
        y = 100 + i * PITCH
        page_words += words(f"Label{i}", LABEL_X, y) + words("v", VALUE_X, y)

    def page(number: int, ocr: bool) -> ParsedPage:
        return ParsedPage(
            page_number=number,
            text="",
            blocks=[],
            width=600,
            height=800,
            ocr=ocr,
            ocr_words=page_words,
        )

    found = scanned_tables([page(1, True), page(2, False)], options())
    assert list(found) == [1]


def test_parser_passes_the_page_segmentation_mode(tmp_path: Path, settings: Settings) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50)))
    doc.save(tmp_path / "scan.pdf")
    doc.close()
    seen: list[int | None] = []

    class Engine:
        def recognize(self, image_png: bytes, page_segmentation: int | None = None) -> OcrResult:
            seen.append(page_segmentation)
            return OcrResult(lines=[])

    parser = ParserOptions.from_settings(settings)
    parse_pdf(tmp_path / "scan.pdf", parser, Engine())
    parse_pdf(tmp_path / "scan.pdf", replace(parser, ocr_page_segmentation=4), Engine())
    assert seen == [None, 4]


def test_a_table_with_three_columns_is_not_rebuilt() -> None:
    page: list[TextBlock] = []
    for i, (name, acid, alkali) in enumerate(
        [
            ("Service fluid", "HCl", "NaOH"),
            ("Suction", "Flooded", "Flooded"),
            ("Mounting", "Foot", "Foot"),
        ]
    ):
        y = 100 + i * PITCH
        page += words(name, LABEL_X, y) + words(acid, VALUE_X, y) + words(alkali, 450, y)
    assert ocr_tables(page, 1, options()) == []


def test_a_list_whose_rows_lose_their_label_is_not_rebuilt() -> None:
    page = words("Dummy plug set - 1 set of the following plugs", LABEL_X, 100)
    page += words("nos", VALUE_X + 100, 100)
    for i, size in enumerate(['3/4"', '1"', '2"', '1"']):
        y = 100 + (i + 1) * PITCH
        page += words(size, 200, y) + words("nos", VALUE_X + 100, y)
    assert ocr_tables(page, 1, options()) == []
