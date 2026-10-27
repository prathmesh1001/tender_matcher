"""`normalize_for_search()`: the one text cleaner shared by the indexer and the query builder.

Catalogue chunks and tender queries go through the same function, so both sides always agree
(CLAUDE.md section 12, cases A1, A3, A4, A6, A8). Its output is `search_text`, used only for
BM25 and embeddings; quotes and evidence always come from the verbatim `raw_text`.

Steps, in order:
1. NFKC: ligatures ("Butterﬂy" -> "Butterfly"), superscripts ("m³" -> "m3"), NBSP -> space.
2. Fold curly quotes, primes and dashes to ASCII.
3. Lower-case.
4. Rewrite codes to one canonical form with the ontology's rules ("SS 316" -> "ss316").
5. Tokenize on anything that is not a letter, digit or "_" (hyphens split compounds; decimals
   like "7.5" stay whole). Non-Latin scripts (e.g. Hindi headings) drop out here.
6. Stem plain words with Snowball ("valves" -> "valv", "balls" -> "ball"); tokens with digits
   or "_" are codes and are never stemmed.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

import snowballstemmer

from app.ontology.schema import Ontology

_FOLD: Final = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{PRIME}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{DOUBLE PRIME}": '"',
        "\N{HYPHEN}": "-",
        "\N{NON-BREAKING HYPHEN}": "-",
        "\N{FIGURE DASH}": "-",
        "\N{EN DASH}": "-",
        "\N{EM DASH}": "-",
        "\N{MINUS SIGN}": "-",
        "\N{FRACTION SLASH}": "/",
    }
)
_TOKEN: Final = re.compile(r"[a-z0-9_]+(?:\.[0-9]+)?")
_CODE_LIKE: Final = re.compile(r"[0-9_]")


@dataclass(frozen=True, slots=True)
class SearchVocabulary:
    """Compiled code rules (in ontology order) used by `normalize_for_search`."""

    code_rules: tuple[tuple[re.Pattern[str], str], ...]

    @classmethod
    def from_ontologies(cls, ontologies: Sequence[Ontology]) -> SearchVocabulary:
        """Compile the code rules of every ontology, keeping file and rule order.

        Args:
            ontologies: Loaded ontologies.

        Returns:
            The vocabulary.
        """
        rules = tuple(
            (re.compile(rule.pattern), rule.canonical) for o in ontologies for rule in o.codes
        )
        return cls(code_rules=rules)


@lru_cache(maxsize=1)
def _stemmer() -> snowballstemmer.stemmer:
    """The English Snowball stemmer (stateless; built once)."""
    return snowballstemmer.stemmer("english")


def fold_text(text: str) -> str:
    """Apply steps 1-3: NFKC, quote/dash folding, lower-casing.

    Args:
        text: Any text.

    Returns:
        Folded, lower-cased text.
    """
    return unicodedata.normalize("NFKC", text).translate(_FOLD).lower()


def canonicalize_codes(text: str, vocabulary: SearchVocabulary) -> str:
    """Rewrite every code variant to its canonical form (step 4).

    Args:
        text: Folded, lower-cased text.
        vocabulary: Compiled code rules.

    Returns:
        Text with codes such as "ss 316" replaced by "ss316".
    """
    for pattern, canonical in vocabulary.code_rules:
        text = pattern.sub(canonical, text)
    return text


def _stem(token: str) -> str:
    """Stem a plain word; leave codes and numbers untouched."""
    return token if _CODE_LIKE.search(token) else str(_stemmer().stemWord(token))


def normalize_for_search(text: str, vocabulary: SearchVocabulary) -> str:
    """Turn raw text into `search_text` (see module docstring for the steps).

    Args:
        text: Verbatim chunk text or a tender query.
        vocabulary: Compiled ontology code rules.

    Returns:
        Space-separated normalized tokens; empty if the text has no Latin words or numbers.
    """
    folded = canonicalize_codes(fold_text(text), vocabulary)
    return " ".join(_stem(token) for token in _TOKEN.findall(folded))
