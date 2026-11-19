"""Unit normalization with `pint`, plus the ontology's size and pressure-class tables.

Every numeric value is converted to its attribute's canonical unit from the ontology, so the
comparator never compares "10 kg/cm²" with "10 bar" (CLAUDE.md B5). PN and Class are separate
designation systems and are mapped only through the EN 1759-1 table, never via bar (B6).
"""

import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from typing import Final

import pint

from app.core.exceptions import UnitConversionError
from app.ontology.schema import Attribute, Ontology

_REGISTRY: Final = pint.UnitRegistry()
# Distinct unit pairs seen in a process; matching asks the same few pairs millions of times.
_UNIT_PAIR_CACHE_SIZE: Final = 4096
# Fraction characters as written fractions; NFKC would turn one and a half into "11/2" (11 halves).
_VULGAR: Final = {
    "\u00bc": "1/4",
    "\u00bd": "1/2",
    "\u00be": "3/4",
    "\u215b": "1/8",
    "\u215c": "3/8",
    "\u215d": "5/8",
    "\u215e": "7/8",
}
_VULGAR_CHAR: Final = re.compile("[" + "".join(_VULGAR) + "]")
_FRACTION_SLASH: Final = "\u2044"


def spell_fractions(text: str) -> str:
    """Write fraction characters out: "1½" -> "1 1/2", "¾" -> "3/4" (read before NFKC folding).

    Args:
        text: A value as printed.

    Returns:
        The text with each fraction character as "n/d" after a space, and fraction slashes as "/".
    """
    spelled = _VULGAR_CHAR.sub(lambda m: " " + _VULGAR[m.group(0)], text)
    return spelled.replace(_FRACTION_SLASH, "/").strip() if spelled != text else spelled


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


def known_unit(ontology: Ontology, unit: str) -> str | None:
    """The pint unit of a folded spelling; a multi-word one may be joined ("degreecelsius").

    Args:
        ontology: Unit spellings.
        unit: A folded spelling as printed.

    Returns:
        The pint expression, or None if the spelling is not known.
    """
    expr = ontology.units.get(unit) or ontology.prefix_units.get(unit)
    if expr is not None:
        return expr
    joined = {k.replace(" ", ""): v for k, v in ontology.units.items() if " " in k}
    return joined.get(unit)


def canonical_number(
    number: float, unit: str | None, attribute: Attribute, ontology: Ontology
) -> float | None:
    """A printed number in the attribute's canonical unit, or None if it cannot be converted.

    Args:
        number: The number as printed.
        unit: Its unit as printed (folded), or None for a bare number.
        attribute: The attribute it is a value of.
        ontology: Unit spellings and the NPS table (inch sizes go through it, not pint).

    Returns:
        The converted number, or None for a bare number, an unknown unit or another dimension.
    """
    if unit is None or attribute.canonical_unit is None:
        return None
    expr = known_unit(ontology, unit)
    if expr is None:
        return None
    try:
        if attribute.nominal_pipe_size and expr == "inch":
            return float(nps_to_dn(ontology, f"{number:g}"))
        return convert(number, expr, attribute.canonical_unit)
    except UnitConversionError:
        return None


def class_to_pn(ontology: Ontology, pressure_class: int) -> int | None:
    """Return the PN designation equivalent to an ASME Class (EN 1759-1), if one is defined."""
    return ontology.class_to_pn.get(pressure_class)


def pn_to_class(ontology: Ontology, pn: int) -> int | None:
    """Return the ASME Class equivalent to a PN designation (EN 1759-1), if one is defined."""
    return next((cls for cls, value in ontology.class_to_pn.items() if value == pn), None)


@lru_cache(maxsize=_UNIT_PAIR_CACHE_SIZE)
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
