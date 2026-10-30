"""Evidence verification on comparisons. Evidence rows and pages are synthetic."""

from app.matching.evidence_verifier import verify_comparison
from app.schemas.enums import DecisionSource, MatchStatus
from app.schemas.matching import Comparison, Evidence, LabelBasis

SHA = "a" * 64


class _Pages:
    def __init__(self, pages: dict[int, str]) -> None:
        self._pages = pages

    def page_text(self, sha256: str, page_number: int) -> str | None:
        return self._pages.get(page_number) if sha256 == SHA else None


PAGES = _Pages({4: "No. Components Materials\n1 Body A 216 Gr. WCB/ WCC\n2 Disc A 351 Gr. CF8M"})


def _evidence(parts: list[str], page: int = 4) -> Evidence:
    return Evidence(
        document="synthetic.pdf",
        sha256=SHA,
        title="Synthetic",
        page_number=page,
        label=f"{parts[0]} Materials",
        value=parts[-1],
        quote="; ".join(parts),
        quote_parts=parts,
        status=MatchStatus.FULL_MATCH,
    )


def _comparison(status: MatchStatus, evidence: list[Evidence]) -> Comparison:
    return Comparison(
        requirement_id="10.8",
        status=status,
        needs_review=False,
        decided_by=DecisionSource.RULE,
        basis=LabelBasis.ATTRIBUTE,
        reason="",
        evidence=evidence,
    )


def test_verified_evidence_keeps_the_decision() -> None:
    result = verify_comparison(
        _comparison(MatchStatus.FULL_MATCH, [_evidence(["Body", "A 216 Gr. WCB/ WCC"])]), PAGES, 0.9
    )
    assert (result.status, result.needs_review) == (MatchStatus.FULL_MATCH, False)
    assert result.evidence[0].verified is True
    assert result.evidence[0].match_score == 1.0


def test_only_verified_rows_are_kept() -> None:
    good, bad = _evidence(["Body", "A 216 Gr. WCB/ WCC"]), _evidence(["Body", "ASTM A105"])
    result = verify_comparison(_comparison(MatchStatus.FULL_MATCH, [bad, good]), PAGES, 0.9)
    assert result.status is MatchStatus.FULL_MATCH
    assert [e.quote_parts for e in result.evidence] == [good.quote_parts]


def test_no_verified_quote_downgrades_to_unsupported() -> None:
    wrong_page = _evidence(["Body", "A 216 Gr. WCB/ WCC"], page=9)
    result = verify_comparison(
        _comparison(MatchStatus.MISSING_CAPABILITY, [wrong_page]), PAGES, 0.9
    )
    assert (result.status, result.needs_review) == (MatchStatus.UNSUPPORTED, True)
    assert result.decided_by is DecisionSource.NONE
    assert result.reason == "quote not found on the cited page (was missing_capability)"
    assert result.evidence[0].verified is False  # kept so a reviewer can look


def test_unsupported_evidence_is_only_marked() -> None:
    considered = _evidence(["Disc", "A 351 Gr. CF8M"])
    result = verify_comparison(_comparison(MatchStatus.UNSUPPORTED, [considered]), PAGES, 0.9)
    assert result.status is MatchStatus.UNSUPPORTED
    assert result.evidence[0].verified is True
