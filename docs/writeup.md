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

**Options written with "/" or "or"** (decision of 2026-11-02, replacing that of 2026-10-31). In
tenders, "/" means either option is acceptable, the same as "or": "Bolting: B7/2H" and "Shaft: SS
410/17-4PH" are met by one of the two. If the catalogue shows at least one option, the result is
`full_match`. Qualifiers such as "or equivalent" and "or higher" are not options. The other
partial-match rules are unchanged: a value offered only in the options list while the table shows
another, an unclear product heading, and near misses on sizes and codes.

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
the current code, rules only ([example diff](../eval/diffs/GEM_2026_B_8119613.md)).

| Run | Agree | Not decided | Weaker than label | Stronger than label | Other catalogue |
|---|---|---|---|---|---|
| With ontology | 36 | 40 | 8 | 3 | 0 |
| Without ontology | 18 | 58 | 4 | 4 | 3 |

How to read this:

- **Not decided is the largest group.** The system often stays silent where a person found an
  answer, which fits "never guess" but limits coverage.
- **Stronger is the dangerous direction**, because it claims more than a person would. There are 3
  rows: on 8017367, size DN300 is `partial_match` where the label is `missing_capability`, and size
  DN100 is `full_match` where the label is `unsupported` (this row belongs to a tie, so it is
  already sent to review); on 8119613, "DISC MOC : ASTM A217 Gr. CA15/CF8M" is `full_match` under
  the "/" rule where its older label says `partial_match`.
- **Weaker rows are mostly cautious.** For example, "API 609 5TH EDITION" is a near miss because
  the catalogue says API 609 without the edition.
- **The ontology matters.** Without it, the system agrees with half as many labels (18 against 36)
  and leaves 58 rows undecided. On 7956201 it cannot tell the API 600 and API 602 catalogues
  apart, so it picks neither and sends both to review; before the "/" rule it picked API 602, and
  all 21 labelled rows counted as another catalogue.
- **Labels cover some manufacturers and items only**, so these figures describe those rows, not
  every tender.

### Evaluation, rules against rules + judge

`eval/run_eval.py` scores both runs on 99 labelled rows: the 87 above plus 12 on pump tender
8169451. Full tables, with precision and recall per status and confusion matrices, are in
[eval/results.md](../eval/results.md).

Before and after the last round of changes (recall attempts, all reverted, and the pump spec-row
fix, kept):

| Measure | Rules before | Rules after | Rules + judge before | Rules + judge after |
|---|---|---|---|---|
| Coverage | 43% (43/99) | 43% (43/99) | 49% (49/99) | 51% (50/99) |
| Decided accuracy | 74% (32/43) | 74% (32/43) | 76% (37/49) | 74% (37/50) |
| `full_match` precision | 93% (27/29) | 93% (27/29) | 91% (31/34) | 91% (31/34) |
| `partial_match` precision | 33% (4/12) | 33% (4/12) | 38% (5/13) | 36% (5/14) |
| Stronger than label | 3 | 3 | 4 | 4 |
| Citation on a labelled page | 72% (23/32) | 72% (23/32) | 68% (25/37) | 68% (25/37) |
| Flagged tenders | 10 of 19 | 9 of 19 | 10 of 19 | 9 of 19 |

The rules columns are identical: the kept change touched no labelled row. The judge columns run the
same code on every labelled row, so their difference is run-to-run noise of the judge: "Trim : 12
-> 16" was `unsupported` in the first run and `partial_match` (label `full_match`) in the second.

These include the "/" rule of 2026-11-02. It changed 3 labelled rows: "Bolting : B7/2H"
(7956201) and "SHAFT MOC : A479 Type SS 410/17-4PH SS" (8119613) now agree with their
`full_match` labels; "DISC MOC : ASTM A217 Gr. CA15/CF8M" (8119613) is now `full_match` where the
label says `partial_match`, so it counts as stronger. That label predates the rule.

- **The judge adds coverage without losing accuracy.** It decided 6 more rows: 5 agree with the
  label. The sixth, "Flanged Ended" on 7956201, is `full_match` where the label says
  `partial_match`, but that label covers a whole manual row ("flanged ends + tongue & groove
  companion flanges"), and the catalogue does show flanged ends. The label is coarser than the
  requirement, so this is not clearly a judge error.
- **`full_match` is precise, `partial_match` is not.** With the judge, a `full_match` agrees with
  the label 91% of the time (31 of 34); a `partial_match` 36% (5 of 14). Most wrong partials are
  labelled `full_match`: the system is cautious.
- **Recall is the weak side.** Of 64 rows labelled `full_match`, the system finds 31 and leaves 25
  `unsupported`.
- **Confidence does separate right from wrong.** With the judge, 19% of decisions at full
  confidence are wrong against 43% below it (rules only: 19% against 57%). The samples are small:
  36 and 14 decisions.
- **Pump tender: half its specification never reached the matcher.** Speed, size, seal, motor,
  spares and base plate were lost in extraction from the scanned page. Of the 12 labelled rows,
  9 agree and 3 are not decided; nothing is wrong, but the rows that would test pump duty were
  never extracted. Its labels were drafted by Claude and checked by hand.

### All 19 tenders

All 19 run without a crash. By a read-through of the reports, not a measurement, 7 look right, 6
partly right and 6 wrong. The extraction warning flags those 6 and 3 more, 9 of 19:

| Tender | Warning reasons | Items found |
|---|---|---|
| 7865644 | specification outside the items | 34 |
| 7951144 | titles are not products, oversized item | 1 |
| 7959356 | specification outside the items | 1 |
| 8026686 | titles are not products | 3 |
| 8063368 | titles are not products | 62 |
| 8123915 | merged item category | 1 |
| 8132165 | titles are not products | 2 |
| 8167974 | merged item category | 1 |
| 8175587 | oversized item, merged item category | 1 |

"Specification outside the items" is new: every item holds at most 2 requirements
(`EXTRACTION_NEARLY_EMPTY_ITEM`) while at least 10 catalogue-checkable lines are general
requirements (`EXTRACTION_MIN_SPEC_OUTSIDE_ITEMS`). It flagged the two pump tenders it was made for
(7959356: 1 requirement on its item, 73 such general lines; 8071357: 1 and 29), and also 7865644,
a thermocouple tender whose 34 items each hold only a part code while 76 such lines are general.

**Pump fix.** A tender with a single item left nearly empty now gets the rows of its GeM category
specification table ("Specification Name: Head; Bid Requirement (Allowed Values): 14.0 (meter)")
as that item's requirements, read as "Head : 14.0 (meter)" (labels in `TENDER_SPEC_ROW_LABELS`).
8071357 went from 1 requirement to 15 (width, length, height, material, capacity, power, head,
insulation, phase, voltage, speed, frequency, weight, warranty) and is no longer flagged; 8054155
(a pesticide, outside the catalogues) also gained its 11 rows. No labelled row changed.

The flag warns but does not fix anything. Two tenders with garbled scanned lines (8011173 and
8169451) are not flagged, so a clean report does not prove clean extraction.

## Known limits

**Accuracy**

- Recall: 25 rows labelled `full_match` stay `unsupported` with the judge. By cause, of the 37
  labelled decisions left undecided: 15 have no catalogue field naming the attribute (codes stated
  in prose, such as "Testing Standard : API 598"), 8 get no search hit above `RERANK_MIN_SCORE`
  ("ZERO LEAKAGE", "HAND GEAR OPERATED", "Spring"), 5 are correct judge answers whose quote fails
  the page check because table chunks are rebuilt as "Header: value" text. Three fixes were tried
  and measured; each raised coverage but failed the gate (decided accuracy must not drop, stronger
  than label must not rise), so all three were reverted:

  | Change tried | Coverage with judge | Decided accuracy | Stronger | New wrong rows |
  |---|---|---|---|---|
  | Baseline | 49% | 76% | 4 | |
  | All three together | 57% | 73% | 5 | "Spring", "Operator : Gear/HW", "Body seat : Stellite 21" |
  | Quote checked piece by piece, alone | 53% | 75% | 5 | "Gasket : Graphite or equivalent" |

  Product type in front of short queries added only the wrong "Spring" row; sending coded rows
  with no field to the judge added 1 right and 2 weaker rows.
- The judge is not fully repeatable at temperature 0: "Gasket : Graphite or equivalent" was
  `partial_match` in one run and `full_match` in the next. Single-row differences between runs
  are within this noise.
- Evidence can come from another model in the same PDF. Catalogues with several product headings
  are read by heading, and unclear cases are capped at `partial_match`, but this is not solved.
- Wrong item rows on 8026686 and 8132165 are flagged, not fixed.
- Pump specifications often do not reach the items:
  - 7959356 (60 HP centrifugal pump): still flagged. Its item holds 1 requirement, "Starter Panel".
    Of its 73 catalogue-checkable general lines, 64 are ATC text about performance guarantees
    filed as performance requirements; the pump table on `buyer_spec.pdf` page 2 came out as one
    merged line. It needs a table-extraction fix, not the spec-row fix, so it gets no technical
    result.
  - 8071357 (submersible sewerage pump): fixed by the spec-row rule (1 requirement to 15).
  - 8169451 (water circulation pump, scanned): 6 of its 11 specification rows never became
    requirements: pump speed, size, seal, motor, spares and base plate. They are lost in the scan,
    not in the general list, so the spec-row rule cannot recover them. Not flagged, because its
    item still holds 20 requirements.
- Scanned tenders are not fixed. 8011173 (submersible pump) reads "SS304" as "55304" and splits
  its table into 38 rows such as "No. il" and "3 4"; 8169451 loses 6 of 11 rows. Neither is
  flagged.
- 99 labelled rows on 5 tenders (1 pump) are too few for firm figures: one row moves decided
  accuracy by about 2 points.
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

1. Raise recall with changes that pass the gate: a quote check that stays strict on table chunks
   (cells checked in their own row), and judge prompts that keep "or equivalent" and "spring"
   cautious. Rerun `eval/run_eval.py` after each.
2. Fix extraction on scanned specification tables (8169451, 8011173) and the pump table of
   7959356, then the flagged tenders, starting with item boundaries.
3. Label more tenders, especially pumps, so the figures rest on more than 99 rows.
4. Add the remaining manufacturers.
