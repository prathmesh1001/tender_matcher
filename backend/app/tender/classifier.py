"""Classify one tender line: type, category, evidence source, mandatory, named make.

The category comes from the section rules (`config/section_categories.yaml`) and says where a
vendor's evidence would come from. Everything else comes from `config/requirement_rules.yaml`.
A neutral vocabulary is used, so classification is the same with or without an ontology.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.config import Settings
from app.core.exceptions import ConfigFileError
from app.ingestion.section_classifier import SectionClassifier, load_section_rules
from app.retrieval.text_normalizer import (
    PhrasePatterns,
    SearchVocabulary,
    normalize_for_search,
    phrase_patterns,
)
from app.schemas.enums import RequirementType
from app.schemas.requirements import RequirementRules
from app.schemas.sections import EvidenceSource
from app.tender.normalizer import has_code

_NEUTRAL = SearchVocabulary(code_rules=())


@dataclass(frozen=True, slots=True)
class LineClass:
    """What the classifier says about one line."""

    category: str
    evidence: EvidenceSource | None  # None: the category could not be told
    req_type: RequirementType
    mandatory: bool | None  # None: the line does not say
    named_make: str | None
    or_equivalent: bool


def load_requirement_rules(path: Path) -> RequirementRules:
    """Read and validate the requirement rules file.

    Args:
        path: The rules YAML.

    Returns:
        Validated rules.

    Raises:
        ConfigFileError: If the file is unreadable or invalid.
    """
    try:
        return RequirementRules.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigFileError(
            f"Invalid requirement rules file {path}: {exc}", details={"path": str(path)}
        ) from exc


def _hit(patterns: PhrasePatterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


@dataclass(frozen=True, slots=True)
class RequirementClassifier:
    """Compiled section categories and requirement rules."""

    sections: SectionClassifier
    rules: RequirementRules
    vague: PhrasePatterns
    mandatory: PhrasePatterns
    optional: PhrasePatterns
    or_equivalent: PhrasePatterns
    makes: tuple[re.Pattern[str], ...]

    @classmethod
    def from_settings(cls, settings: Settings) -> RequirementClassifier:
        """Load and compile both rule files named by the settings."""
        return cls.from_rules(
            SectionClassifier.from_rules(
                load_section_rules(settings.SECTION_CATEGORIES_FILE), _NEUTRAL
            ),
            load_requirement_rules(settings.REQUIREMENT_RULES_FILE),
        )

    @classmethod
    def from_rules(
        cls, sections: SectionClassifier, rules: RequirementRules
    ) -> RequirementClassifier:
        """Compile the phrases and patterns."""
        return cls(
            sections=sections,
            rules=rules,
            vague=phrase_patterns(rules.vague, _NEUTRAL),
            mandatory=phrase_patterns(rules.mandatory, _NEUTRAL),
            optional=phrase_patterns(rules.optional, _NEUTRAL),
            or_equivalent=phrase_patterns(rules.or_equivalent, _NEUTRAL),
            makes=tuple(re.compile(p, re.IGNORECASE) for p in rules.make_patterns),
        )

    def named_make(self, text: str) -> str | None:
        """The make a line names ("Make: M/s. X"), as printed, or None."""
        for pattern in self.makes:
            found = pattern.search(text)
            if found:
                return found.group("make").strip() or None
        return None

    def _mandatory(self, normalized: str, is_item: bool) -> bool | None:
        """Mandatory phrase, else optional phrase, else what an item line means: required."""
        if _hit(self.mandatory, normalized):
            return True
        if _hit(self.optional, normalized):
            return False
        return True if is_item else None

    def classify(
        self, text: str, heading: str | None = None, *, is_item: bool = False
    ) -> LineClass:
        """Classify one line.

        Args:
            text: The line (English part), label included.
            heading: Its section heading, if any.
            is_item: The line belongs to an item's schedule row.

        Returns:
            The classification. A line naming no category gets the item default when
            `is_item`, else the default category with no evidence source.
        """
        normalized = normalize_for_search(text, _NEUTRAL)
        found = self.sections.classify(normalize_for_search(heading or "", _NEUTRAL), normalized)
        name = found.category
        if name == self.sections.default and is_item:
            name = self.rules.item_default_category
        category = self.sections.category(name)
        make = self.named_make(text)
        vague = _hit(self.vague, normalized) and make is None and not has_code(text)
        if vague or category is None:
            req_type = RequirementType.VAGUE if vague else self.rules.unclassified_type
        else:
            req_type = self.rules.kind_types[category.kind]
        return LineClass(
            category=name,
            evidence=category.evidence if category else None,
            req_type=req_type,
            mandatory=self._mandatory(normalized, is_item),
            named_make=make,
            or_equivalent=_hit(self.or_equivalent, normalized),
        )
