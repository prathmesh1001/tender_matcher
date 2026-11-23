"""Best GeM categories for an item when no category names it exactly, by shared words.

GeM names a category its own way: the item "SS hex head bolt" is sold in "Hexagonal Head Bolts
(V2) ISI marked to IS 1363 (Part 1)", "SS spring washer" in "Stainless Steel Spring Lock Washer -
BHEL". The exact rule (`gem_search.fitting_categories`) finds neither, so this one scores every
category a GeM search offered:

1. Both sides are written the same way: folded, cut into parts at brackets, " - ", commas and
   cut words ("as per", "conforming"), each part normalized like every search text
   (`normalize_for_search`), abbreviations replaced by their full words ("hex" -> "hexagonal",
   "ss" -> "stainless steel"), filler and code words left out.
2. A category part fits an item phrase (the title's first part, "ss hex head bolt") when
   - its last word, the product it names, is the phrase's last word ("Writing Pad Chair" is a
     chair, never a "brake pad"; "PHPA for Water Shut off" is no "coolant shut off valve"),
   - they share at least two words, in the same order ("Glass Doors" is no "door glass"),
   - coverage: common words / the shorter of (phrase, part) >= `min_coverage`
     ("Timing Belt" shares only "belt" with "fan belt"), and
   - precision: part words that are item title words / part words >= `min_precision`.
3. Categories are ranked by coverage + precision of their best part, best first.

Only items whose phrase has at least two words use it: one word alone ("injector") fits any
category ending in it, which the exact rule's qualifier check already judges.

Pure functions; every word list comes from config.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.discovery.gem_parser import CategoryLink
from app.retrieval.text_normalizer import SearchVocabulary, fold_text, normalize_for_search

_SEPARATORS: Final = r"[()\[\],&/:;|]|\s-\s|\s-|-\s"
_MIN_PHRASE_WORDS: Final = 2


@dataclass(frozen=True, slots=True)
class FitWords:
    """How item titles and category names are turned into comparable words."""

    vocabulary: SearchVocabulary
    equivalents: Mapping[str, tuple[str, ...]]  # normalized word -> its normalized full words
    cut: re.Pattern[str]  # where a folded name is cut into parts
    filler: frozenset[str]  # normalized words never counted

    @classmethod
    def build(
        cls,
        vocabulary: SearchVocabulary,
        equivalents: Mapping[str, str],
        cut_words: Iterable[str],
        filler_words: Iterable[str],
    ) -> FitWords:
        """Compile the word lists.

        Args:
            vocabulary: Ontology code rules (shared normalizer).
            equivalents: One-word abbreviation -> full words (config and ontology).
            cut_words: Words a name is cut before ("as per").
            filler_words: Words never counted ("set", "of").

        Returns:
            The compiled lists.
        """
        expand: dict[str, tuple[str, ...]] = {}
        for short, full in equivalents.items():
            key = normalize_for_search(short, vocabulary).split()
            if len(key) == 1:
                expand[key[0]] = tuple(normalize_for_search(full, vocabulary).split())
        words = sorted({" ".join(fold_text(w).split()) for w in cut_words if w.strip()}, key=len)
        phrases = "|".join(re.escape(w).replace(r"\ ", r"\s+") for w in reversed(words))
        cut = re.compile(rf"{_SEPARATORS}|\b(?:{phrases})\b" if phrases else _SEPARATORS)
        filler = frozenset(
            w for f in filler_words for w in normalize_for_search(f, vocabulary).split()
        )
        return cls(vocabulary, expand, cut, filler)

    def parts(self, text: str) -> list[list[str]]:
        """The text's parts as comparable words, empty parts left out."""
        out: list[list[str]] = []
        for piece in self.cut.split(fold_text(text)):
            words = self._words(piece)
            if words:
                out.append(words)
        return out

    def _words(self, piece: str) -> list[str]:
        """One part's normalized words: abbreviations expanded, filler and codes left out."""
        out: list[str] = []
        for word in normalize_for_search(piece, self.vocabulary).split():
            for w in self.equivalents.get(word, (word,)):
                if w not in self.filler and len(w) > 1 and not any(c.isdigit() for c in w):
                    out.append(w)
        return out


@dataclass(frozen=True, slots=True)
class ItemWords:
    """An item's words for the fit: its product phrases and every title word."""

    phrases: tuple[tuple[str, ...], ...]  # each title's first part ("ss hex head bolt")
    title: frozenset[str]  # every word of every title

    @classmethod
    def from_titles(cls, titles: Sequence[str], fit: FitWords) -> ItemWords:
        """The words of the titles of one item type.

        Args:
            titles: Titles of the tender items of the type.
            fit: The word rules.

        Returns:
            Their phrases (first part of each title, each once) and all title words.
        """
        parts = [fit.parts(t) for t in titles]
        phrases = tuple(dict.fromkeys(tuple(p[0]) for p in parts if p))
        title = frozenset(w for p in parts for part in p for w in part)
        return cls(phrases, title)

    @property
    def longest(self) -> int:
        """Word count of the longest phrase."""
        return max((len(p) for p in self.phrases), default=0)


@dataclass(frozen=True, slots=True)
class FitScore:
    """How well one category fits."""

    link: CategoryLink
    coverage: float
    precision: float
    name_share: float = 0.0  # words of the whole name that are title words / its words

    @property
    def total(self) -> float:
        """Coverage plus precision plus the whole name's share, for ranking.

        The name share puts "Stainless Steel Spring Lock Washer" before a hardware kit category
        whose list merely holds "Spring Washer".
        """
        return self.coverage + self.precision + self.name_share


def _in_order(part: Sequence[str], phrase: Sequence[str], common: set[str]) -> bool:
    """True if the common words come in the same order in the part and the phrase."""
    in_part = [w for w in dict.fromkeys(part) if w in common]
    in_phrase = [w for w in dict.fromkeys(phrase) if w in common]
    return in_part == in_phrase


def phrase_fit(
    part: Sequence[str], phrase: Sequence[str], title: frozenset[str]
) -> tuple[float, float] | None:
    """(coverage, precision) of one category part for one item phrase, or None if it does not fit.

    Args:
        part: The category part's comparable words.
        phrase: The item phrase's comparable words.
        title: Every item title word.

    Returns:
        The two shares; None when the part names another product (last words differ), shares
        fewer than two words, or shares them in another order.
    """
    if not part or not phrase or part[-1] != phrase[-1]:
        return None
    common = set(part) & set(phrase)
    if len(common) < _MIN_PHRASE_WORDS or not _in_order(part, phrase, common):
        return None
    coverage = len(common) / min(len(set(part)), len(set(phrase)))
    precision = len(set(part) & title) / len(set(part))
    return coverage, precision


def part_fit(part: Sequence[str], item: ItemWords) -> tuple[float, float] | None:
    """The best (coverage, precision) of one category part over the item's phrases.

    Args:
        part: The part's comparable words.
        item: The item's words.

    Returns:
        The best shares by their sum, or None if no phrase fits.
    """
    fits = [f for p in item.phrases if (f := phrase_fit(part, p, item.title))]
    return max(fits, key=sum) if fits else None


def score_category(link: CategoryLink, item: ItemWords, fit: FitWords) -> FitScore | None:
    """The category's best part as a score, or None if no part names the item's product.

    Args:
        link: A category a GeM search offered.
        item: The item's words.
        fit: The word rules.

    Returns:
        The score of its best part.
    """
    best: FitScore | None = None
    parts = fit.parts(link.name)
    words = {w for p in parts for w in p}
    name_share = len(words & item.title) / len(words) if words else 0.0
    for part in parts:
        shares = part_fit(part, item)
        if shares is None:
            continue
        score = FitScore(link, *shares, name_share=name_share)
        if best is None or score.total > best.total:
            best = score
    return best


def best_categories(
    links: Iterable[CategoryLink],
    titles: Sequence[str],
    fit: FitWords,
    min_coverage: float,
    min_precision: float,
) -> list[CategoryLink]:
    """The categories fitting the items by shared words, best first, each once.

    Args:
        links: Categories offered by the GeM searches of the type.
        titles: Titles of the tender items of the type.
        fit: The word rules.
        min_coverage: Fewest common words / shorter word list.
        min_precision: Fewest category part words that are title words / part words.

    Returns:
        The fitting categories; empty when the item phrase has fewer than two words.
    """
    item = ItemWords.from_titles(titles, fit)
    if item.longest < _MIN_PHRASE_WORDS:
        return []
    scores: dict[str, FitScore] = {}
    for link in links:
        score = score_category(link, item, fit)
        if score is None or score.coverage < min_coverage or score.precision < min_precision:
            continue
        if link.slug not in scores or score.total > scores[link.slug].total:
            scores[link.slug] = score
    ranked = sorted(scores.values(), key=lambda s: (-s.total, s.link.name))
    return [s.link for s in ranked]
