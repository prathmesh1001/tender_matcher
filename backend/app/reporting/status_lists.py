"""Which requirements got which status, per manufacturer. Pure functions."""

from collections.abc import Sequence

from app.schemas.enums import MatchStatus
from app.schemas.report import ItemReport, ManufacturerStatusList, StatusListEntry


def status_lists(items: Sequence[ItemReport]) -> list[ManufacturerStatusList]:
    """List every requirement under its status for each manufacturer.

    Tied catalogues are counted once, as in the scores: their rows share the status the score uses.

    Args:
        items: The tender's item reports.

    Returns:
        One list per manufacturer sorted by name; statuses in their usual order, empty ones left
        out.
    """
    found: dict[str, dict[MatchStatus, list[StatusListEntry]]] = {}
    for item in items:
        text = {r.id: r.raw_text for r in item.requirements}
        for m in item.manufacturers:
            lists = found.setdefault(m.manufacturer, {})
            for result in m.results:
                lists.setdefault(result.status, []).append(
                    StatusListEntry(
                        item_no=item.item.item_no,
                        requirement_id=result.requirement_id,
                        requirement=text[result.requirement_id],
                        needs_review=result.needs_review,
                    )
                )
    return [
        ManufacturerStatusList(
            manufacturer=name,
            lists={s: lists[s] for s in MatchStatus if s in lists},
        )
        for name, lists in sorted(found.items())
    ]
