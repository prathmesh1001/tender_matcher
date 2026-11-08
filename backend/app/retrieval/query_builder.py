"""Turn a tender requirement into a `SearchQuery` (CLAUDE.md section 12: A1, A2, A5, A6).

1. Tokenize with the shared normalizer (`search_tokens`), so query and chunks agree.
2. Correct typos (A2): a word of at least `min_length` letters that is neither an ontology word
   nor a word of the searched chunks is replaced by the one ontology word within
   `max_distance` edits (transpositions count as one). Ties are left alone. Every correction
   is logged. Words the catalogues use ("water") are never "corrected" ("wafer").
3. Expand abbreviations (A5): "nrv" adds "check valve".
4. Expand synonyms: a product type, sub-type or attribute named in the query (or the
   requirement's canonical attribute) adds the other words the ontology lists for it. An
   attribute's synonyms are also added, as written, to the dense and rerank text: a short
   line ("Gasket : Graphite") otherwise scores low against a row labelled "Body-Bonnet Joint".
5. Expand equivalents: a material named in any form ("CI", "cast iron") adds its grade codes
   ("A126", "IS210"), also to the dense and rerank text, since catalogues often give only those.
6. Stem. The requirement's own tokens come first; expansion tokens are added once each.
7. Collect the code terms ("en593", "pn10", and equivalent grade codes like "a536" for SGI):
   a chunk containing one counts as evidence even when the reranker scores it low.

All vocabularies come from the ontology; nothing category-specific lives here.
"""

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from app.ontology.schema import Ontology
from app.retrieval.embedder import embedding_text
from app.retrieval.text_normalizer import SearchVocabulary, is_code, search_tokens, stem_token
from app.schemas.retrieval import SearchQuery, TypoCorrection

logger = logging.getLogger(__name__)

_Phrase = tuple[str, ...]
_NO_MATCH: Final = -1


@dataclass(frozen=True, slots=True)
class SynonymGroup:
    """Stemmed phrases naming one product type, sub-type or attribute."""

    name: str
    phrases: tuple[_Phrase, ...]
    written: tuple[str, ...] = ()  # the phrases as the ontology writes them

    @property
    def terms(self) -> tuple[str, ...]:
        """Every distinct stem of the group, in phrase order."""
        return tuple(dict.fromkeys(t for phrase in self.phrases for t in phrase))


@dataclass(frozen=True, slots=True)
class QueryVocabulary:
    """Ontology words compiled for query building."""

    search: SearchVocabulary
    abbreviations: tuple[tuple[_Phrase, str], ...]  # (unstemmed key tokens, expansion)
    typo_words: tuple[str, ...]  # unstemmed ontology words that typos are corrected to
    typo_stems: frozenset[str]
    groups: tuple[SynonymGroup, ...]
    attributes: dict[str, SynonymGroup]
    equivalents: tuple[tuple[SynonymGroup, tuple[str, ...]], ...]  # (stemmed, terms as written)
    max_distance: int
    min_length: int

    @classmethod
    def from_ontologies(
        cls,
        ontologies: Sequence[Ontology],
        search: SearchVocabulary,
        *,
        max_distance: int,
        min_length: int,
    ) -> QueryVocabulary:
        """Compile abbreviations, typo targets and synonym groups of every ontology.

        Args:
            ontologies: Loaded ontologies.
            search: The shared normalizer vocabulary.
            max_distance: Edit distance for typo correction (0 = off).
            min_length: Shorter words are never corrected.

        Returns:
            The vocabulary.
        """
        # Enum values are typo targets only: expanding "body material" to every grade would
        # boost any chunk naming any material.
        attributes = {
            a.name: _group(a.name, a.synonyms, search) for o in ontologies for a in o.attributes
        }
        groups = [*_product_groups(ontologies, search), *attributes.values()]
        abbreviations = tuple(
            (tuple(search_tokens(key, search)), expansion)
            for o in ontologies
            for key, expansion in o.abbreviations.items()
        )
        phrases = [p for o in ontologies for p in _ontology_phrases(o)]
        words = sorted({w for p in phrases for w in search_tokens(p, search) if w.isalpha()})
        typo_words = tuple(w for w in words if len(w) >= min_length)
        return cls(
            search=search,
            abbreviations=abbreviations,
            typo_words=typo_words,
            typo_stems=frozenset(stem_token(w) for w in words),
            groups=tuple(groups),
            attributes=attributes,
            equivalents=tuple(
                (_group(e.name, e.terms, search), tuple(e.terms))
                for o in ontologies
                for e in o.equivalents
            ),
            max_distance=max_distance,
            min_length=min_length,
        )


def _group(name: str, phrases: Iterable[str], search: SearchVocabulary) -> SynonymGroup:
    """Stem every phrase of a group, dropping phrases that normalize to nothing."""
    phrases = list(phrases)
    stemmed = (tuple(stem_token(t) for t in search_tokens(p, search)) for p in phrases)
    return SynonymGroup(
        name=name,
        phrases=tuple(dict.fromkeys(p for p in stemmed if p)),
        written=tuple(dict.fromkeys(p.strip() for p in phrases if p.strip())),
    )


def _product_groups(ontologies: Sequence[Ontology], search: SearchVocabulary) -> list[SynonymGroup]:
    """One group per product type and per sub-type."""
    groups: list[SynonymGroup] = []
    for ontology in ontologies:
        for product in ontology.product_types:
            groups.append(_group(product.name, product.synonyms, search))
            groups.extend(_group(s.name, s.synonyms, search) for s in product.sub_types)
    return groups


def _ontology_phrases(ontology: Ontology) -> list[str]:
    """Every phrase whose words are typo-correction targets (not the short abbreviation keys)."""
    phrases: list[str] = []
    for product in ontology.product_types:
        phrases.extend(product.synonyms)
        phrases.extend(s for sub in product.sub_types for s in sub.synonyms)
    for attribute in ontology.attributes:
        phrases.extend([*attribute.synonyms, *attribute.values])
    phrases.extend(ontology.abbreviations.values())
    phrases.extend(t for group in ontology.equivalents for t in group.terms)
    return phrases


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (a swap of neighbours costs 1), capped above `limit`.

    Args:
        a: First word.
        b: Second word.
        limit: Distances above this are not needed exactly.

    Returns:
        The distance, or `limit + 1` if it exceeds `limit`.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous: list[int] = []
    current = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        before, previous, current = previous, current, [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], before[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
    return min(current[len(b)], limit + 1)


def correct_token(
    token: str, vocabulary: QueryVocabulary, known_terms: Collection[str]
) -> TypoCorrection | None:
    """Find the single ontology word a misspelt token stands for (A2).

    Args:
        token: Unstemmed query token.
        vocabulary: Query vocabulary.
        known_terms: Stems of the searched chunks; a token whose stem is here is never changed.

    Returns:
        The correction, or None if the token is fine, too short, a code, or ambiguous.
    """
    limit = vocabulary.max_distance
    if limit == 0 or len(token) < vocabulary.min_length or not token.isalpha():
        return None
    stem = stem_token(token)
    if stem in vocabulary.typo_stems or stem in known_terms:
        return None
    best, best_distance = "", limit + 1
    for word in vocabulary.typo_words:
        distance = edit_distance(token, word, limit)
        if distance < best_distance:
            best, best_distance = word, distance
        elif distance == best_distance and distance <= limit:
            best = ""  # two words equally close: ambiguous, keep the original
    if not best or best_distance > limit:
        return None
    return TypoCorrection(original=token, corrected=best, distance=best_distance)


def _find(tokens: Sequence[str], phrase: _Phrase) -> int:
    """Index where `phrase` occurs as consecutive tokens, else `_NO_MATCH`."""
    size = len(phrase)
    for start in range(len(tokens) - size + 1):
        if tuple(tokens[start : start + size]) == phrase:
            return start
    return _NO_MATCH


def _abbreviation_expansions(tokens: Sequence[str], vocabulary: QueryVocabulary) -> list[str]:
    """Expansions of every abbreviation key in the (unstemmed) tokens, in ontology order."""
    return [
        expansion
        for key, expansion in vocabulary.abbreviations
        if key and _find(tokens, key) != _NO_MATCH
    ]


def _named(group: SynonymGroup, stems: Sequence[str]) -> bool:
    """True if any phrase of the group occurs in the stems."""
    return any(_find(stems, p) != _NO_MATCH for p in group.phrases)


def _equivalent_expansions(
    stems: Sequence[str], text: str, vocabulary: QueryVocabulary
) -> tuple[list[str], list[str]]:
    """Stems and written terms of every equivalence group named in the query.

    Args:
        stems: Query stems including abbreviation expansions ("ci" -> "cast iron").
        text: The requirement as written (terms already in it are not repeated).
        vocabulary: Query vocabulary.

    Returns:
        `(stems for BM25, terms for the dense/rerank text)`.
    """
    named = [(g, raw) for g, raw in vocabulary.equivalents if _named(g, stems)]
    written = text.lower()
    return (
        [t for group, _ in named for t in group.terms],
        [t for _, raw in named for t in raw if t.lower() not in written],
    )


def _synonym_terms(
    stems: Sequence[str], vocabulary: QueryVocabulary, attribute: str | None
) -> list[str]:
    """Stems of every synonym group named in the query, plus the given attribute's group."""
    named = [g for g in vocabulary.groups if _named(g, stems)]
    given = vocabulary.attributes.get(attribute) if attribute else None
    if given is not None:
        named.append(given)
    return [t for group in named for t in group.terms]


def _attribute_phrases(
    text: str, stems: Sequence[str], vocabulary: QueryVocabulary, attribute: str | None
) -> list[str]:
    """Synonyms (as written) of the attributes named in the query or given, not in the text."""
    named = [g for g in vocabulary.attributes.values() if _named(g, stems)]
    given = vocabulary.attributes.get(attribute) if attribute else None
    if given is not None and given not in named:
        named.append(given)
    lowered = text.lower()
    return [p for g in named for p in g.written if p.lower() not in lowered]


def build_query(
    text: str,
    vocabulary: QueryVocabulary,
    *,
    known_terms: Collection[str] = frozenset(),
    attribute: str | None = None,
) -> SearchQuery:
    """Build the BM25 terms and dense text for one requirement.

    Args:
        text: Requirement text as written in the tender.
        vocabulary: Query vocabulary.
        known_terms: Stems of the chunks being searched (protects their words from correction).
        attribute: The requirement's canonical attribute, if known.

    Returns:
        The query.
    """
    tokens = search_tokens(text, vocabulary.search)
    corrections = _correct_typos(tokens, vocabulary, known_terms)
    abbreviations = _abbreviation_expansions(tokens, vocabulary)
    terms = [stem_token(t) for t in tokens]
    expansion = [
        stem_token(t) for phrase in abbreviations for t in search_tokens(phrase, vocabulary.search)
    ]
    expansion += _synonym_terms([*terms, *expansion], vocabulary, attribute)
    equivalent_stems, equivalent_terms = _equivalent_expansions(
        [*terms, *expansion], text, vocabulary
    )
    expansion += equivalent_stems
    extra = tuple(dict.fromkeys(t for t in expansion if t not in terms))
    synonyms = _attribute_phrases(text, [*terms, *expansion], vocabulary, attribute)
    dense_extra = list(
        dict.fromkeys(
            [c.corrected for c in corrections] + abbreviations + equivalent_terms + synonyms
        )
    )
    dense_text = " ".join([embedding_text(text), *dense_extra]).strip()
    return SearchQuery(
        raw_text=text,
        terms=tuple(terms),
        expansion_terms=extra,
        dense_text=dense_text,
        rerank_text=rerank_text(dense_text, attribute),
        code_terms=tuple(dict.fromkeys(t for t in [*terms, *equivalent_stems] if is_code(t))),
        corrections=tuple(corrections),
    )


def _correct_typos(
    tokens: list[str], vocabulary: QueryVocabulary, known_terms: Collection[str]
) -> list[TypoCorrection]:
    """Replace misspelt tokens in place (A2) and log every correction.

    Args:
        tokens: Unstemmed query tokens; corrected entries are overwritten.
        vocabulary: Query vocabulary.
        known_terms: Stems of the searched chunks (never corrected).

    Returns:
        The corrections made, in token order.
    """
    corrections: list[TypoCorrection] = []
    for i, token in enumerate(tokens):
        fix = correct_token(token, vocabulary, known_terms)
        if fix is not None:
            corrections.append(fix)
            tokens[i] = fix.corrected
            logger.info(
                "query typo corrected",
                extra={"original": fix.original, "corrected": fix.corrected},
            )
    return corrections


def rerank_text(dense_text: str, attribute: str | None) -> str:
    """Lead the query with the attribute's name for the cross-encoder.

    Measured on tender GEM/2026/B/8157057 against Delval and L&T (13 labelled pairs), correct
    chunk ranked first with / without the name: bge-reranker-v2-m3 6/13 vs 5/13, MiniLM 10/13
    vs 8/13.

    Args:
        dense_text: The query's dense text.
        attribute: Canonical attribute name ("body_material"), if known.

    Returns:
        "body material: <dense_text>", or `dense_text` unchanged without an attribute.
    """
    return f"{attribute.replace('_', ' ')}: {dense_text}" if attribute else dense_text
