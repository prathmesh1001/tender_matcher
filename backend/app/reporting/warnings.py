"""Warnings a reader must see before the results. Pure functions."""

from collections.abc import Sequence

from app.schemas.report import Citation, SingleFirmWarning
from app.schemas.requirements import Requirement


def single_firm_warning(requirements: Sequence[Requirement]) -> SingleFirmWarning | None:
    """The warning if the tender says only one firm is acceptable.

    The ranking still lists every manufacturer: the tender's restriction is for the reader to
    weigh, and it never fails another make (D4).

    Args:
        requirements: Every requirement of the tender, items and general.

    Returns:
        The warning with the lines saying so, or None. Its makes are those named by those lines,
        else the only make the tender names anywhere; empty if neither gives one.
    """
    flagged = [r for r in requirements if r.single_firm]
    if not flagged:
        return None
    named: set[str] = {r.named_make for r in flagged if r.named_make}
    if not named:
        anywhere = {
            make for r in requirements for make in (r.named_make, *r.acceptable_makes) if make
        }
        named = anywhere if len(anywhere) == 1 else set()
    return SingleFirmWarning(
        makes=sorted(named),
        citations=[
            Citation(document=r.document, page_number=r.page_number, quote=r.raw_text)
            for r in flagged
        ],
    )
