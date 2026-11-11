# Tender Matcher: write-up

This describes what the system does, how it decides, how well it works today and where it falls
short. Setup is in the [README](../README.md).

**Verdict.** A first-pass assistant with checked evidence. Every decision cites a page and a quote
that was found on that page; a `full_match` agrees with the hand label 9 times in 10 (95% rules
only, 90% with the judge). A person reviews the `unsupported` and `partial_match` rows (about 4 in
10 labelled rows stay undecided, and a `partial_match` agrees with its label only about a third of
the time). It is not fit for automatic bidder ranking: 6 of 19 tenders give wrong reports, and the
ranking rests on low coverage.

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

**The LLM judge handles the rest.** Every requirement the rules leave `unsupported` for a catalogue
reason (no labelled field, free text) reaches Gemini (`gemini-3.8-flash`). It runs after the
catalogue is chosen, so it cannot change that choice or any rule result. For each line:

1. Hybrid search (BM25 and embeddings, fused with RRF, then a cross-encoder) runs inside the chosen
   catalogue only, with the attribute's ontology synonyms in the query. In a document holding
   several products, passages under another product's heading are left out. With no evidence
   hit, the LLM is not called; at most 5 passages are sent (`LLM_JUDGE_MAX_PASSAGES`).
2. The model answers in JSON and may cite only the passages it was given.
3. Its answer is not trusted. Document and page come from the cited passage, the quote must name
   the attribute and must be found on the page. A `full_match` or `missing_capability` on a
   numeric or code requirement must also be what the comparator reads from the judge's own quote.
   Anything failing stays `unsupported`.
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
| The judge's passage scored below `RERANK_MIN_SCORE` | 0.8 |
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

**Judge on when a key is set** (2026-11-10, replacing "judge off by default"). `LLM_JUDGE_ENABLED`
defaults to `true`; without `GEMINI_API_KEY` a run is rules only and logs a warning instead of
failing. Each tender has a call limit (`LLM_JUDGE_MAX_CALLS`, 35, about Rs 5 a tender at most),
because the API costs quota.

**Judge cache.** Every judge answer is stored in `judge_answers`, keyed by a sha256 of the prompt
version, the model and the full prompt (requirement text plus the passages with their chunk ids).
A rerun asks nothing twice, costs no quota and gives the same answers; failed calls are not
stored. Changing the prompt wording means raising `PROMPT_VERSION` in `llm_judge.py`.

**Low-score passages go to the judge.** When the product filter finds the right product's
passages but none reaches `RERANK_MIN_SCORE`, the best `LLM_JUDGE_LOW_SCORE_HITS` (3) go to the
judge instead of ending `unsupported`. The usual checks still apply: the quote must be found on
its page and name the attribute, else `unsupported` and review. Such decisions carry the
"search score below threshold" factor, so they always go to review.

**Files on disk.** The plan called for files stored in Postgres. They stay in a mounted `data/`
folder, because the catalogue registry and tender manifests read from disk and moving them is a
larger change than adding a store.

## Results

### Recall plan (2026-11-07 to 2026-11-11)

The last round of work aimed at recall: the system left too many rows `unsupported` that a person
could decide. Each step was measured against the hand labels and kept only if decided accuracy did
not drop and "stronger than label" rose by at most 1. Step by step notes are in
[eval/results.md](../eval/results.md).

| Step | Change | Result |
|---|---|---|
| 0 | Baseline | measured |
| 1 | Interleaved table cells cited line by line | kept, rules coverage 49% -> 52% |
| 2 | Standard codes from an untagged standards row (code only evidence) | kept, 53% |
| 3 | Product type lines met by the product heading | kept, 54% |
| 4 | Judge reach: every catalogue-unsupported line, 5 passages, synonyms | kept with step 5 |
| 5 | Judge guardrail: numeric and code verdicts re-checked by the comparator | kept with step 4 |
| 6 | Options lists under the item's own product heading | skipped (see known limits) |
| 7 | Scanned tender tables rebuilt from OCR word boxes | kept |
| 8 | Judge on by default when a key is set | kept |
| 9 | This write-up; no final evaluation run (decision of 2026-11-11, to save calls) | |

Latest measured figures. The two columns were measured at different times: rules after step 7, the
judge after steps 4-5 (on gold-labelled item x manufacturer pairs only, 92 Gemini calls). No run
with the judge was made after steps 6-8, so the judge column does not include step 7.

| Measure | Rules, step 0 | Rules, step 7 | Rules + judge, step 0 | Rules + judge, steps 4-5 |
|---|---|---|---|---|
| Coverage | 49% (49/99) | 54% (50/93) | 58% (57/99) | 62% (61/99) |
| Decided accuracy | 76% (37/49) | 76% (38/50) | 74% (42/57) | 72% (44/61) |
| `full_match` precision | 94% (32/34) | 95% (35/37) at step 6 | 90% (36/40) | 90% (38/42) |
| Stronger than label | 3 | 3 | 5 | 5 |
| Citation accuracy | 65% (24/37) | 63% (24/38) | 60% (25/42) | 68% (30/44) |

- **Rules gained 5 points of coverage at the same accuracy.** Every kept rules step turned
  `unsupported` rows into agreements, and none produced a new stronger row.
- **The judge gained coverage but lost accuracy** (74% -> 72%), below the gate. The user accepted
  it on 2026-11-09 after the judge was limited to the item's own product heading. Of the rows only
  the judge decides, its errors are 3 cautious (weaker than label: "Operator : Gear/HW", "Ball &
  Stem : SS A182", pump water at ambient temperature) and 2 stronger ("Flanged Ended", whose
  label is coarser than the requirement, and "Spring", see known limits).
- **Step 7 moved the pump tender's ids.** 8169451's scan now gives 32 item requirements instead of
  20, so the step 7 rules run counted 6 of its labelled rows as another catalogue (hence 93 rows,
  not 99). The gold was re-keyed on 2026-11-11 (14 rows now: the old description row with the flow
  merged in is two rows). On a rules-only run of that tender alone: 9 agree, 4 not decided, 1
  stronger ("Impeller type enclosed": Kirloskar `missing_capability`, label `unsupported`). The
  totals above were not recomputed after the re-key.

### One tender, rules against rules + judge

Earlier measurement (2026-11-02), before the recall plan.

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

Earlier measurement, before the recall plan. 87 rows on 4 tenders were checked by hand ([manual check](manual_match_check.md)) and compared with
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

Earlier measurement (2026-11-02), before the recall plan. `eval/run_eval.py` scores both runs on 99 labelled rows: the 87 above plus 12 on pump tender
8169451. Full tables, with precision and recall per status and confusion matrices, are in
[eval/results.md](../eval/results.md).

Before and after the last change, low-score passages sent to the judge (kept). "Before" is commit
d01bfd4's code. Both runs use the judge cache, so questions asked in both get the same answer and
the difference comes from the change alone:

| Measure | Rules before | Rules after | Rules + judge before | Rules + judge after |
|---|---|---|---|---|
| Coverage | 43% (43/99) | 43% (43/99) | 49% (49/99) | 51% (50/99) |
| Decided accuracy | 74% (32/43) | 74% (32/43) | 76% (37/49) | 74% (37/50) |
| `full_match` precision | 93% (27/29) | 93% (27/29) | 91% (31/34) | 91% (31/34) |
| `partial_match` precision | 33% (4/12) | 33% (4/12) | 38% (5/13) | 36% (5/14) |
| Stronger than label | 3 | 3 | 4 | 4 |
| Citation on a labelled page | 72% (23/32) | 72% (23/32) | 68% (25/37) | 68% (25/37) |
| Flagged tenders | 9 of 19 | 9 of 19 | 9 of 19 | 9 of 19 |
| Gemini calls | 0 | 0 | 32 | 52 new + 32 from cache |

The gate for this change was: coverage goes up and "stronger than label" rises by at most 1. It
passed (coverage +1 row, stronger unchanged), so it was kept. The rules columns are identical
because the change only affects the judge. Of the labelled rows that were below threshold before,
the judge now saw 12:

- 1 new decision: "Ball & Stem : SS (ASTM A 182 or equivelent)" (8017367) is `partial_match`, label
  `full_match` (weaker). The catalogue names A351 and A479 stainless steel, not A182.
- 6 the judge answered "no evidence" ("ZERO LEAKAGE", "HAND GEAR OPERATED", pump speed).
- 5 answers the checks rejected: 2 quotes not found on the page (one of them, "Spring", a
  `full_match` the label calls `partial_match`), 2 quotes that do not name the attribute, 1
  citing a passage it was not given. Each stays `unsupported` and goes to review.

So the gain is small, but the guards stopped a stronger-than-label answer.

These include the "/" rule of 2026-11-02. It changed 3 labelled rows: "Bolting : B7/2H"
(7956201) and "SHAFT MOC : A479 Type SS 410/17-4PH SS" (8119613) now agree with their
`full_match` labels; "DISC MOC : ASTM A217 Gr. CA15/CF8M" (8119613) is now `full_match` where the
label says `partial_match`, so it counts as stronger. That label predates the rule.

- **The judge adds coverage at little cost in accuracy.** It decided 7 more rows: 5 agree with the
  label and "Ball & Stem" (above) is weaker. The seventh, "Flanged Ended" on 7956201, is `full_match` where the label says
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

**Extraction**

- 6 of 19 tenders give wrong reports (by a read-through, not a measurement), and the extraction
  warning flags 9 of 19 (those 6 and 3 more). A flagged report sends every result to review; it
  does not fix the items. Wrong item rows on 8026686 and 8132165 are flagged, not fixed.
- Scanned pages are only partly fixed. Recall plan step 7 rebuilds two-column scanned tables from
  OCR word boxes and repairs ontology codes ("55304" -> SS304): 8011173 went from 38 garbled rows
  to 61 clean item requirements, and 8169451 from 20 to 32. Other scanned layouts (8157758,
  8167974) keep the plain OCR text, and pages with low OCR confidence are flagged for review, not
  fixed.
- Pump extraction:
  - 7959356 (60 HP centrifugal pump): the pump table on `buyer_spec.pdf` page 2 came out as one
    merged line, so its item holds 1 requirement, "Starter Panel", and it gets no technical
    result. It is flagged.
  - 8169451 (water circulation pump, scanned): since step 7 the speed, size, seal, motor, spares
    and base plate rows reach the item (32 requirements), but they have no hand labels yet.
  - 8071357 (submersible sewerage pump) is fixed by the spec-row rule (1 requirement to 15).

**Accuracy**

- Only about 100 labelled rows on 5 tenders (1 pump). One row moves decided accuracy by about 2
  points, so the figures are not firm.
- The final figures are not one run. Rules were last measured after step 7, the judge after steps
  4-5, and neither after the 8169451 gold re-key. A full `run_eval.py --judge` would need new
  Gemini calls and was not run (decision of 2026-11-11).
- The judge reads too much into a passage. "Spring" (8017367) became `full_match` from a line
  saying the design "prevents spring failure", which shows a spring exists but not the material
  or type asked. The guardrail re-checks numbers and codes only, not free-text inference.
- Product headings come from section names and, in documents declaring several product types,
  from a page's title line (fixed 2026-11-11; before, the figure caption "48-inch Class 150 Gate
  Valve" on L&T API 600 p8 owned pages 9-16, globe pages included). Still wrong: a range title
  the ontology does not read as naming several products ("ASME B16.34 L&T Gate, Y-Globe & Swing
  Check Valves", API 600 p16) leaves that page under the previous product. One-product
  catalogues read no page titles, so their variants are told apart by section names only.
- Options lists: an option makes a `full_match` only from a list under the item's own product
  heading or on a page of the whole document (front matter, ordering page); any other list gives
  `partial_match` + review. A shared ordering page does not say which product an option is for,
  so in a document whose products differ in options this may claim too much.
- "Impeller : Type -enclosed" (8169451) gives Kirloskar `missing_capability` where the label says
  `unsupported`: the catalogue does not state an impeller type, so this is a stronger row.
- Recall: 25 rows labelled `full_match` stayed `unsupported` with the judge before the recall plan;
  steps 1-5 decided some of them (coverage 58% -> 62% with the judge). Most have no catalogue
  field naming the attribute (codes stated in prose, such as "Testing Standard : API 598"), or a
  correct judge answer whose quote fails the page check because table chunks are rebuilt as
  "Header: value" text. Sending low-score passages to the judge added 1 row (above). Three earlier
  fixes raised coverage but failed their gate and were reverted:

  | Change tried | Coverage with judge | Decided accuracy | Stronger | New wrong rows |
  |---|---|---|---|---|
  | Baseline | 49% | 76% | 4 | |
  | All three together | 57% | 73% | 5 | "Spring", "Operator : Gear/HW", "Body seat : Stellite 21" |
  | Quote checked piece by piece, alone | 53% | 75% | 5 | "Gasket : Graphite or equivalent" |

- Judge variation: at temperature 0 the judge still changes about 1 labelled row between fresh
  runs ("Gasket : Graphite or equivalent" `partial_match` once, `full_match` the next; "Trim : 12
  -> 16" `unsupported` once, `partial_match` the next). The cache makes reruns repeat the stored
  answer, but any new question, prompt version or model gets a fresh, possibly different answer.
- Evidence can come from another model in the same PDF. Catalogues with several product headings
  are read by heading, and unclear cases are capped at `partial_match`, but this is not solved.
- Tied catalogues are not judged.

**Not built yet**

- Spec fact extraction exists but has never run on real catalogues, so `spec_facts` is empty. The
  comparator reads labelled fields from chunks instead of the structured lookup the design planned.
- Confidence has no factor for conflicting documents, and none for source priority (datasheet over
  catalogue), because conflicts are not detected.
- Duncan and Intervalve Poonawala catalogues are missing. The current manufacturers are Delval,
  KSB, Kirloskar Brothers and L&T.

**Operations**

- No Redis queue: jobs run in a queue inside the single API process, so a restart fails any run in
  progress. RQ and Redis from the plan are not added.
- Files are not in Postgres: they stay in the mounted `data/` folder, and PDFs are not in git or
  the images, so a fresh clone must supply them.
- Docker images are not published to a registry; they are built locally with `docker compose up
  --build`.
- The Docker image is CPU only: 20 chunks took about 160 seconds to embed, so a new database should
  be embedded on a GPU host. A run with the judge took about 7 minutes on CPU.
- Gemini quota is unknown, and the judge is now on by default whenever a key is set (35 calls per
  tender at most). The evaluation cache holds 346 judge answers; judging every item x
  manufacturer pair of the gold tenders, not only labelled ones, would need about 670 new calls.

## What to do next

1. Run `eval/run_eval.py --judge` once on the current code and re-keyed gold, so rules and judge
   figures come from one run (recount the new Gemini calls first).
2. Label more tenders, especially pumps (8169451's speed, size, seal and motor rows first), so the
   figures rest on more than about 100 rows on 5 tenders.
3. Run spec fact extraction on the real catalogues, so `spec_facts` is filled and numeric checks
   use the structured lookup instead of text fields.
4. Use Gemini vision extraction for the flagged tenders and the scanned layouts step 7 does not
   rebuild (8157758, 8167974, the pump table of 7959356).
