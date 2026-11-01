"""Domain enumerations shared by DB models, schemas and pipelines.

Values are the stable strings stored in the DB and returned by the API; never rename a value
without a data migration.
"""

from enum import StrEnum


class DocType(StrEnum):
    """Kind of manufacturer document. Order of trust: datasheet > catalogue > brochure."""

    DATASHEET = "datasheet"
    CATALOGUE = "catalogue"
    BROCHURE = "brochure"
    MANUAL = "manual"
    OTHER = "other"


class DocumentSource(StrEnum):
    """Where a stored document came from."""

    MANUFACTURER = "manufacturer"
    TENDER = "tender"


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
    ITEM_CATEGORY_FALLBACK = "item_category_fallback"
    LOW_CONFIDENCE_OCR = "low_confidence_ocr"


class RunStatus(StrEnum):
    """Lifecycle of a matching run."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
