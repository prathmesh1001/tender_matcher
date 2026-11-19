"""A product key for an item whose title names no ontology product type ("foot_valve").

Items outside the ontology still need vendors: GeM Marketplace is searched by the key and its
listings are stored under it as their product type. The key is chosen so that matching finds
it again in the title without an ontology (`declared_types_named`: the key's words appear in
order in the title):

1. a product of the ontology's category that has no type (`other_products`, longest phrase
   first): "PVC foot valve Ball type" -> `foot_valve`; a part word after it keys the part
   ("Brass foot valve nipple set" -> `foot_valve_nipple`, see `named_as`);
2. else the first run of up to `max_words` product words of the title, before any accessory
   word ("with"), skipping filler words ("supply", "of") and anything holding a digit (sizes,
   codes, "(V2)"): "Supply of Hydraulic Hose Pipe 1/2 inch" -> `hydraulic_hose_pipe`.

Pure functions; word lists come from the ontologies and settings, never from code.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.ontology.schema import Ontology
from app.tender.normalizer import words


@dataclass(frozen=True, slots=True)
class TitleKeyOptions:
    """Word lists and the key length."""

    stop_words: frozenset[str]  # filler words never part of a key ("supply", "of")
    max_words: int
    min_word_length: int = 2


def key_phrase(key: str) -> str:
    """A key as words ("foot_valve" -> "foot valve")."""
    return key.replace("_", " ")


def _at(title_words: list[str], phrase: list[str], start: int = 0) -> int | None:
    """Where the phrase first starts in the title words, or None."""
    n = len(phrase)
    return next(
        (i for i in range(start, len(title_words) - n + 1) if title_words[i : i + n] == phrase),
        None,
    )


def named_as(
    title_words: list[str], phrase: list[str], ontologies: Sequence[Ontology]
) -> list[str] | None:
    """How a title names a product phrase: as the product, as a part of it, or not at all.

    A part word after the phrase, before any accessory word, makes the item a part of the
    product: "Brass foot valve nipple set" buys a nipple, not a foot valve.

    Args:
        title_words: The title as words.
        phrase: The product phrase as words ("foot valve").
        ontologies: Their `part_words` and `accessory_words` are used.

    Returns:
        None if the title lacks the phrase; the phrase if it names the product; else the
        words from the phrase through the part word (["foot", "valve", "nipple"]).
    """
    start = _at(title_words, phrase) if phrase else None
    if start is None:
        return None
    end = start + len(phrase)
    tail = _before_accessory(title_words[end:], ontologies)
    parts = [words(p) for o in ontologies for p in o.part_words]
    found = [(i, len(p)) for p in parts if p for i in [_at(tail, p)] if i is not None]
    if not found:
        return phrase
    i, n = min(found)
    return title_words[start : end + i + n]


def _other_product(title_words: list[str], ontologies: Sequence[Ontology]) -> str | None:
    """The longest `other_products` phrase the title holds, as a key (through a part word)."""
    phrases = sorted(
        (words(p) for o in ontologies for p in o.other_products), key=len, reverse=True
    )
    for phrase in phrases:
        named = named_as(title_words, phrase, ontologies)
        if named:
            return "_".join(named)
    return None


def _before_accessory(title_words: list[str], ontologies: Sequence[Ontology]) -> list[str]:
    """The title words before its first accessory word ("with", "complete with")."""
    accessories = [words(a) for o in ontologies for a in o.accessory_words]
    for i in range(len(title_words)):
        if any(a and title_words[i : i + len(a)] == a for a in accessories):
            return title_words[:i]
    return title_words


def _first_run(title_words: list[str], options: TitleKeyOptions) -> list[str]:
    """The first run of consecutive product words, at most `max_words` long."""
    run: list[str] = []
    for word in title_words:
        product_word = (
            word not in options.stop_words
            and len(word) >= options.min_word_length
            and not any(ch.isdigit() for ch in word)
        )
        if product_word:
            run.append(word)
            if len(run) == options.max_words:
                break
        elif run:
            break
    return run


def title_key(title: str, ontologies: Sequence[Ontology], options: TitleKeyOptions) -> str | None:
    """The product key of an item title that names no ontology product type.

    Args:
        title: The item title.
        ontologies: Their `other_products` and `accessory_words` are used.
        options: Filler words and the key length.

    Returns:
        The key ("foot_valve", "hydraulic_hose_pipe"), or None if the title has no product word.
    """
    title_words = words(title)
    other = _other_product(title_words, ontologies)
    if other:
        return other
    run = _first_run(_before_accessory(title_words, ontologies), options)
    return "_".join(run) or None
