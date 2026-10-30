"""Unit normalization with `pint`, plus the ontology's size and pressure-class tables.

Every numeric value is converted to its attribute's canonical unit from the ontology, so the
comparator never compares "10 kg/cm²" with "10 bar" (CLAUDE.md B5). PN and Class are separate
designation systems and are mapped only through the EN 1759-1 table, never via bar (B6).
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import pint

from app.core.exceptions import UnitConversionError
from app.ontology.schema import Ontology

_REGISTRY: Final = pint.UnitRegistry()


@dataclass(frozen=True, slots=True)
class CanonicalValue:
    """A number in its attribute's canonical unit."""

    value: float
    unit: str


def unit_expression(ontology: Ontology, unit: str) -> str:
    """Resolve a unit as written ("kg/cm²", "m3/hr") to a pint expression.

    Args:
        ontology: Provides the spelling table.
        unit: Unit text from a document.

    Returns:
        The pint unit expression.

    Raises:
        UnitConversionError: If the spelling is not in the ontology.
    """
    key = " ".join(unit.strip().lower().split())
    if key not in ontology.units:
        raise UnitConversionError(f"Unknown unit {unit!r}", details={"unit": unit})
    return ontology.units[key]


def to_canonical(ontology: Ontology, attribute: str, value: float, unit: str) -> CanonicalValue:
    """Convert a value to the canonical unit of `attribute`.

    Args:
        ontology: The category ontology.
        attribute: Canonical attribute name, e.g. "pressure_rating".
        value: The number as written.
        unit: Its unit as written.

    Returns:
        The value in the attribute's canonical unit.

    Raises:
        UnitConversionError: If the attribute is unknown or has no unit, the unit is unknown,
            or the dimensions do not match (e.g. "mm" for a pressure).
    """
    spec = ontology.attribute(attribute)
    if spec is None or spec.canonical_unit is None:
        raise UnitConversionError(
            f"Attribute {attribute!r} has no canonical unit", details={"attribute": attribute}
        )
    try:
        quantity = _REGISTRY.Quantity(value, unit_expression(ontology, unit))
        converted = quantity.to(spec.canonical_unit)
    except pint.errors.PintError as exc:
        raise UnitConversionError(
            f"Cannot convert {value} {unit} to {spec.canonical_unit} for {attribute}",
            details={"attribute": attribute, "unit": unit},
        ) from exc
    return CanonicalValue(value=float(converted.magnitude), unit=spec.canonical_unit)


def convert(value: float, unit: str, target: str) -> float:
    """Convert a value between two pint unit expressions ("psi" -> "bar").

    Args:
        value: The number.
        unit: Its pint unit expression.
        target: The pint unit expression wanted.

    Returns:
        The converted number.

    Raises:
        UnitConversionError: If either unit does not parse or the dimensions differ.
    """
    try:
        return float(_REGISTRY.Quantity(value, unit).to(target).magnitude)
    except (pint.errors.PintError, AttributeError, TypeError, ValueError) as exc:
        raise UnitConversionError(
            f"Cannot convert {value} {unit} to {target}", details={"unit": unit, "target": target}
        ) from exc


def _nps_value(nps: str) -> Fraction:
    """Parse "2", "1/2", "1-1/2" or "1.5" into an exact number of inches."""
    whole, _, frac = nps.strip().partition("-")
    if frac:
        return Fraction(whole) + Fraction(frac)
    return Fraction(whole)


def nps_to_dn(ontology: Ontology, nps: str) -> int:
    """Map a nominal pipe size in inches to DN (mm).

    Args:
        ontology: Provides the NPS->DN table.
        nps: Size as written, e.g. "3", "1-1/2", "1.5".

    Returns:
        The DN number.

    Raises:
        UnitConversionError: If the size is not in the table or is not a number.
    """
    try:
        wanted = _nps_value(nps)
        table = {_nps_value(k): dn for k, dn in ontology.nps_to_dn.items()}
    except (ValueError, ZeroDivisionError) as exc:
        raise UnitConversionError(f"Not a pipe size: {nps!r}", details={"nps": nps}) from exc
    if wanted not in table:
        raise UnitConversionError(f"NPS {nps} not in size table", details={"nps": nps})
    return table[wanted]


def class_to_pn(ontology: Ontology, pressure_class: int) -> int | None:
    """Return the PN designation equivalent to an ASME Class (EN 1759-1), if one is defined."""
    return ontology.class_to_pn.get(pressure_class)


def pn_to_class(ontology: Ontology, pn: int) -> int | None:
    """Return the ASME Class equivalent to a PN designation (EN 1759-1), if one is defined."""
    return next((cls for cls, value in ontology.class_to_pn.items() if value == pn), None)


def units_compatible(unit: str, other: str) -> bool:
    """True if two pint unit expressions measure the same dimension ("m**3/h" and "L/s").

    Args:
        unit: A pint unit expression.
        other: Another pint unit expression.

    Returns:
        Whether one converts to the other; False if either does not parse.
    """
    try:
        return bool(_REGISTRY.parse_units(unit).is_compatible_with(_REGISTRY.parse_units(other)))
    except pint.errors.PintError, AttributeError, TypeError, ValueError:
        return False
