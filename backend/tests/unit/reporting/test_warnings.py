"""Single-firm warning. Requirements are synthetic."""

from app.reporting.warnings import single_firm_warning
from app.schemas.enums import Operator
from app.schemas.requirements import Requirement, ValueKind


def _req(**fields: object) -> Requirement:
    return Requirement(
        id="1",
        item_no="general",
        position=0,
        kind=ValueKind.TEXT,
        operator=Operator.TEXT,
        value_text="v",
        raw_text="printed line",
        document="bid.pdf",
        page_number=7,
        **fields,  # type: ignore[arg-type]
    )


def test_no_single_firm_line_gives_no_warning() -> None:
    assert single_firm_warning([_req(named_make="Acme"), _req()]) is None


def test_warning_cites_the_line_and_names_its_make() -> None:
    warning = single_firm_warning([_req(single_firm=True, named_make="Acme"), _req()])
    assert warning is not None
    assert warning.makes == ["Acme"]
    [citation] = warning.citations
    assert (citation.document, citation.page_number, citation.quote) == (
        "bid.pdf", 7, "printed line",
    )  # fmt: skip


def test_two_lines_saying_so_share_one_warning() -> None:
    lines = [_req(single_firm=True, named_make="Acme"), _req(single_firm=True)]
    warning = single_firm_warning(lines)
    assert warning is not None
    assert (warning.makes, len(warning.citations)) == (["Acme"], 2)


def test_the_only_make_named_elsewhere_is_used() -> None:
    warning = single_firm_warning([_req(single_firm=True), _req(named_make="Acme")])
    assert warning is not None
    assert warning.makes == ["Acme"]


def test_several_makes_elsewhere_are_not_guessed() -> None:
    lines = [_req(single_firm=True), _req(acceptable_makes=["Acme", "Beta"])]
    warning = single_firm_warning(lines)
    assert warning is not None
    assert warning.makes == []
