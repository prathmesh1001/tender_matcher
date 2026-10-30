"""Boilerplate (running header/footer) removal. All pages and text are synthetic."""

from dataclasses import replace

from app.ingestion.boilerplate import (
    BoilerplateOptions,
    find_boilerplate,
    in_margin_band,
    line_key,
    remove_boilerplate,
    strip_block,
)
from app.ingestion.chunker import ChunkerOptions, chunk_document
from app.schemas.parsing import BBox, ParsedPage, TextBlock

HEIGHT = 1000.0
OPTIONS = BoilerplateOptions(margin_band=0.12, min_page_ratio=0.5, min_pages=2, min_letters=3)
CHUNKER = ChunkerOptions(
    target_tokens=400,
    max_tokens=500,
    overlap_tokens=50,
    heading_max_words=10,
    heading_min_latin_ratio=0.5,
    model_header_keywords=("model",),
)
TOP = 20.0  # y0 of a running header (wholly inside the top band)
BOTTOM = 950.0  # y0 of a footer (wholly inside the bottom band)


def _block(text: str, y0: float, h: float = 15) -> TextBlock:
    return TextBlock(text=text, bbox=BBox(x0=50, y0=y0, x1=500, y1=y0 + h))


def _page(number: int, *blocks: TextBlock) -> ParsedPage:
    return ParsedPage(
        page_number=number,
        text="\n".join(b.text for b in blocks),
        blocks=list(blocks),
        width=600,
        height=HEIGHT,
        ocr=False,
    )


def _texts(page: ParsedPage) -> list[str]:
    return [b.text for b in page.blocks]


# ---------------------------------------------------------------- helpers


def test_line_key_folds_case_spaces_and_digits() -> None:
    assert line_key("  Page 1  of 2 ") == line_key("PAGE 12 of 20") == "page # of #"


def test_margin_band_needs_the_whole_box_inside() -> None:
    assert in_margin_band(BBox(x0=0, y0=10, x1=1, y1=100), HEIGHT, 0.12)
    assert in_margin_band(BBox(x0=0, y0=900, x1=1, y1=990), HEIGHT, 0.12)
    # starts in the top band but runs into the body (a sidebar or a tall block)
    assert not in_margin_band(BBox(x0=0, y0=50, x1=1, y1=800), HEIGHT, 0.12)


# ---------------------------------------------------------------- detection


def test_repeated_footer_and_header_are_found() -> None:
    pages = [
        _page(n, _block("ACME PUMPS", TOP), _block("Body text", 400), _block("Acme Ltd", BOTTOM))
        for n in (1, 2, 3)
    ]
    assert find_boilerplate(pages, OPTIONS) == {"acme pumps", "acme ltd"}


def test_page_numbers_match_across_pages() -> None:
    pages = [_page(n, _block(f"Page {n} of 3", BOTTOM)) for n in (1, 2, 3)]
    assert find_boilerplate(pages, OPTIONS) == {"page # of #"}


def test_line_repeated_in_the_body_is_not_boilerplate() -> None:
    # A series name printed on both pages of a two-page catalogue, but not in the margins.
    pages = [_page(n, _block("Series KX", 400)) for n in (1, 2)]
    assert find_boilerplate(pages, OPTIONS) == frozenset()


def test_margin_line_also_in_the_body_is_not_boilerplate() -> None:
    # A chart axis label whose first copy touches the top band and is repeated lower down.
    pages = [_page(n, _block("HEAD IN METRES", TOP), _block("HEAD IN METRES", 500)) for n in (1, 2)]
    assert find_boilerplate(pages, OPTIONS) == frozenset()


def test_line_on_too_few_pages_is_not_boilerplate() -> None:
    pages = [_page(1, _block("Acme Ltd", BOTTOM)), *(_page(n) for n in (2, 3, 4, 5))]
    assert find_boilerplate(pages, OPTIONS) == frozenset()


def test_single_page_document_has_no_boilerplate() -> None:
    assert find_boilerplate([_page(1, _block("Acme Ltd", BOTTOM))], OPTIONS) == frozenset()


def test_lines_with_few_letters_are_never_boilerplate() -> None:
    pages = [_page(n, _block("12", BOTTOM)) for n in (1, 2)]
    assert find_boilerplate(pages, OPTIONS) == frozenset()


def test_ratio_threshold_comes_from_options() -> None:
    pages = [_page(1, _block("Acme Ltd", BOTTOM)), _page(2, _block("Acme Ltd", BOTTOM)), _page(3)]
    assert find_boilerplate(pages, OPTIONS) == {"acme ltd"}  # 2/3 >= 0.5
    strict = replace(OPTIONS, min_page_ratio=0.9)
    assert find_boilerplate(pages, strict) == frozenset()


# ---------------------------------------------------------------- removal


def test_strip_block_keeps_non_boilerplate_lines() -> None:
    block = _block("Acme Ltd\nRev. 3 issued for model KX-50", BOTTOM)
    stripped = strip_block(block, frozenset({"acme ltd"}))
    assert stripped is not None
    assert stripped.text == "Rev. 3 issued for model KX-50"
    assert stripped.bbox == block.bbox


def test_strip_block_drops_block_left_without_content() -> None:
    assert strip_block(_block("Acme Ltd\n -", BOTTOM), frozenset({"acme ltd"})) is None


def test_remove_boilerplate_keeps_page_text_and_body_blocks() -> None:
    pages = [
        _page(n, _block("ACME PUMPS", TOP), _block("Body text", 400), _block("Acme Ltd", BOTTOM))
        for n in (1, 2)
    ]
    cleaned, found = remove_boilerplate(pages, OPTIONS)
    assert found == {"acme pumps", "acme ltd"}
    assert _texts(cleaned[0]) == ["Body text"]
    # Page text is untouched: quotes are verified against it.
    assert cleaned[0].text == pages[0].text


# ---------------------------------------------------------------- chunking (V8)


def test_removed_boilerplate_is_not_a_heading_or_chunk() -> None:
    pages = [
        _page(n, _block("ACME PUMPS", TOP), _block(f"Pump model KX-{n} spec text.", 400))
        for n in (1, 2)
    ]
    cleaned, _ = remove_boilerplate(pages, OPTIONS)
    chunks = chunk_document(cleaned, {}, CHUNKER)
    assert all(c.section is None for c in chunks)
    assert not any("ACME PUMPS" in c.raw_text for c in chunks)


# ---------------------------------------------------------------- footer headings (V8)

FOOTER_CHUNKER = replace(CHUNKER, footer_band=0.12)


def test_footer_heading_does_not_carry_to_the_next_page() -> None:
    # Synthetic V8: a one-off company line at the foot of page 1, a spec block on page 2.
    pages = [
        _page(
            1,
            _block("FEATURES", 100),
            _block("Compact design for small rooms.", 130),
            _block("A Acme Group Company", BOTTOM),
        ),
        _page(2, _block("Flow 40 m3/h at 2900 rpm for every size listed.", 300)),
    ]
    chunks = chunk_document(pages, {}, FOOTER_CHUNKER)
    assert [c.section for c in chunks if c.page_number == 2] == ["FEATURES"]


def test_footer_heading_still_heads_its_own_page() -> None:
    pages = [_page(1, _block("Body text.", 100), _block("Notes", 930), _block("See p. 2.", 960))]
    chunks = chunk_document(pages, {}, FOOTER_CHUNKER)
    assert [(c.section, c.raw_text) for c in chunks] == [
        (None, "Body text."),
        ("Notes", "See p. 2."),
    ]


def test_body_heading_still_carries() -> None:
    pages = [_page(1, _block("Delivery Terms", 500)), _page(2, _block("Within 30 days.", 300))]
    chunks = chunk_document(pages, {}, FOOTER_CHUNKER)
    assert [c.section for c in chunks] == ["Delivery Terms"]


def test_footer_band_off_carries_last_line_heading() -> None:
    # DOCX virtual pages turn the band off: their last line can be a real heading.
    pages = [
        _page(1, _block("Body text.", 100), _block("Delivery Terms", BOTTOM)),
        _page(2, _block("Within 30 days.", 300)),
    ]
    chunks = chunk_document(pages, {}, CHUNKER)
    assert [c.section for c in chunks if c.page_number == 2] == ["Delivery Terms"]
