"""Typed output of PDF parsing and OCR, passed between ingestion stages."""

from pydantic import BaseModel, ConfigDict, Field


class BBox(BaseModel):
    """Axis-aligned box in PDF points (1/72 inch), origin top-left, as PyMuPDF reports it."""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float


class TextBlock(BaseModel):
    """A run of text (PDF text block, or one OCR line) and where it sits on the page."""

    model_config = ConfigDict(frozen=True)

    text: str
    bbox: BBox
    confidence: float | None = Field(
        default=None, ge=0, le=1, description="OCR confidence; None for the native text layer."
    )


class ParsedPage(BaseModel):
    """Text of one page. `page_number` is 1-based, matching what citations show."""

    model_config = ConfigDict(frozen=True)

    page_number: int = Field(ge=1)
    text: str
    blocks: list[TextBlock]
    width: float
    height: float
    ocr: bool = Field(description="Text came from OCR rather than the PDF text layer.")
    needs_ocr: bool = Field(
        default=False, description="Page looks scanned but no OCR engine was available."
    )
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    ocr_words: list[TextBlock] = Field(
        default_factory=list,
        description="OCR pages only: every word with its box, to rebuild scanned tables.",
    )

    @property
    def char_count(self) -> int:
        """Number of non-whitespace characters in `text`."""
        return count_text_chars(self.text)


class ParsedDocument(BaseModel):
    """All pages of a PDF, in order."""

    model_config = ConfigDict(frozen=True)

    pages: list[ParsedPage]

    @property
    def page_count(self) -> int:
        """Number of pages."""
        return len(self.pages)


def count_text_chars(text: str) -> int:
    """Count non-whitespace characters (used to decide whether a page is scanned).

    Args:
        text: Any text.

    Returns:
        The number of characters that are not whitespace.
    """
    return sum(1 for ch in text if not ch.isspace())
