"""Domain enumerations shared by DB models, schemas and pipelines.

Values are the stable strings stored in the DB and returned by the API; never rename a value
without a data migration.
"""

from enum import StrEnum


class DocType(StrEnum):
    """Kind of vendor document.

    Order of trust: datasheet > catalogue > brochure > gem_listing (a GeM Marketplace product
    page whose specifications the seller typed in).
    """

    DATASHEET = "datasheet"
    CATALOGUE = "catalogue"
    BROCHURE = "brochure"
    MANUAL = "manual"
    OTHER = "other"
    GEM_LISTING = "gem_listing"


# Lower is trusted more (CLAUDE.md section 6: datasheet > catalogue > brochure, then GeM).
DOC_TYPE_TRUST: dict[DocType, int] = {
    DocType.DATASHEET: 0,
    DocType.CATALOGUE: 1,
    DocType.MANUAL: 2,
    DocType.BROCHURE: 3,
    DocType.OTHER: 4,
    DocType.GEM_LISTING: 5,
}


class DocumentSource(StrEnum):
    """Where a stored document came from."""

    MANUFACTURER = "manufacturer"
    TENDER = "tender"
    GEM_MARKETPLACE = "gem_marketplace"


class DiscoveryStatus(StrEnum):
    """Lifecycle of one GeM Marketplace check for a product type."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class ChunkType(StrEnum):
    """How a chunk was produced from a page."""

    PROSE = "prose"
    TABLE_ROW = "table_row"


class RequirementType(StrEnum):
    """Classification of a tender line."""

    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    LEGAL = "legal"
    COMPLIANCE_DOC = "compliance_doc"
    VAGUE = "vague"


class Operator(StrEnum):
    """Comparison a normalized requirement expresses."""

    EQ = "="
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    RANGE = "range"
    IN = "in"
    BOOL = "bool"
    TEXT = "text"


class MatchStatus(StrEnum):
    """Outcome of one requirement x manufacturer evaluation (see CLAUDE.md section 7)."""

    FULL_MATCH = "full_match"
    PARTIAL_MATCH = "partial_match"
    MISSING_CAPABILITY = "missing_capability"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class ReviewFlag(StrEnum):
    """A weakness a rule or the judge saw in its own decision; each lowers confidence."""

    NEAR_MISS = "near_miss"  # only part of the code found
    MISREAD = "misread"  # a printed value was impossible and ignored
    HEADING_UNCLEAR = "heading_unclear"  # no product heading clearly owns the evidence
    TIED_CATALOGUES = "tied_catalogues"  # nothing tells the best catalogues apart
    CODE_ONLY_HIT = "code_only_hit"  # search accepted the passage only for a code it holds
    LOW_SCORE_HIT = "low_score_hit"  # no passage reached the rerank threshold
    SIBLING_PRODUCT = "sibling_product"  # stated only for another product of the same document
    SELLER_DECLARED = "seller_declared"  # cited only from GeM listings a seller typed in


class DecisionSource(StrEnum):
    """What produced a match decision."""

    RULE = "rule"
    LLM = "llm"
    NONE = "none"


class ExtractionStatus(StrEnum):
    """Whether a tender's extracted items can be trusted."""

    OK = "ok"
    UNRELIABLE = "unreliable"


class ExtractionIssueCode(StrEnum):
    """Why a tender's extraction is unreliable."""

    TITLES_NOT_PRODUCTS = "titles_not_products"
    OVERSIZED_ITEM = "oversized_item"
    MERGED_ITEM_CATEGORY = "merged_item_category"
    LOW_CONFIDENCE_OCR = "low_confidence_ocr"
    FILES_NOT_FETCHED = "files_not_fetched"
    SPEC_OUTSIDE_ITEMS = "spec_outside_items"
    TITLE_ONLY_ITEMS = "title_only_items"


class RunStatus(StrEnum):
    """Lifecycle of a matching run."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
