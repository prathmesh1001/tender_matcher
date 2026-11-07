"""Shifted-font and superscript-degree repair. Strings copied from the L&T API 600 catalogue."""

import pytest

from app.ingestion.glyph_repair import (
    PageRepair,
    RepairOptions,
    detect_shift,
    has_superscript_degree,
    repair_text,
    repair_word,
)

OPTIONS = RepairOptions(min_control_chars=5, min_word_letters=4, min_vowel_share=0.3)
SHIFTED = PageRepair(shift=30, degree=False, options=OPTIONS)
PROSE = "6JG\x02XCNXGU\x02CTG\x02CXCKNCDNG\x02YKVJ\x02ƀCPIGF\x02GPFU"


def test_the_shifted_space_gives_the_shift() -> None:
    assert detect_shift(PROSE, OPTIONS) == 30


@pytest.mark.parametrize(
    "text",
    [
        "650\n26\n\x17O\n700\n28\n\x17O\n\x17O\n\x17O\n\x17O\n\x17O",  # KSB symbol glyph
        "• \t\x07Full Port\n• \t\x07Exceptional\n• \t\x07Corrosion",  # Watts bullets
        "Publication Number: &\x15\x13\x15\x17\x10\x16\x12\x03\x13",  # too few, not spaces
        "Plain text with no control characters at all",
    ],
)
def test_symbols_and_bullets_are_no_shifted_space(text: str) -> None:
    assert detect_shift(text, OPTIONS) is None


def test_a_token_with_a_control_character_is_decoded() -> None:
    assert repair_text("9QTMKPI\x02VGORGTCVWTG", SHIFTED) == "Working temperature"
    assert repair_text(PROSE, SHIFTED).startswith("The valves are available with")


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("2CTCOGVGT", "Parameter"),  # one-word header, no control character
        ("%QORNKCPEG", "Compliance"),
        ("ASTM", "ASTM"),  # A decodes to "_": not a word
        ("WCB", "WCB"),
        ("PTFE", "PTFE"),  # "nrdc": no vowels
        ("DISC", "DISC"),  # "bgqa": too few vowels
        ("CF8M", "CF8M"),  # uppercase after the first letter
        ("Body", "Body"),  # lowercase: printed in a normal font
    ],
)
def test_a_token_without_control_characters_is_decoded_only_if_it_reads(
    token: str, expected: str
) -> None:
    assert repair_text(token, SHIFTED) == expected


def test_words_inside_a_shifted_span_are_decoded() -> None:
    repair = PageRepair(30, False, OPTIONS, shifted_boxes=((0.0, 0.0, 100.0, 10.0),))
    assert repair_word("VQ", (10.0, 2.0, 20.0, 8.0), repair) == "to"
    assert repair_word("VQ", (10.0, 20.0, 20.0, 28.0), repair) == "VQ"  # outside: kept


def test_a_page_needing_nothing_is_unchanged() -> None:
    clean = PageRepair(shift=None, degree=False, options=OPTIONS)
    assert not clean.active
    assert repair_text("-290C to 4270C", clean) == "-290C to 4270C"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-290C to 4270C (-200F to 8000F)", "-29°C to 427°C (-20°F to 800°F)"),
        ("-20O F (-29O C)", "-20°F (-29°C)"),  # Delval: raised letter O
        ("up to 4250C in carbon steel", "up to 425°C in carbon steel"),
        ("PN 10 Class 150", "PN 10 Class 150"),
    ],
)
def test_superscript_degree_is_restored(text: str, expected: str) -> None:
    assert repair_text(text, PageRepair(None, True, OPTIONS)) == expected


def test_superscript_degree_needs_a_raised_zero_before_c_or_f() -> None:
    raised = [
        {"text": "-29", "flags": 4},
        {"text": "0", "flags": 5},
        {"text": "C to 427", "flags": 4},
    ]
    flat = [
        {"text": "-29", "flags": 4},
        {"text": "0", "flags": 4},
        {"text": "C to 427", "flags": 4},
    ]
    footnote = [
        {"text": "Note", "flags": 4},
        {"text": "0", "flags": 5},
        {"text": "see", "flags": 4},
    ]
    assert has_superscript_degree([raised])
    assert not has_superscript_degree([flat])
    assert not has_superscript_degree([footnote])
