"""Use case: which manufacturers can be matched, from the registered catalogues."""

from collections.abc import Sequence

from app.core.config import Settings
from app.core.exceptions import InvalidInputError
from app.ingestion.registry import load_registry
from app.schemas.manufacturers import Manufacturer
from app.schemas.sources import RegisteredSource


class ManufacturerService:
    """Read manufacturers from the catalogue registry."""

    def __init__(self, sources: Sequence[RegisteredSource]) -> None:
        self._sources = sources

    def list_manufacturers(self) -> list[Manufacturer]:
        """List each manufacturer per category, with what its catalogues cover.

        Returns:
            Manufacturers sorted by category, then name.
        """
        groups: dict[tuple[str, str], list[RegisteredSource]] = {}
        for source in self._sources:
            groups.setdefault((source.category, source.manufacturer), []).append(source)
        return [
            Manufacturer(
                name=name,
                category=category,
                product_types=sorted({t for s in sources for t in s.product_types}),
                documents=len(sources),
            )
            for (category, name), sources in sorted(groups.items())
        ]

    def require(self, category: str, names: Sequence[str]) -> None:
        """Check that every manufacturer has catalogues in this category.

        Args:
            category: The category asked for.
            names: The manufacturers asked for.

        Raises:
            InvalidInputError: If any has none, naming them and what is available.
        """
        available = {s.manufacturer for s in self._sources if s.category == category}
        unknown = sorted(set(names) - available)
        if unknown:
            raise InvalidInputError(
                f"No catalogues in category {category!r} for: {', '.join(unknown)}",
                details={"unknown": unknown, "available": sorted(available)},
            )


def build_manufacturer_service(settings: Settings) -> ManufacturerService:
    """Wire the service from the registry on disk.

    Args:
        settings: Application settings.

    Returns:
        A ready service.

    Raises:
        SourceRegistryError: If a registry file is invalid.
    """
    return ManufacturerService(load_registry(settings.MANUFACTURER_DATA_DIR))
