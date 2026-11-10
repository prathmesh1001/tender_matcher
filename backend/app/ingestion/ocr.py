"""OCR for scanned pages, behind a small `OcrEngine` protocol so engines can be swapped.

Engines return lines with boxes in *image pixels*; the PDF parser converts them to PDF points.
"""

import io
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol

import pytesseract
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings
from app.core.exceptions import ExternalServiceError

_TESSERACT_CONF_MAX: Final = 100.0


class OcrWord(BaseModel):
    """One recognized word with its box in image pixels."""

    model_config = ConfigDict(frozen=True)

    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: float = Field(ge=0, le=1)


class OcrLine(BaseModel):
    """One recognized line of text with its box in image pixels."""

    model_config = ConfigDict(frozen=True)

    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: float = Field(ge=0, le=1)
    words: list[OcrWord] = Field(
        default_factory=list, description="The line's words, so table cells can be rebuilt."
    )


class OcrResult(BaseModel):
    """Recognized lines of one page image, in reading order."""

    model_config = ConfigDict(frozen=True)

    lines: list[OcrLine]

    @property
    def text(self) -> str:
        """All lines joined with newlines."""
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float | None:
        """Average line confidence, or None if nothing was recognized."""
        if not self.lines:
            return None
        return sum(line.confidence for line in self.lines) / len(self.lines)


class OcrEngine(Protocol):
    """Anything that turns a page image into text lines."""

    def recognize(self, image_png: bytes, page_segmentation: int | None = None) -> OcrResult:
        """Recognize text in a PNG image.

        Args:
            image_png: The rendered page as PNG bytes.
            page_segmentation: Tesseract page segmentation mode, or None for the engine default.

        Returns:
            Lines with pixel boxes and confidences.

        Raises:
            ExternalServiceError: If the engine is unavailable or fails.
        """
        ...


def words_to_lines(data: Mapping[str, Sequence[object]]) -> list[OcrLine]:
    """Group Tesseract `image_to_data` word rows into lines.

    Rows with confidence < 0 are layout rows (blocks/paragraphs), not words, and are skipped.

    Args:
        data: Output of `pytesseract.image_to_data(..., output_type=Output.DICT)`.

    Returns:
        Lines in Tesseract's reading order, each with the union box of its words and the mean
        word confidence scaled to 0..1.
    """
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, raw_text in enumerate(data["text"]):
        if float(str(data["conf"][i])) < 0 or not str(raw_text).strip():
            continue
        key = (
            int(str(data["block_num"][i])),
            int(str(data["par_num"][i])),
            int(str(data["line_num"][i])),
        )
        groups.setdefault(key, []).append(i)
    return [_merge_words(data, idx) for idx in groups.values()]


def _merge_words(data: Mapping[str, Sequence[object]], idx: list[int]) -> OcrLine:
    """Build one `OcrLine` from the word rows at `idx`."""

    def num(field: str, i: int) -> float:
        return float(str(data[field][i]))

    words = [
        OcrWord(
            text=str(data["text"][i]).strip(),
            left=num("left", i),
            top=num("top", i),
            right=num("left", i) + num("width", i),
            bottom=num("top", i) + num("height", i),
            confidence=min(num("conf", i) / _TESSERACT_CONF_MAX, 1.0),
        )
        for i in idx
    ]
    return OcrLine(
        text=" ".join(w.text for w in words),
        left=min(w.left for w in words),
        top=min(w.top for w in words),
        right=max(w.right for w in words),
        bottom=max(w.bottom for w in words),
        confidence=sum(w.confidence for w in words) / len(words),
        words=words,
    )


class TesseractOcrEngine:
    """OCR with the Tesseract binary via `pytesseract`."""

    def __init__(
        self,
        *,
        language: str,
        tesseract_cmd: str | None = None,
        tessdata_dir: Path | None = None,
    ) -> None:
        self._language = language
        # Unquoted on purpose: on Windows pytesseract passes quotes through literally. Settings
        # rejects a TESSDATA_DIR containing whitespace, so no quoting is needed.
        self._config = f"--tessdata-dir {tessdata_dir.as_posix()}" if tessdata_dir else ""
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    def recognize(self, image_png: bytes, page_segmentation: int | None = None) -> OcrResult:
        """Recognize text in a PNG image with Tesseract.

        Args:
            image_png: The rendered page as PNG bytes.
            page_segmentation: Tesseract `--psm` mode, or None for Tesseract's default.

        Returns:
            Recognized lines.

        Raises:
            ExternalServiceError: If Tesseract is not installed or fails on the image.
        """
        try:
            with Image.open(io.BytesIO(image_png)) as image:
                data = pytesseract.image_to_data(
                    image,
                    lang=self._language,
                    config=_with_psm(self._config, page_segmentation),
                    output_type=pytesseract.Output.DICT,
                )
        except pytesseract.TesseractNotFoundError as exc:
            raise ExternalServiceError(
                "Tesseract is not installed or not on PATH; set TESSERACT_CMD or OCR_ENABLED=false"
            ) from exc
        except (pytesseract.TesseractError, OSError) as exc:
            raise ExternalServiceError(f"Tesseract failed: {exc}") from exc
        return OcrResult(lines=words_to_lines(data))


def _with_psm(config: str, page_segmentation: int | None) -> str:
    """Tesseract config with a `--psm` option added when a mode is given."""
    if page_segmentation is None:
        return config
    return f"{config} --psm {page_segmentation}".strip()


def build_ocr_engine(settings: Settings) -> OcrEngine | None:
    """Create the configured OCR engine.

    Args:
        settings: Application settings.

    Returns:
        A Tesseract engine, or None when OCR is disabled.
    """
    if not settings.OCR_ENABLED:
        return None
    return TesseractOcrEngine(
        language=settings.OCR_LANGUAGE,
        tesseract_cmd=settings.TESSERACT_CMD,
        tessdata_dir=settings.TESSDATA_DIR,
    )
