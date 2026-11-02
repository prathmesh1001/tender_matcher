# Tender Matcher: write-up

This describes what the system does, how it decides, how well it works today and where it falls
short. Setup is in the [README](../README.md).

## The problem

A GeM tender lists technical requirements, often in attachments rather than in `bid.pdf`. A buyer
or bidder has to check each one against manufacturer catalogues, and a claim is only useful if it
can be traced to a page. So for each requirement and manufacturer the system reports a status, a
score and the evidence: document, page and verbatim quote. It also gives each manufacturer a
compatibility %, a coverage % and a rank.

The main rule is **never guess**. Missing or unverifiable evidence gives `unsupported`, not a match.
A tool that says "not decided" costs a reader some time. A tool that invents a match can lose a bid.

## How it works

```
Tender PDF + linked files                     Manufacturer PDFs + sources.yaml
        |                                                |
 line items, atomic requirements             pages, table rows, prose chunks, product tags
        |                                                |
        +-------------------> one item <-----------------+
                                 |
             choose ONE catalogue per manufacturer (rules)
                                 |
             rules: numeric and code values, like for like
                                 |
             judge: free text only (retrieval -> Gemini)
                                 |
             verify every quote on its page, then score and rank
                                 |
                  JSON, Excel and report screen
```

**Tender side.** Items come from a CSV, a specification schedule or the bid's item category, in
that order. Linked specification, BOQ and ATC files are followed. Compound lines are split, so
"SS316 body & disc, PN16, flanged" becomes separate requirements. Every line is kept, including
commercial and legal ones, and each is routed by where its evidence would come from.

**Catalogue side.** Each document is registered by hand in `sources.yaml` with its URL and sha256.
Pages are parsed one by one, with OCR for scanned pages. Each table row becomes a chunk with its
header and product repeated. Each chunk keeps `raw_text`, used for quotes and never changed, and
`search_text`, normalized for search only. The current set is 12 documents and 1,794 chunks from 4
manufacturers.

**Choosing a catalogue.** Specs are never combined across documents, because size from one model
and material from another describes no real product. Each catalogue declaring the product type is
evaluated alone, then ranked by sub-type fit, whether it decides anything, critical requirements
met, fewest critical requirements contradicted, coverage and fit. If nothing separates the best
ones, none is picked: the tie is shown and a person decides.

**Rules decide first.** Numeric and code requirements go to a deterministic comparator that knows
units and direction (kg/cm² vs bar, at least vs at most). A requirement is compared only with the
vendor value for the same attribute, the same component and the same condition. Tender "Disc MOC"
is never checked against catalogue "Body", and a working pressure is never checked against a test
pressure. With no like-for-like value the result is `unsupported`, even if the same number appears
elsewhere on the page.

**The LLM judge handles the rest.** Only free-text and unlabelled lines reach Gemini
(`gemini-3.8-flash`). It runs after the catalogue is chosen, so it cannot change that choice or any
rule result. For each line:

1. Hybrid search (BM25 and embeddings, fused with RRF, then a cross-encoder) runs inside the chosen
   catalogue only. With no evidence hit, the LLM is not called.
2. The model answers in JSON and may cite only the passages it was given.
3. Its answer is not trusted. Document and page come from the cited passage, the quote must name
   the attribute and must be found on the page. Anything failing stays `unsupported`.
4. Every LLM decision is marked `needs_review`.

If the API fails or a run's call limit is reached, the remaining lines stay `unsupported` and the
run finishes.

## Status definitions

| Status | Meaning | Score |
|---|---|---|
| `full_match` | Evidence satisfies all conditions | 1.0 |
| `partial_match` | Some conditions met, only some models, optional accessory, or near miss | 0.5 |
| `missing_capability` | The catalogue covers the attribute and the value fails | 0.0 |
| `unsupported` | Not technically verifiable, or no evidence in the catalogues | left out |
| `not_applicable` | Nothing to compare (buyer details, bid dates, procedure) | left out |

`unsupported` means "we could not tell", never "the manufacturer fails". Turnover, experience and
certificates are `unsupported` because no vendor documents are ingested.

## Scoring

- **Compatibility %** = total score ÷ decided requirements × 100. Every requirement counts the same.
- **Coverage %** = decided requirements ÷ all requirements except `not_applicable` × 100.

The two must be read together: 100% compatibility on 10% coverage says little. Requirements outside
the item schedule are listed separately and never enter the score.

## Confidence

Each decided result also gets a confidence from 0 to 1, shown with what lowered it. It starts at 1.0
and each weakness the facts show multiplies it down:

| Weakness | Factor |
|---|---|
| Decided by the LLM | 0.7 |
| All evidence from brochures | 0.85 |
| A cited page was OCR'd | 0.8 |
| The judge's passage was found only by a code | 0.8 |
| A rule flag: near miss, misread, unclear product heading, tied catalogues | 0.8 |

Below 0.75 the result goes to review, so every LLM decision does, and so does any result with two
weaknesses. Confidence never changes a status or a score; it says what to check first.
`unsupported` and `not_applicable` decide nothing and get none. Conflicting documents should lower
it too, but conflicts are not detected yet. The factors are settings in `config.py`.

## Decisions

**Ranking.** Manufacturers are ranked by compatibility, with a minimum evidence coverage
(`RANKING_MIN_COVERAGE_PCT`, starting at 50 %). A manufacturer below the minimum is listed after
the others and clearly marked "low coverage", so a high score on little evidence cannot lead the
ranking. Coverage is always shown. If every manufacturer is below the minimum, all are marked and
the order within that group is still by compatibility.

**Options written with "/".** A tender value such as "CA15/CF8M" does not say whether one option
is enough. If the catalogue shows only some of the options, the result is `partial_match` with
`needs_review`. If the tender writes "or" between options ("NITRILE OR SS"), one option found is a
`full_match`. Qualifiers such as "or equivalent" and "or higher" are not options.

**Named makes.** A make named by the tender is recorded and shown, and never fails another make. A
tender saying only one firm is acceptable gets a warning at the top of the report.

**Unreliable tenders.** Tenders whose items cannot be trusted are flagged, every result goes to
review, and the LLM is not called for them.

**Judge off by default.** `LLM_JUDGE_ENABLED` defaults to `false`, and each tender has a call limit,
because the API costs quota.

**Files on disk.** The plan called for files stored in Postgres. They stay in a mounted `data/`
folder, because the catalogue registry and tender manifests read from disk and moving them is a
larger change than adding a store.

## Results

### One tender, rules against rules + judge

Tender GEM/2026/B/8119613, two triple offset butterfly valve items, rules plus 5 Gemini calls. No
call failed, all 8 LLM decisions verified on their pages, and no rule result changed.

| Manufacturer | Compatibility | Coverage |
|---|---|---|
| LnT_Valves | 71.4% → 80.0% | 38.9% → 55.6% |
| Delval | 50.0% → 41.7% | 27.8% → 33.3% |
| KSB, Kirloskar Brothers | none | 0%, no catalogue for this product |

Delval fell because the judge cited its catalogue title, "Double Offset High Performance Butterfly
Valve", against a tender asking for triple offset.

### Against hand labels, rules only

87 rows on 4 tenders were checked by hand ([manual check](manual_match_check.md)) and compared with
the current code ([example diff](gold_diff_8119613.md)).

| Run | Agree | Not decided | Weaker than label | Stronger than label | Other catalogue |
|---|---|---|---|---|---|
| With ontology | 35 | 40 | 10 | 2 | 0 |
| Without ontology | 13 | 42 | 5 | 3 | 24 |

How to read this:

- **Not decided is the largest group.** The system often stays silent where a person found an
  answer, which fits "never guess" but limits coverage.
- **Stronger is the dangerous direction**, because it claims more than a person would. There are 2
  rows: on 8017367, size DN300 is `partial_match` where the label is `missing_capability`, and size
  DN100 is `full_match` where the label is `unsupported` (this row belongs to a tie, so it is
  already sent to review).
- **Weaker rows are mostly cautious.** For example, "API 609 5TH EDITION" is a near miss because
  the catalogue says API 609 without the edition.
- **The ontology matters.** Without it, the system picks a different catalogue on 24 rows.
- **Labels cover some manufacturers and items only**, so these figures describe those rows, not
  every tender.

### Evaluation, rules against rules + judge

`eval/run_eval.py` scores both runs on 99 labelled rows: the 87 above plus 12 on pump tender
8169451. Full tables, with precision and recall per status and confusion matrices, are in
[eval/results.md](../eval/results.md).

| Run | Coverage | Decided accuracy | Stronger than label | Citation on a labelled page |
|---|---|---|---|---|
| Rules | 43% (43/99) | 72% (31/43) | 2 | 71% (22/31) |
| Rules + judge | 49% (49/99) | 73% (36/49) | 3 | 67% (24/36) |

- **The judge adds coverage without losing accuracy.** It decided 6 more rows: 5 agree with the
  label. The sixth, "Flanged Ended" on 7956201, is `full_match` where the label says
  `partial_match`, but that label covers a whole manual row ("flanged ends + tongue & groove
  companion flanges"), and the catalogue does show flanged ends. The label is coarser than the
  requirement, so this is not clearly a judge error.
- **`full_match` is precise, `partial_match` is not.** A `full_match` agrees with the label 94% of
  the time with the judge; a `partial_match` only 23%. Most wrong partials are labelled
  `full_match`: the system is cautious.
- **Recall is the weak side.** Of 62 rows labelled `full_match`, the system finds 29 and leaves 24
  `unsupported`.
- **Confidence does separate right from wrong.** With the judge, 23% of decisions at full
  confidence are wrong against 36% below it (rules only: 23% against 50%). The samples are small:
  35 and 14 decisions.
- **Pump tender: half its specification never reached the matcher.** Speed, size, seal, motor,
  spares and base plate were lost in extraction from the scanned page. Of the 12 labelled rows,
  9 agree and 3 are not decided; nothing is wrong, but the rows that would test pump duty were
  never extracted. Its labels were drafted by Claude and checked by hand.

### All 19 tenders

All 19 run without a crash. By a read-through of the reports, not a measurement, 7 look right, 6
partly right and 6 wrong. The extraction warning flags those 6 plus one more:

| Tender | Warning reasons | Items found |
|---|---|---|
| 7951144 | titles are not products, oversized item | 1 |
| 8026686 | titles are not products | 3 |
| 8063368 | titles are not products | 62 |
| 8123915 | merged item category | 1 |
| 8132165 | titles are not products | 2 |
| 8167974 | merged item category | 1 |
| 8175587 | oversized item, merged item category | 1 |

The flag warns but does not fix anything. Two tenders with garbled scanned lines (8011173 and
8169451) are not flagged, so a clean report does not prove clean extraction.

## Known limits

**Accuracy**

- Retrieval rejects many short lines before the judge sees them. "ZERO LEAKAGE" and "HAND GEAR
  OPERATED" both have labels saying L&T complies, and both stay `unsupported`. The reranker scores
  exact spec lines low, and the threshold has not been tuned.
- Evidence can come from another model in the same PDF. Catalogues with several product headings
  are read by heading, and unclear cases are capped at `partial_match`, but this is not solved.
- Wrong item rows on 8026686 and 8132165 are flagged, not fixed.
- Tied catalogues are not judged.

**Not built yet**

- Confidence has no factor for conflicting documents, and none for the source priority (datasheet
  over catalogue), because conflicts are not detected.
- Spec fact extraction exists but has never run on real documents, so `spec_facts` is empty. The
  comparator reads labelled fields from chunks instead of the structured lookup the design planned.
- Duncan and Intervalve Poonawala catalogues. The current manufacturers are Delval, KSB, Kirloskar
  Brothers and L&T.

**Operations**

- Jobs run in a queue inside the single API process, so a restart fails any run in progress. RQ and
  Redis from the plan are not added.
- The Docker image is CPU only: 20 chunks took about 160 seconds to embed, so a new database should
  be embedded on a GPU host. A run with the judge took about 7 minutes on CPU.
- PDFs are not in git or the images, so a fresh clone must supply them.
- Gemini quota is unknown. A full pass over the 13 reliable tenders has 184 distinct lines to judge,
  checked per manufacturer catalogue.

## What to do next

1. Raise recall: 24 rows labelled `full_match` stay `unsupported`. Tune retrieval on short spec
   lines and rerun `eval/run_eval.py` to see whether it helps.
2. Fix extraction on scanned specification tables (the pump tender lost half its rows) and on the
   flagged tenders, starting with item boundaries.
3. Label more tenders, especially pumps, so the figures rest on more than 99 rows.
4. Add the remaining manufacturers.
