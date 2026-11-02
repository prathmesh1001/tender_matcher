"""Application configuration.

Every environment variable the backend reads is declared here and documented in `.env.example`.
No other module may read `os.environ` directly.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment the app is running in."""

    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Supported log levels (names match the stdlib `logging` levels)."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class FileStoreBackend(StrEnum):
    """Where source files are stored (Postgres is added in plan step 16)."""

    LOCAL = "local"


class LLMProvider(StrEnum):
    """LLM providers behind `LLMClient`."""

    GEMINI = "gemini"


class EmbeddingDevice(StrEnum):
    """Where the embedding model runs; `auto` uses CUDA when available."""

    AUTO = "auto"
    CPU = "cpu"
    CUDA = "cuda"


class DenseSearch(StrEnum):
    """How dense search scores the product-filtered chunks."""

    EXACT = "exact"  # cosine distance to every filtered vector
    HNSW = "hnsw"  # pgvector HNSW index with iterative scan (approximate)


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables and an optional `.env` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    APP_NAME: str = Field(default="Tender Matcher", description="Name shown in OpenAPI and logs.")
    APP_VERSION: str = Field(default="0.1.0", description="Version reported by the health route.")
    ENVIRONMENT: Environment = Field(default=Environment.LOCAL)
    API_PREFIX: str = Field(default="/api/v1", pattern=r"^/[a-z0-9/_-]*[a-z0-9]$")
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO)
    LOG_JSON: bool = Field(
        default=True, description="Emit JSON logs; set false for readable dev logs."
    )
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="Allowed browser origins, as a JSON list.",
    )

    DATABASE_URL: PostgresDsn = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5433/db. Required."
    )
    DB_ECHO: bool = Field(default=False, description="Log every SQL statement (debug only).")
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=10, ge=0)
    DB_POOL_TIMEOUT_S: float = Field(default=30.0, gt=0)

    FILE_STORE_BACKEND: FileStoreBackend = Field(default=FileStoreBackend.LOCAL)
    FILE_STORE_ROOT: Path = Field(
        default=Path("../data"),
        description="Root of the local file store; documents store paths relative to it.",
    )
    MANUFACTURER_DATA_DIR: Path = Field(
        default=Path("../data/manufacturers"),
        description="Root holding one folder per manufacturer, each with PDFs + sources.yaml.",
    )
    TENDER_DATA_DIR: Path = Field(
        default=Path("../data/tenders"),
        description="Root holding one folder per tender, each with bid.pdf + tender.yaml.",
    )
    ONTOLOGY_DIR: Path = Field(
        default=Path("ontology"), description="Folder with one ontology YAML per category."
    )
    SECTION_CATEGORIES_FILE: Path = Field(
        default=Path("config/section_categories.yaml"),
        description="Section categories (costing, eligibility, dimensions, ...) and their phrases.",
    )
    REQUIREMENT_RULES_FILE: Path = Field(
        default=Path("config/requirement_rules.yaml"),
        description="Phrases that classify a tender line: vague, mandatory, named make, ...",
    )
    REPORT_DIR: Path = Field(
        default=Path(".cache/reports"),
        description="Where match-tender writes JSON reports (local output, not committed).",
    )
    CHUNK_CACHE_DIR: Path = Field(
        default=Path(".cache/chunks"),
        description="Processed catalogue files for in-memory matching, one JSON per sha256.",
    )

    PRODUCT_PAGE_MIN_TAGGED: int = Field(
        default=2,
        ge=1,
        description="Tagged chunks (all one type) a page needs before untagged ones inherit it.",
    )
    TABLE_HEADER_MAX_WORDS: int = Field(
        default=8,
        ge=1,
        description=(
            "Longest column label in words. A longer header cell with commas is a caption "
            "('PERFORMANCE CHART FOR ...'): it becomes the table's section, not its header."
        ),
    )
    LAYOUT_RULE_MAX_THICKNESS: float = Field(
        default=2.0,
        gt=0,
        description="Drawn shapes at most this thick (points) are table rules, not areas.",
    )
    LAYOUT_RULE_TOLERANCE: float = Field(
        default=2.0,
        gt=0,
        description="Rule positions closer than this (points) are the same edge.",
    )
    LAYOUT_GRID_MIN_COLUMNS: int = Field(
        default=2, ge=2, description="A rule grid needs this many columns to be a table."
    )
    LAYOUT_GRID_MIN_ROWS: int = Field(
        default=3, ge=2, description="A rule grid needs this many row bands to be a table."
    )
    LAYOUT_MAX_RULES: int = Field(
        default=2000,
        ge=1,
        description="Pages with more thin shapes are drawings or outlined text, not rule tables.",
    )
    TABLE_PARAGRAPH_GAP_FACTOR: float = Field(
        default=1.5,
        gt=1,
        description="A line gap this many times a cell's median line pitch starts a paragraph.",
    )
    TENDER_ITEM_NO_HEADERS: list[str] = Field(
        default_factory=lambda: ["item no", "sl no", "s no", "sr no", "item number"],
        description="Header labels of a tender schedule's item-number column (JSON list).",
    )
    TENDER_ITEM_TITLE_HEADERS: list[str] = Field(
        default_factory=lambda: ["item title", "item name"],
        description="Header labels of the item-title column (JSON list).",
    )
    TENDER_ITEM_DESCRIPTION_HEADERS: list[str] = Field(
        default_factory=lambda: [
            "description",
            "descriptions",
            "specification",
            "specifications",
            "critical dimension",
        ],
        description="Header labels of the columns holding an item's specification (JSON list).",
    )
    TENDER_ITEM_QUANTITY_HEADERS: list[str] = Field(
        default_factory=lambda: ["quant", "qty"],
        description="Prefixes of quantity-column header words ('Quantity', 'Qty.').",
    )
    TENDER_ITEM_LIST_ROLES: list[str] = Field(
        default_factory=lambda: ["boq_items"],
        description="File roles that are a tender's item list; read first (JSON list).",
    )
    TENDER_SPEC_ROLES: list[str] = Field(
        default_factory=lambda: [
            "boq_spec",
            "buyer_spec",
            "gem_category_spec",
            "buyer_doc",
            "attachment",
        ],
        description="File roles whose schedules can give line items; ATC never does (JSON list).",
    )
    TENDER_ITEM_CATEGORY_LABELS: list[str] = Field(
        default_factory=lambda: ["item category"],
        description="Label of the bid row naming the items, the last source of items (JSON list).",
    )
    TENDER_ITEM_UNIT_HEADERS: list[str] = Field(
        default_factory=lambda: ["unit", "uom"],
        description="Header labels of the unit-of-measure column (JSON list).",
    )
    TENDER_LABEL_MAX_WORDS: int = Field(
        default=6,
        ge=1,
        description="A 'KEY : value' line's key has at most this many words.",
    )
    TENDER_INLINE_KEY_MAX_WORDS: int = Field(
        default=3,
        ge=1,
        description="Longest key inside a value ('MATERIAL: Body: WCB'), in words.",
    )
    TENDER_ENUM_MAX_WORDS: int = Field(
        default=2,
        ge=1,
        description="Values this short with no code ('WAFER') are options; longer ones are text.",
    )
    COMPARATOR_GENERIC_LABEL_WORDS: list[str] = Field(
        default_factory=lambda: [
            "valve",
            "moc",
            "material",
            "of",
            "construction",
            "type",
            "the",
            "for",
            "and",
            "max",
            "maximum",
            "min",
            "minimum",
            "range",
            "at",
        ],
        description=(
            "Label words that do not name the attribute; without an ontology the other label "
            "words must all appear in a catalogue field's label (JSON list)."
        ),
    )
    COMPARATOR_CODE_NOISE_WORDS: list[str] = Field(
        default_factory=lambda: [
            "astm",
            "asme",
            "gr",
            "grade",
            "type",
            "or",
            "and",
            "equivalent",
            "equivelent",
            "eqv",
            "with",
            "of",
            "the",
            "to",
            "as",
            "per",
        ],
        description="Words ignored when comparing codes ('ASTM A216 Gr. WCB' -> a216, wcb).",
    )
    COMPARATOR_DESIGNATION_PREFIXES: list[str] = Field(
        default_factory=lambda: ["pn", "class", "dn", "nb"],
        description="Designations written before their number ('PN 16', 'DN 50') (JSON list).",
    )
    COMPARATOR_DIMENSION_WORDS: list[str] = Field(
        default_factory=lambda: ["dimension", "dimensions", "weight", "wt", "face to face"],
        description="A table whose header or section names one of these is a dimension table.",
    )
    CATALOGUE_OPTION_SECTIONS: list[str] = Field(
        default_factory=lambda: ["ordering information", "how to order", "ordering code"],
        description="A catalogue page with a section naming one of these lists its options.",
    )
    COMPARATOR_DIMENSION_MIN_LETTER_COLUMNS: int = Field(
        default=2,
        ge=1,
        description=(
            "A header with this many one-letter columns (A, B, H: drawing callouts) is a "
            "dimension table."
        ),
    )
    COMPARATOR_REL_TOLERANCE: float = Field(
        default=0.001,
        ge=0,
        description="Relative tolerance for numeric comparison after unit conversion.",
    )
    COMPARATOR_VALUE_MAX_WORDS: int = Field(
        default=4,
        ge=1,
        description="A catalogue value this short without codes ('Flanged') states an option; "
        "a longer one without codes is a sentence and never makes a requirement missing.",
    )
    COMPARATOR_MAX_EVIDENCE: int = Field(
        default=3, ge=1, description="Evidence rows kept per requirement and manufacturer."
    )
    QUOTE_MATCH_THRESHOLD: float = Field(
        default=0.9,
        ge=0,
        le=1,
        description="Every piece of a cited quote must match its page at least this well "
        "(normalized fuzzy partial ratio).",
    )
    STATUS_SCORES: dict[str, float] = Field(
        default_factory=lambda: {
            "full_match": 1.0,
            "partial_match": 0.5,
            "missing_capability": 0.0,
        },
        description="Score per decided status (CLAUDE.md section 7); other statuses are "
        "excluded from scores (JSON object).",
    )
    CATALOGUE_OVERVIEW_MIN_HEADINGS: int = Field(
        default=3,
        ge=2,
        description="A catalogue page naming this many products is an overview of them.",
    )
    EXTRACTION_TITLE_FRAGMENT_MAX_WORDS: int = Field(
        default=3,
        ge=1,
        description="An item title this short, with no digit and no product, is a fragment.",
    )
    EXTRACTION_TITLE_SENTENCE_MIN_WORDS: int = Field(
        default=25,
        ge=1,
        description="An item title this long, with no product, is running text.",
    )
    EXTRACTION_TITLE_SENTENCE_WORDS: list[str] = Field(
        default_factory=lambda: ["shall", "must", "should", "will", "are", "be"],
        description="Words that make an item title a sentence, never one that is also a code "
        "('IS 6438') (JSON list).",
    )
    EXTRACTION_MERGED_TITLE_LEAD_WORDS: int = Field(
        default=2,
        ge=1,
        description="Comma-separated parts of an Item Category row starting with this many same "
        "words are several products merged into one item.",
    )
    EXTRACTION_BAD_TITLE_SHARE: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Tender is unreliable if this share of items have such titles.",
    )
    EXTRACTION_MAX_ITEM_REQUIREMENTS: int = Field(
        default=50,
        ge=1,
        description="Tender is unreliable if one item has more atomic requirements than this.",
    )
    EXTRACTION_OCR_MIN_CONFIDENCE: float = Field(
        default=0.7,
        ge=0,
        le=1,
        description="OCR pages with a lower mean confidence are low-confidence.",
    )
    EXTRACTION_LOW_OCR_SHARE: float = Field(
        default=0.2,
        ge=0,
        le=1,
        description="Tender is unreliable if this share of item requirements cite such pages.",
    )
    RANKING_MIN_COVERAGE_PCT: float = Field(
        default=50.0,
        ge=0,
        le=100,
        description="Manufacturers with less evidence coverage are marked low coverage and "
        "ranked after the others.",
    )
    LLM_PROVIDER: LLMProvider = Field(default=LLMProvider.GEMINI)
    LLM_MODEL: str = Field(default="gemini-3.8-flash", description="Model id at the provider.")
    GEMINI_API_KEY: SecretStr | None = Field(
        default=None, description="Needed by every command that calls the LLM."
    )
    GEMINI_API_BASE: str = Field(default="https://generativelanguage.googleapis.com/v1beta")
    LLM_JUDGE_ENABLED: bool = Field(
        default=False,
        description="API runs send free-text requirements to the LLM judge (the CLI: --judge).",
    )
    LLM_JUDGE_MAX_CALLS: int = Field(
        default=200, ge=1, description="Most LLM judge calls for one tender, to protect the quota."
    )
    LLM_JUDGE_MAX_FAILURES: int = Field(
        default=3, ge=1, description="LLM judge failures in a row before a run stops calling."
    )
    LLM_TEMPERATURE: float = Field(default=0.0, ge=0, le=2)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=16384, ge=1)
    LLM_TIMEOUT_SECONDS: float = Field(default=120.0, gt=0)
    LLM_MAX_RETRIES: int = Field(
        default=4, ge=0, description="Retries after a rate limit, server error or network failure."
    )
    LLM_RETRY_BACKOFF_SECONDS: float = Field(
        default=2.0, ge=0, description="First retry delay; doubles on each further retry."
    )
    SPEC_EXTRACT_BATCH_CHUNKS: int = Field(
        default=25, ge=1, description="Chunks sent to the LLM in one spec-extraction call."
    )
    TABLE_CONTINUATION_EDGE_FRACTION: float = Field(
        default=0.25,
        gt=0,
        lt=0.5,
        description="A table ending/starting this close to a page edge may continue across it.",
    )

    EMBEDDING_MODEL: str = Field(
        default="BAAI/bge-m3",
        min_length=1,
        description="sentence-transformers model id; changing it needs a matching vector size.",
    )
    EMBEDDING_DEVICE: EmbeddingDevice = Field(default=EmbeddingDevice.AUTO)
    EMBEDDING_FP16: bool = Field(
        default=True, description="Half precision on CUDA (ignored on CPU)."
    )
    EMBEDDING_BATCH_SIZE: int = Field(default=16, ge=1, le=256)
    EMBEDDING_MAX_TOKENS: int = Field(
        default=512, ge=32, le=8192, description="Longer chunk text is truncated by the model."
    )

    QUERY_TYPO_MAX_DISTANCE: int = Field(
        default=2, ge=0, le=3, description="Edit distance for fixing tender typos (0 = off)."
    )
    QUERY_TYPO_MIN_LENGTH: int = Field(
        default=5, ge=3, description="Shorter query words are never typo-corrected."
    )
    BM25_K1: float = Field(default=1.5, ge=0, description="BM25 term-frequency saturation.")
    BM25_B: float = Field(default=0.75, ge=0, le=1, description="BM25 length normalization.")
    RETRIEVAL_CANDIDATES: int = Field(
        default=50, ge=1, description="Candidates taken from each of BM25 and dense search."
    )
    DENSE_SEARCH: DenseSearch = Field(default=DenseSearch.EXACT)
    HNSW_EF_SEARCH: int = Field(
        default=100, ge=1, le=1000, description="HNSW candidate list size (recall vs speed)."
    )
    RRF_K: int = Field(default=60, ge=1, description="Reciprocal rank fusion constant.")
    RRF_BM25_WEIGHT: float = Field(default=1.0, gt=0, description="BM25 share of the fusion.")
    RRF_DENSE_WEIGHT: float = Field(default=0.7, ge=0, description="Dense share; 0 = BM25 only.")
    RERANKER_MODEL: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        min_length=1,
        description="sentence-transformers CrossEncoder id (English MiniLM by default).",
    )
    RERANKER_DEVICE: EmbeddingDevice = Field(default=EmbeddingDevice.AUTO)
    RERANKER_FP16: bool = Field(
        default=False, description="Half precision on CUDA (for large models; ignored on CPU)."
    )
    RERANKER_BATCH_SIZE: int = Field(default=32, ge=1, le=256)
    RERANKER_MAX_TOKENS: int = Field(
        default=512, ge=32, le=8192, description="Query + chunk longer than this is truncated."
    )
    RERANK_TOP_K: int = Field(default=5, ge=1, description="Chunks kept after reranking.")
    RERANK_MIN_SCORE: float = Field(
        default=0.3,
        ge=0,
        le=1,
        description="Top rerank score (0-1) below this means insufficient evidence.",
    )

    TENDER_UPLOAD_MAX_MB: int = Field(
        default=50, ge=1, description="Largest bid.pdf the API accepts."
    )
    RUNS_LIST_LIMIT: int = Field(default=100, ge=1, description="Most runs `GET /runs` returns.")
    RUN_ERROR_MAX_CHARS: int = Field(
        default=2000, ge=1, description="Longest failure reason kept on a run."
    )
    TENDER_LINK_RULES_FILE: Path = Field(
        default=Path("config/tender_links.yaml"),
        description="URL patterns deciding which links in a tender are fetched or skipped.",
    )
    TENDER_LINK_MAX_DEPTH: int = Field(
        default=3, ge=1, le=5, description="Follow links this many hops from bid.pdf."
    )
    HTTP_TIMEOUT_S: float = Field(default=60.0, gt=0, description="Per-request download timeout.")
    HTTP_MAX_DOWNLOAD_MB: int = Field(
        default=100, ge=1, description="Larger linked files are refused."
    )
    HTTP_USER_AGENT: str = Field(
        default="tender-matcher/0.1 (+document fetcher)",
        min_length=1,
        description="User-Agent sent when downloading tender attachments.",
    )

    CHUNK_TARGET_TOKENS: int = Field(
        default=400, ge=50, description="Aim for prose chunks of this size."
    )
    CHUNK_MAX_TOKENS: int = Field(default=500, ge=50, description="Hard cap; longer text is split.")
    CHUNK_OVERLAP_TOKENS: int = Field(
        default=50, ge=0, description="Repeated between prose chunks."
    )
    CHUNK_HEADING_MAX_WORDS: int = Field(
        default=10, ge=1, description="Text blocks longer than this are never treated as headings."
    )
    CHUNK_HEADING_MIN_LATIN_RATIO: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="Headings must be at least this share Latin letters (rejects garbled Hindi).",
    )
    CHUNK_HEADING_MAX_DIGIT_SHARE: float = Field(
        default=0.6,
        ge=0,
        le=1,
        description="Lines with a larger share of digits are codes, never headings.",
    )
    PAGE_MARGIN_BAND_FRACTION: float = Field(
        default=0.12,
        gt=0,
        lt=0.5,
        description=(
            "Top/bottom share of a PDF page where running headers and footers sit. A heading "
            "in the bottom band does not carry over to the next page."
        ),
    )
    BOILERPLATE_MIN_PAGE_RATIO: float = Field(
        default=0.5,
        gt=0,
        le=1,
        description="A margin line on at least this share of pages is boilerplate (dropped).",
    )
    BOILERPLATE_MIN_PAGES: int = Field(
        default=2, ge=2, description="...and on at least this many pages (one page is no repeat)."
    )
    BOILERPLATE_MIN_LETTERS: int = Field(
        default=3,
        ge=1,
        description="Lines with fewer letters (page numbers, lone digits) are never boilerplate.",
    )
    CHUNK_MODEL_HEADER_KEYWORDS: list[str] = Field(
        default_factory=lambda: ["model"],
        min_length=1,
        description=(
            'Table header words that mark the model column, as a JSON list. Not "size" or '
            '"type": those columns hold sizes and product types, never model names.'
        ),
    )
    CHUNK_VARIANT_HEADER_KEYWORDS: list[str] = Field(
        default_factory=lambda: ["material", "materials", "specification"],
        description=(
            "First words of header labels that mark version columns (one material grade per "
            "column). Two or more such columns -> one chunk per row and version, as a JSON list."
        ),
    )

    OCR_ENABLED: bool = Field(default=True, description="OCR pages that have no text layer.")
    OCR_MIN_TEXT_CHARS: int = Field(
        default=50,
        ge=0,
        description="Below this many non-whitespace text-layer characters a page is 'sparse'.",
    )
    OCR_MIN_IMAGE_COVERAGE: float = Field(
        default=0.5,
        ge=0,
        le=1,
        description="A sparse page is OCR'd only if images cover at least this page fraction.",
    )
    OCR_DPI: int = Field(default=300, ge=72, le=600, description="Render resolution for OCR.")
    OCR_LANGUAGE: str = Field(
        default="eng+hin",
        pattern=r"^[a-z_]+(\+[a-z_]+)*$",
        description="Tesseract language code(s); GeM tenders are often bilingual Hindi/English.",
    )
    TESSERACT_CMD: str | None = Field(
        default=None, description="Path to the tesseract binary if it is not on PATH."
    )
    TESSDATA_DIR: Path | None = Field(
        default=None,
        description="Folder with *.traineddata files, if not Tesseract's built-in tessdata.",
    )

    @model_validator(mode="after")
    def _chunk_sizes_are_consistent(self) -> Settings:
        """Target must fit under the cap, and overlap must be smaller than the target."""
        if self.CHUNK_TARGET_TOKENS > self.CHUNK_MAX_TOKENS:
            raise ValueError("CHUNK_TARGET_TOKENS must be <= CHUNK_MAX_TOKENS")
        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_TARGET_TOKENS:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be < CHUNK_TARGET_TOKENS")
        return self

    @field_validator("OCR_LANGUAGE")
    @classmethod
    def _english_first(cls, value: str) -> str:
        """Require `eng` as the primary language.

        Tesseract favours the first language: with `hin` first, Latin spec codes are misread
        (measured: `SS316` -> `55376`, `1450 RPM` -> `450 RPM`), which would corrupt evidence.
        """
        if value.split("+")[0] != "eng":
            raise ValueError("OCR_LANGUAGE must start with 'eng', e.g. 'eng' or 'eng+hin'")
        return value

    @field_validator("TESSDATA_DIR")
    @classmethod
    def _tessdata_dir_without_spaces(cls, value: Path | None) -> Path | None:
        """Reject whitespace: pytesseract cannot pass a quoted path to Tesseract on Windows."""
        if value is not None and any(ch.isspace() for ch in str(value)):
            raise ValueError("TESSDATA_DIR must not contain spaces; use a path like C:/tessdata")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance.

    Cached so the environment is read once. Tests should build `Settings(...)` directly and pass
    it to `create_app` instead of mutating the environment.

    Returns:
        The loaded settings.
    """
    return Settings()
