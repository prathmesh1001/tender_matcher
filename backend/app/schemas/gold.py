"""Hand-labelled gold for one tender and its diff against match reports."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import MatchStatus
from app.schemas.matching import SubTypeFit
from app.schemas.report import RankingEntry, Score


class ProductLabel(StrEnum):
    """Product label per catalogue (Layer 2 of docs/manual_match_check.md)."""

    FULL = "full"
    PARTIAL = "partial"
    LEAST = "least"
    NOT_FOUND = "not_found"


class GoldRequirement(BaseModel):
    """One hand label for one atomic requirement."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    manual_row: str = Field(description="Row of the manual check this label comes from.")
    status: MatchStatus
    pages: list[int] = Field(default_factory=list, description="Pages the manual check cites.")
    evidence: str
    note: str | None = None


class GoldEvaluation(BaseModel):
    """Hand labels for one item x manufacturer x catalogue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_no: str
    manufacturer: str
    document: str
    manual_fit: float | None = Field(default=None, description="Weighted, as the manual check.")
    manual_coverage: str | None = None
    requirements: list[GoldRequirement]


class GoldProduct(BaseModel):
    """Hand product label for one item x manufacturer x catalogue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_no: str
    manufacturer: str
    document: str
    label: ProductLabel
    note: str | None = None


class GoldFile(BaseModel):
    """One file under eval/gold/."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tender: str = Field(description="Tender folder name.")
    source: str = Field(description="Where the labels were copied from.")
    evaluations: list[GoldEvaluation]
    products: list[GoldProduct] = Field(default_factory=list)


class Verdict(StrEnum):
    """How one run's status relates to the gold status."""

    AGREE = "agree"
    NOT_DECIDED = "not decided"  # gold decided, the run stayed unsupported
    WEAKER = "weaker"  # both decided, the run is below gold
    STRONGER = "stronger"  # the run claims more than gold: the serious direction
    OTHER_CATALOGUE = "other catalogue"  # the run chose another catalogue: not comparable


class RunCell(BaseModel):
    """One run's result for one gold requirement."""

    model_config = ConfigDict(frozen=True)

    status: MatchStatus | None
    pages: list[int]
    reason: str
    verdict: Verdict
    page_agrees: bool | None = Field(
        description="Status agrees and a gold page is cited; None if gold cites no page or "
        "the status differs."
    )


class RowDiff(BaseModel):
    """One gold requirement across all runs."""

    model_config = ConfigDict(frozen=True)

    requirement: str = Field(description="The requirement as the tender prints it.")
    gold: GoldRequirement
    cells: dict[str, RunCell]


class Unlabelled(BaseModel):
    """An atomic requirement of the evaluated item that gold has no label for."""

    model_config = ConfigDict(frozen=True)

    id: str
    requirement: str
    statuses: dict[str, MatchStatus | None]


class EvaluationDiff(BaseModel):
    """One gold evaluation across all runs."""

    model_config = ConfigDict(frozen=True)

    evaluation: GoldEvaluation
    rows: list[RowDiff]
    unlabelled: list[Unlabelled]
    scores: dict[str, Score | None] = Field(description="Item score per run.")
    tied: dict[str, list[str]] = Field(
        default_factory=dict, description="Per run, the catalogues that tie, if any."
    )


class ProductDiff(BaseModel):
    """One gold product label across all runs."""

    model_config = ConfigDict(frozen=True)

    gold: GoldProduct
    fits: dict[str, SubTypeFit | None] = Field(description="None if not a candidate in that run.")
    chosen: dict[str, bool]
    agrees: dict[str, bool]


class GoldDiff(BaseModel):
    """Everything the diff found for one tender."""

    model_config = ConfigDict(frozen=True)

    tender: str
    source: str
    runs: list[str]
    evaluations: list[EvaluationDiff]
    products: list[ProductDiff]
    items_without_gold: list[str]
    rankings: dict[str, list[RankingEntry]]
