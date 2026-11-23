"""`config/gem_marketplace.yaml`: which GeM category pages hold listings for a product type."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import ConfigFileError


class GemCategories(BaseModel):
    """GeM category slugs for one ontology product type."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_type: str = Field(pattern=r"^[a-z0-9_]+$")
    categories: list[str] = Field(min_length=1)
    exclude: list[str] = Field(
        default_factory=list,
        description="Look-alike products listed in these categories that are not this type "
        "(a title or model naming one is rejected).",
    )


class GemMarketplaceConfig(BaseModel):
    """The whole file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_types: list[GemCategories] = Field(default_factory=list)
    empty_values: list[str] = Field(default_factory=list)
    rank_ignore_words: list[str] = Field(
        default_factory=list,
        description="Item title words that never tell products apart when ranking listings.",
    )
    seller_rank: list[str] = Field(
        default_factory=list,
        description="How a product is sold (card 'sold_as' classes), best first.",
    )
    word_equivalents: dict[str, str] = Field(
        default_factory=dict,
        description="One-word abbreviation -> its full words, applied to item titles and GeM "
        "category names before their shared words are counted.",
    )
    category_cut_words: list[str] = Field(
        default_factory=list,
        description="Words a category name or item title is cut before ('as per', 'conforming').",
    )
    category_filler_words: list[str] = Field(
        default_factory=list,
        description="Words never counted as shared, and left out when category slugs are compared.",
    )

    def categories_for(self, product_type: str) -> list[str]:
        """Category slugs configured for a product type (empty if none).

        Args:
            product_type: An ontology product type.

        Returns:
            The slugs, in reading order.
        """
        return next(
            (p.categories for p in self.product_types if p.product_type == product_type), []
        )

    def excludes_for(self, product_type: str) -> list[str]:
        """Look-alike product phrases configured for a product type (empty if none).

        Args:
            product_type: An ontology product type.

        Returns:
            The phrases.
        """
        return next((p.exclude for p in self.product_types if p.product_type == product_type), [])


def load_gem_config(path: Path) -> GemMarketplaceConfig:
    """Read and validate the GeM Marketplace config.

    Args:
        path: The YAML file.

    Returns:
        The config.

    Raises:
        ConfigFileError: If the file is missing or invalid.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return GemMarketplaceConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigFileError(
            f"Invalid GeM Marketplace config: {path}", details={"error": str(exc)}
        ) from exc
