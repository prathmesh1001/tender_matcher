"""Load and validate ontology YAML files (one per product category)."""

from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from app.core.exceptions import OntologyError
from app.ontology.schema import Ontology

ONTOLOGY_SUFFIX: Final = ".yaml"


def load_ontology_file(path: Path) -> Ontology:
    """Read and validate one ontology file.

    Args:
        path: The YAML file.

    Returns:
        The validated ontology.

    Raises:
        OntologyError: If the file is unreadable, not YAML, or fails validation, or if a
            numeric attribute has no canonical unit.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        ontology = Ontology.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise OntologyError(f"Invalid ontology {path}: {exc}", details={"path": str(path)}) from exc
    missing = [
        a.name for a in ontology.attributes if a.dimension != "none" and not a.canonical_unit
    ]
    if missing:
        raise OntologyError(
            f"Numeric attributes without canonical_unit in {path}: {missing}",
            details={"path": str(path), "attributes": missing},
        )
    return ontology


def load_ontologies(directory: Path) -> list[Ontology]:
    """Load every ontology in a directory, sorted by file name.

    Args:
        directory: Folder with `<category>.yaml` files.

    Returns:
        One ontology per file.

    Raises:
        OntologyError: If the folder is missing or empty, or two files share a category.
    """
    if not directory.is_dir():
        raise OntologyError(
            f"Ontology dir not found: {directory}", details={"path": str(directory)}
        )
    ontologies = [load_ontology_file(p) for p in sorted(directory.glob(f"*{ONTOLOGY_SUFFIX}"))]
    if not ontologies:
        raise OntologyError(f"No ontology files in {directory}", details={"path": str(directory)})
    categories = [o.category for o in ontologies]
    if len(set(categories)) != len(categories):
        raise OntologyError(f"Duplicate ontology categories: {categories}")
    return ontologies
