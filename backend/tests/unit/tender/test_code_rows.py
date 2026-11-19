"""An item's own spec row found by its code. Row texts are verbatim from 7865644 boq_spec.pdf."""

from app.tender.code_rows import code_row_lines, codes_in, find_code_row, row_cells

VARIANT_ROW = (
    "HYDERABAD | Var No: 15; Material Code: PY9756306157; Process Connection Size: 1½” 300#RF; "
    "Process Connection Flange material: A182 F316; Thermowell Immersion Length: U=280 MM; "
    "Thermowell Material: SS 316 / 446; Range: 0 ≤ 1200 oC; Applicable Drg: DWG-2"
)
NACE_ROW = (
    "HYDERABAD | Var. No: 02; Material Code: PY9756305029 (NACE); Range: 0 ≤ 650 oC; "
    "Thermowell Material: SS316 / 446; Thermowell Immersion Length: U=400 MM"
)
CODE_LIST_ROW = (
    "TECHNICAL PQC CRITERIA | Remarks 4: RTD RTD; Remarks 4: Material; "
    "Remarks 4: PY9756306157 PY9756306076 PY9756306033 PY9756306432 PY9756306165"
)
PRICE_ROW = (
    "File No. PESD-4500PROC(MISC)/95/2026-PE&SD-450_C&I (Computer No. 328510) | and Thermowell "
    "PY56305, PY56306,: RTD; Confirmation Bidders: Code; NOT QUOTED] [QUOTED/: PY9756305916; "
    "Column 5: PY9756305924 PY9756305940 PY9756305967 PY9756305983"
)
OTHERS = ["PY9756306076", "PY9756305924", "PY9756305940"]


def test_codes_are_long_letter_and_digit_tokens() -> None:
    title = "Thermocouple -K-type- with Thermowell- SS316-446-A182 F316-1.5 Inch300"
    assert codes_in([title, "PY9756306157"], 8) == {"PY9756306157"}


def test_row_cells_drop_the_context() -> None:
    cells = row_cells(VARIANT_ROW)
    assert cells[0] == ("Var No", "15")
    assert cells[1] == ("Material Code", "PY9756306157")
    assert len(cells) == 8


def test_variant_row_gives_its_other_cells_as_lines() -> None:
    lines = code_row_lines(VARIANT_ROW, "PY9756306157", OTHERS, 3)
    assert lines is not None
    assert "Material Code : PY9756306157" not in lines
    assert "Process Connection Size : 1½” 300#RF" in lines
    assert "Thermowell Material : SS 316 / 446" in lines


def test_code_with_a_trailing_note_still_keys_the_row() -> None:
    assert code_row_lines(NACE_ROW, "PY9756305029", OTHERS, 3) is not None


def test_a_list_of_codes_is_not_the_items_row() -> None:
    assert code_row_lines(CODE_LIST_ROW, "PY9756306157", OTHERS, 3) is None


def test_a_row_naming_other_items_codes_is_not_the_items_row() -> None:
    assert code_row_lines(PRICE_ROW, "PY9756305916", OTHERS, 3) is None


def test_too_few_cells_is_not_a_spec_row() -> None:
    assert code_row_lines("Material Code: PY9756306157; Qty: 4", "PY9756306157", [], 3) is None


def test_the_one_matching_row_is_found_among_others() -> None:
    texts = [CODE_LIST_ROW, VARIANT_ROW, PRICE_ROW]
    found = find_code_row("PY9756306157", texts, OTHERS, 3)
    assert found is not None
    assert found[0] == 1


def test_two_matching_rows_give_none() -> None:
    assert find_code_row("PY9756306157", [VARIANT_ROW, VARIANT_ROW], OTHERS, 3) is None
