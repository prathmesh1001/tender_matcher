import os
import shutil
from pathlib import Path

import pymupdf
import pytesseract
import pytest

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError
from app.ingestion.ocr import (
    OcrLine,
    OcrResult,
    TesseractOcrEngine,
    build_ocr_engine,
    words_to_lines,
)
from app.ingestion.pdf_parser import ParserOptions, parse_pdf

# Standard install location of the UB-Mannheim Windows build (not on PATH by default).
WINDOWS_TESSERACT = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")


def find_tesseract() -> str | None:
    """Locate a real tesseract binary: TESSERACT_CMD, then PATH, then the Windows default."""
    candidates = [
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
        str(WINDOWS_TESSERACT),
    ]
    return next((c for c in candidates if c and Path(c).is_file()), None)


TESSERACT_BINARY = find_tesseract()

# Shape of pytesseract.image_to_data(..., output_type=Output.DICT): one row per layout item.
TESSERACT_DATA: dict[str, list[object]] = {
    "block_num": [1, 1, 1, 1, 1],
    "par_num": [1, 1, 1, 1, 1],
    "line_num": [0, 1, 1, 2, 2],
    "text": ["", "Body", "SS316", "Head", " "],
    "conf": [-1, 90, 80, 70, 95],
    "left": [0, 10, 60, 10, 50],
    "top": [0, 5, 4, 30, 30],
    "width": [0, 40, 50, 30, 5],
    "height": [0, 10, 12, 10, 10],
}


def test_words_are_grouped_into_lines_with_union_box_and_mean_confidence() -> None:
    lines = words_to_lines(TESSERACT_DATA)
    assert [line.text for line in lines] == ["Body SS316", "Head"]
    body = lines[0]
    assert (body.left, body.top, body.right, body.bottom) == (10, 4, 110, 16)
    assert body.confidence == pytest.approx(0.85)


def test_layout_rows_and_blank_words_are_skipped() -> None:
    lines = words_to_lines(TESSERACT_DATA)
    assert all(line.text.strip() for line in lines)
    assert lines[1].confidence == pytest.approx(0.70)  # the blank word (conf 95) is ignored


def test_result_text_and_mean_confidence() -> None:
    empty = OcrResult(lines=[])
    assert empty.text == ""
    assert empty.mean_confidence is None
    lines = [
        OcrLine(text="a", left=0, top=0, right=1, bottom=1, confidence=0.5),
        OcrLine(text="b", left=0, top=2, right=1, bottom=3, confidence=1.0),
    ]
    assert OcrResult(lines=lines).text == "a\nb"
    assert OcrResult(lines=lines).mean_confidence == pytest.approx(0.75)


def _png() -> bytes:
    doc = pymupdf.open()
    png = bytes(doc.new_page(width=50, height=50).get_pixmap().tobytes("png"))
    doc.close()
    return png


def test_missing_tesseract_becomes_external_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found(*args: object, **kwargs: object) -> None:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", not_found)
    with pytest.raises(ExternalServiceError, match="TESSERACT_CMD"):
        TesseractOcrEngine(language="eng").recognize(_png())


def test_tesseract_failure_becomes_external_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fails(*args: object, **kwargs: object) -> None:
        raise pytesseract.TesseractError(1, "bad language")

    monkeypatch.setattr(pytesseract, "image_to_data", fails)
    with pytest.raises(ExternalServiceError, match="Tesseract failed"):
        TesseractOcrEngine(language="xx").recognize(_png())


def test_engine_uses_tesseract_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: TESSERACT_DATA)
    result = TesseractOcrEngine(language="eng").recognize(_png())
    assert result.text == "Body SS316\nHead"


def test_engine_passes_language_and_tessdata_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def capture(*args: object, **kwargs: object) -> dict[str, list[object]]:
        captured.update(kwargs)
        return TESSERACT_DATA

    monkeypatch.setattr(pytesseract, "image_to_data", capture)
    engine = TesseractOcrEngine(language="eng+hin", tessdata_dir=Path("C:/tessdata"))
    engine.recognize(_png())
    assert captured["lang"] == "eng+hin"
    assert captured["config"] == "--tessdata-dir C:/tessdata"


def test_engine_without_tessdata_dir_passes_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def capture(*args: object, **kwargs: object) -> dict[str, list[object]]:
        captured.update(kwargs)
        return TESSERACT_DATA

    monkeypatch.setattr(pytesseract, "image_to_data", capture)
    TesseractOcrEngine(language="eng").recognize(_png())
    assert captured["config"] == ""


def test_build_ocr_engine_respects_settings(settings: Settings) -> None:
    disabled = settings.model_copy(update={"OCR_ENABLED": False})
    assert build_ocr_engine(disabled) is None
    assert isinstance(build_ocr_engine(settings), TesseractOcrEngine)


def find_tessdata_with(language: str) -> Path | None:
    """Locate a tessdata folder that has `<language>.traineddata` installed."""
    candidates = [
        os.environ.get("TESSDATA_DIR"),
        str(Path.home() / "AppData/Local/tessdata"),
        str(WINDOWS_TESSERACT.parent / "tessdata"),
        *(str(p) for p in Path("/usr/share/tesseract-ocr").glob("*/tessdata")),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / f"{language}.traineddata").is_file():
            return Path(candidate)
    return None


HINDI_TESSDATA = find_tessdata_with("hin")


@pytest.mark.skipif(
    TESSERACT_BINARY is None or HINDI_TESSDATA is None, reason="tesseract + hin not installed"
)
def test_real_tesseract_reads_bilingual_hindi_english_page() -> None:
    page = pymupdf.open().new_page()
    lines = ["Body material: SS316 / SS304", "पंप का प्रकार: सेंट्रीफ्यूगल", "Motor IP55, 1450 RPM"]
    html = "".join(f'<p style="font-size:16px">{line}</p>' for line in lines)
    page.insert_htmlbox(pymupdf.Rect(50, 50, 550, 300), html)
    png = bytes(page.get_pixmap(dpi=300).tobytes("png"))

    engine = TesseractOcrEngine(
        language="eng+hin", tesseract_cmd=TESSERACT_BINARY, tessdata_dir=HINDI_TESSDATA
    )
    text = engine.recognize(png).text
    assert "पंप" in text
    assert "SS316" in text
    assert "1450 RPM" in text


@pytest.mark.skipif(TESSERACT_BINARY is None, reason="tesseract binary not installed")
def test_real_tesseract_reads_a_scanned_page(scanned_pdf: Path) -> None:
    page = parse_pdf(
        scanned_pdf,
        ParserOptions(ocr_min_text_chars=50, ocr_min_image_coverage=0.5, ocr_dpi=300),
        TesseractOcrEngine(language="eng", tesseract_cmd=TESSERACT_BINARY),
    ).pages[0]
    assert page.ocr is True
    assert "SS316" in page.text


def test_lines_keep_their_words_with_boxes() -> None:
    body = words_to_lines(TESSERACT_DATA)[0]
    assert [w.text for w in body.words] == ["Body", "SS316"]
    assert (body.words[1].left, body.words[1].right) == (60, 110)
    assert body.words[1].confidence == pytest.approx(0.80)


def test_engine_adds_the_page_segmentation_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def capture(*args: object, **kwargs: object) -> dict[str, list[object]]:
        captured.update(kwargs)
        return TESSERACT_DATA

    monkeypatch.setattr(pytesseract, "image_to_data", capture)
    engine = TesseractOcrEngine(language="eng", tessdata_dir=Path("C:/tessdata"))
    engine.recognize(_png(), page_segmentation=4)
    assert captured["config"] == "--tessdata-dir C:/tessdata --psm 4"
