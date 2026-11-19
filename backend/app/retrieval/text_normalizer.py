"""`normalize_for_search()`: the one text cleaner shared by the indexer and the query builder.

Catalogue chunks and tender queries go through the same function, so both sides always agree
(CLAUDE.md section 12, cases A1, A3, A4, A6, A8). Its output is `search_text`, used only for
BM25 and embeddings; quotes and evidence always come from the verbatim `raw_text`.

Steps, in order:
1. NFKC: ligatures ("Butterﬂy" -> "Butterfly"), superscripts ("m³" -> "m3"), NBSP -> space.
2. Fold curly quotes, primes and dashes to ASCII.
3. Lower-case.
4. Rewrite codes to one canonical form with the ontology's rules ("SS 316" -> "ss316").
5. Drop words containing Devanagari or U+FFFD (Hindi, often garbled in GeM PDFs), then
   tokenize on anything that is not a letter, digit or "_" (hyphens split compounds; decimals
   like "7.5" stay whole). A number glued to an ontology unit is split ("450MM" -> "450 mm"),
   so it matches catalogues that write "450 mm" or a "Valve Size" column holding "450".
6. Stem plain words with Snowball ("valves" -> "valv", "balls" -> "ball"); tokens with digits
   or "_" are codes and are never stemmed.
"""

import re
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
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
_NUMBER_UNIT: Final = re.compile(r"([0-9]+(?:\.[0-9]+)?)([a-z]+)")
# A word touching Devanagari or U+FFFD: GeM bid PDFs extract their Hindi as broken glyphs
# ("सूZम"), so the whole word is dropped rather than leaking stray Latin letters (A8).
_NON_LATIN_WORD: Final = re.compile(r"[^\s/|:;,()\[\]]*[\u0900-\u097F\ufffd][^\s/|:;,()\[\]]*")


@dataclass(frozen=True, slots=True)
class SearchVocabulary:
    """Compiled code rules (in ontology order) and unit spellings used by `normalize_for_search`."""

    code_rules: tuple[tuple[re.Pattern[str], str], ...]
    units: frozenset[str] = frozenset()
    # Every folded unit spelling, including "m3/h" and "°c" (table cells that are only a unit).
    unit_spellings: frozenset[str] = frozenset()

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
        # Only all-letter spellings can be glued to a number ("mm", "bar"; not "m3/h").
        spellings = frozenset(fold_text(u).strip() for o in ontologies for u in o.units)
        units = frozenset(u for u in spellings if u.isalpha())
        return cls(code_rules=rules, units=units, unit_spellings=spellings)


_LOCAL: Final = threading.local()


def _stemmer() -> snowballstemmer.stemmer:
    """This thread's English Snowball stemmer.

    A stemmer keeps the word being stemmed in its own fields, so one shared instance breaks
    when two threads stem at once (the catalogue warm-up next to a run: IndexError inside
    snowballstemmer). Each thread builds its own, once.
    """
    stemmer: snowballstemmer.stemmer | None = getattr(_LOCAL, "stemmer", None)
    if stemmer is None:
        stemmer = snowballstemmer.stemmer("english")
        _LOCAL.stemmer = stemmer
    return stemmer


def fold_unicode(text: str) -> str:
    """Apply steps 1-2: NFKC and quote/dash folding, keeping case and every script.

    Shared with the embedder, whose input must keep Hindi and casing.

    Args:
        text: Any text.

    Returns:
        Folded text.
    """
    return unicodedata.normalize("NFKC", text).translate(_FOLD)


def fold_text(text: str) -> str:
    """Apply steps 1-3: NFKC, quote/dash folding, lower-casing.

    Args:
        text: Any text.

    Returns:
        Folded, lower-cased text.
    """
    return fold_unicode(text).lower()


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


def stem_token(token: str) -> str:
    """Stem a plain word (step 6); leave codes and numbers untouched.

    Args:
        token: One token from `search_tokens`.

    Returns:
        The stem, or the token itself if it contains a digit or "_".
    """
    return token if _CODE_LIKE.search(token) else str(_stemmer().stemWord(token))


def search_tokens(text: str, vocabulary: SearchVocabulary) -> list[str]:
    """Apply steps 1-5: the tokens of `search_text` before stemming.

    The query builder corrects typos on these (a typo is easier to see before stemming).

    Args:
        text: Verbatim chunk text or a tender query.
        vocabulary: Compiled ontology code rules.

    Returns:
        Folded, lower-cased, code-canonical tokens in text order.
    """
    folded = canonicalize_codes(_NON_LATIN_WORD.sub(" ", fold_text(text)), vocabulary)
    return [part for token in _TOKEN.findall(folded) for part in _split_unit(token, vocabulary)]


def _split_unit(token: str, vocabulary: SearchVocabulary) -> tuple[str, ...]:
    """Split "450mm" into ("450", "mm") when the letters are an ontology unit; else keep it."""
    match = _NUMBER_UNIT.fullmatch(token)
    if match and match.group(2) in vocabulary.units:
        return match.group(1), match.group(2)
    return (token,)


def is_code(token: str) -> bool:
    """True for code tokens ("en593", "pn10", "a216_wcb"); False for words and bare numbers.

    Args:
        token: One normalized token.

    Returns:
        Whether the token mixes letters with digits or "_".
    """
    return bool(_CODE_LIKE.search(token)) and any(ch.isalpha() for ch in token)


def normalize_for_search(text: str, vocabulary: SearchVocabulary) -> str:
    """Turn raw text into `search_text` (see module docstring for the steps).

    Args:
        text: Verbatim chunk text or a tender query.
        vocabulary: Compiled ontology code rules.

    Returns:
        Space-separated normalized tokens; empty if the text has no Latin words or numbers.
    """
    return " ".join(stem_token(token) for token in search_tokens(text, vocabulary))


PhrasePatterns = tuple[re.Pattern[str], ...]


def phrase_patterns(phrases: Sequence[str], vocabulary: SearchVocabulary) -> PhrasePatterns:
    """Compile phrases as whole-phrase patterns over normalized (`search_text`) form.

    Phrases go through `normalize_for_search` too, so "Ball Valves" matches "ball valve" and
    "SS 316" matches "ss316"; a phrase only matches on token boundaries.

    Args:
        phrases: Plain phrases (synonyms, heading keywords).
        vocabulary: Compiled ontology code rules.

    Returns:
        One pattern per distinct non-empty normalized phrase.
    """
    normalized = {normalize_for_search(p, vocabulary) for p in phrases}
    return tuple(re.compile(rf"(?<!\S){re.escape(n)}(?!\S)") for n in sorted(normalized) if n)
