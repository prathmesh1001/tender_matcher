"""Give every chunk a section category (costing, eligibility, dimensions, ...).

The categories and their phrases live in `config/section_categories.yaml`; nothing about them
is hard-coded here. Rules, all on normalized text (`normalize_for_search`):

1. The chunk's section heading names a category -> that category. Headings are the strongest
   signal ("3. Warranty", "Dimensions - PN 16").
2. Otherwise the chunk's own text: the category with the most phrase hits, ties broken by the
   order of the rules file. Needed because most GeM bid.pdf rows have no heading at all: they
   are label/value rows like "EMD Amount: 24000" under a "Bid Details" title.
3. Otherwise the default category ("other").

Matching never decides compliance; it only routes requirements to the right evidence.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.exceptions import ConfigFileError
from app.retrieval.text_normalizer import PhrasePatterns, SearchVocabulary, phrase_patterns
from app.schemas.sections import CategorySource, ChunkCategory, SectionCategory, SectionRules


def load_section_rules(path: Path) -> SectionRules:
    """Read and validate the section categories file.

    Args:
        path: The rules YAML.

    Returns:
        Validated rules.

    Raises:
        ConfigFileError: If the file is unreadable or invalid.
    """
    try:
        return SectionRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigFileError(
            f"Invalid section categories file {path}: {exc}", details={"path": str(path)}
        ) from exc


def _hits(patterns: PhrasePatterns, text: str) -> int:
    """Number of distinct phrases of one category found in `text`."""
    return sum(1 for p in patterns if p.search(text))


def _best(scored: Sequence[tuple[str, int]]) -> str | None:
    """Category with the most hits (first wins a tie), or None if nothing matched."""
    best_name, best_hits = None, 0
    for name, hits in scored:
        if hits > best_hits:
            best_name, best_hits = name, hits
    return best_name


@dataclass(frozen=True, slots=True)
class SectionClassifier:
    """Compiled heading and text phrases per category, in rules-file order."""

    rules: SectionRules
    headings: tuple[tuple[str, PhrasePatterns], ...]
    texts: tuple[tuple[str, PhrasePatterns], ...]

    @classmethod
    def from_rules(cls, rules: SectionRules, vocabulary: SearchVocabulary) -> SectionClassifier:
        """Compile the rules; phrases are normalized exactly like chunk `search_text`."""
        categories = rules.categories
        return cls(
            rules=rules,
            headings=tuple((c.name, phrase_patterns(c.headings, vocabulary)) for c in categories),
            texts=tuple((c.name, phrase_patterns(c.text, vocabulary)) for c in categories),
        )

    @property
    def default(self) -> str:
        """Category for chunks nothing matched."""
        return self.rules.default

    def category(self, name: str) -> SectionCategory | None:
        """Rules for one category (None for the default)."""
        return next((c for c in self.rules.categories if c.name == name), None)

    def from_heading(self, heading: str) -> str | None:
        """Category a normalized heading names, if any."""
        return _best([(name, _hits(p, heading)) for name, p in self.headings]) if heading else None

    def from_text(self, text: str) -> str | None:
        """Category a normalized chunk text names, if any."""
        return _best([(name, _hits(p, text)) for name, p in self.texts]) if text else None

    def classify(self, heading: str, text: str) -> ChunkCategory:
        """Category of one chunk from its normalized heading and text (see module docstring)."""
        by_heading = self.from_heading(heading)
        if by_heading is not None:
            return ChunkCategory(category=by_heading, source=CategorySource.HEADING)
        by_text = self.from_text(text)
        if by_text is not None:
            return ChunkCategory(category=by_text, source=CategorySource.TEXT)
        return ChunkCategory(category=self.default, source=CategorySource.DEFAULT)
