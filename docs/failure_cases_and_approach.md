# Failure cases, test cases and matching approach

This doc answers one question: how do we make sure each tender requirement ends up compared with
the right product's value, even though the text we parse from tenders and catalogues gets mixed up?

Everything below was observed by running the current ingestion code (`process_file`) on real
files in `data/` on 2026-10-29. Case IDs (V = vendor, T = tender, L = link) are used in tests and
in `eval/`.

## 1. Data used

| Side | What we have | Added today |
|---|---|---|
| Tenders | 19 GeM bids (14 downloaded from GeM BidPlus, 5 provided locally), with every linked spec, BOQ, CSV, ATC and MII file | – |
| Valves | L&T Valves (6 catalogues), DelVal (3 datasheets) | – |
| Pumps | none until today | KSB Etanorm brochure; Kirloskar KDS/KS/KDT/SRF/KDI monobloc catalogue; Kirloskar KP4 submersible brochure |

The new pump files are registered in `data/manufacturers/KSB/sources.yaml` and
`data/manufacturers/Kirloskar_Brothers/sources.yaml`. Official sites (ksb.com,
kirloskarpumps.com) do not serve the PDFs to scripts, so the copies come from distributor hosts;
`notes` records this.

Still missing: Duncan, Intervalve Poonawalla and Crompton. No public PDFs were found for them, so
those manufacturers will honestly get `unsupported`. A French KSB copy was rejected because the
distributor inserted its own ad pages into it (see V9).

## 2. Root cause in one line

Today a requirement and its value only belong together if they land in the same chunk, and
several layouts break that. **Text order is not structure.** Two-column pages, tables without
ruling lines, OCR output and grid charts all come out as a stream in which labels, values and
models drift apart.

## 3. Failure cases seen

### Vendor catalogues

| ID | What happens | Real example | Effect |
|---|---|---|---|
| V1 | **Several models in one row chunk** | Kirloskar p1: 3 SRF models in one chunk; p2: 25 KDS/GMC models in one cell (`KDS0510* 2 GMC-112* 3 GMC-116++* …`) | the values of 25 pumps share one chunk, so a match cannot say which model meets the duty point |
| V2 | **Caption taken as the header** | Kirloskar p1–2: column names are `PERFORMANCE CHART FOR`, `'SRF' SERIES,`, `2 POLE,`, `MONOBLOC PUMPS, AT RATED VOLTAGE…` | labels such as "kW / HP / Pipe size / RPM" are lost, so the numbers have no meaning |
| V3 | **Header row read as a data row** | Kirloskar p2 spec table: first row `KDS / KDI / KDT / KS / SRF` becomes data; values sit under `Column 3…6` | "Upto 110 meters" is no longer tied to KDT |
| V4 | **Continuation lines become separate rows** | `/ F Class(above 7.5 HP)`, `/Stainless Steel /Noryl`, `/Mechanical seal` | the "or SS" option is detached from its series: a missed match or a wrong one |
| V5 | **One spanning value split across columns** | KSB p2: `Pressure; Etanorm: Up to 16; Etanorm R: bar`; `Fluid temperature; Etanorm: -30 °C to; Etanorm R: +140 °C` | Etanorm R's pressure becomes "bar" and Etanorm's temperature loses its upper bound |
| V6 | **Availability marks lost** | KSB "Material variants": per-column availability is drawn as a symbol (only `–` survives as text) | "bronze available for Etanorm R?" cannot be answered; must be `unsupported` and never guessed |
| V7 | **Grid charts flattened** | Kirloskar head-vs-discharge charts: one number per line in prose chunks | a duty point (flow, head) lookup is impossible without rebuilding the grid |
| V8 | **Footer or slogan used as the section** | section = `A Kirloskar Group Company`, `Service-friendly`, `Flatter Efficiency Curve` | section-based routing and product tagging go wrong |
| V9 | **Third-party content inside a vendor PDF** | the French KSB copy carries distributor ad pages | non-manufacturer claims end up in the knowledge base |
| V10 | **Two-column prose interleaved** | KSB p2: "Great choice of materials… As a standard, the impellers are trimmed…" | quotes can splice two sentences; quote verification must catch this |
| V11 | Spelling variants | `MONOBLOC` (catalogue) vs `monoblock` (ontology) | product filter miss (A1) |
| V12 | Brochure-level detail only | KSB brochure: ranges only, no curves or per-size data | low coverage → correctly `unsupported` |

### Tender documents

| ID | What happens | Real example | Effect |
|---|---|---|---|
| T1 | **Line items merged** (table not detected: 43 drawn shapes but no ruling lines) | 8017367 `boq_spec.pdf`: 28 items became 2 prose chunks; item 11's `End details: Screwed female NPT` runs into `12 BALL VALVE DN38 MM, CLASS: 800 … 13 BALL VALVE DN50 MM` | a requirement is attached to the wrong item. **This is the "chunks get mixed" problem.** |
| T2 | **OCR shifts labels and values by one** | 8011173 spec (scanned): value `1500 Ipm` becomes the *section* of the chunk `Head @ duty point`; `2900 RPM` is the section of `Class of Insulation` | head could be read as 1500 lpm; every pairing is wrong |
| T3 | OCR misreads | `55304` = SS304, `55410` = SS410, `Ipm` = lpm, `415410%` = 415 ± 10 % | material and tolerance lost |
| T4 | Many requirements in one cell | 8119613: `TYPE : … SIZE : 250 MM PRESSURE RATING : CLASS #150 VALVE DESIGN : API 609 …` in one cell | must be split on `KEY :` patterns, per item |
| T5 | Same item twice with different values | 8119613 items 10 and 20: 250 mm / 650 m³/h vs 300 mm / 850 m³/h | must never merge; one requirement set per item (D5) |
| T6 | Repeated header after a page break | 8071357 p4: header rows chunked as junk (`Column 1 ववरण/Specification…`) | noise requirements |
| T7 | Bilingual labels with mojibake | `बड ववरण/Bid Details`, `ववरण/Speciﬁcation` | use the English half only (A8) |
| T8 | Buyer and admin data looks like requirements | consignee e-mails, bid dates, EMD, MSE (8071357, 8017367 CSV) | must be `not_applicable` or `vendor_documents`, never matched |
| T9 | Named make inside a column header | 8157057: `Description of items (Make: M/s. Intervalve Poonawalla Ltd.)` | a hard make requirement hidden in a header |
| T10 | Spec only in an attachment | most bids: `bid.pdf` holds only the item title; specs are in linked PDFs/CSV/DOCX | if links are not followed, there is nothing to match |

### Linked documents ("click here to download")

| ID | Case | Handling (most already in `tender_links.yaml` + link fetcher) |
|---|---|---|
| L1 | Spec/BOQ/ATC/CSV links inside `bid.pdf` | follow, depth ≤ 3, store each file with its role and URL |
| L2 | Empty responses (`downloadOmppdfile`, 13 occurrences) | recorded in `tender.yaml`, not stored |
| L3 | Word files served as `octet-stream`, XLSX ATC | detect by magic bytes, not by content type |
| L4 | Same spec linked once per item | dedupe by sha256 |
| L5 | Re-saved local copies with outlined text | prefer the GeM original from the link |
| L6 | HTML category page (`showCatalogue`) without units | PDF table stays primary; HTML is only a fallback |

## 4. The approach: structure first, retrieval second

Six rules, each fixing a group of failures above.

### Rule 1: Tender requirements are extracted per item, not retrieved from chunks (T1, T4, T5, T9)

Tender chunks are only for finding text. The requirements themselves are built as typed records
**before** anything is mixed:

1. **Item list first**: take it from `boq_items.csv` when present, else from the GeM
   item-category string, else from `Item No` / `Sl. No.` columns.
2. **Item segmentation**: cut the spec text at item boundaries (the item number plus the item
   title from the list, e.g. `12 BALL VALVE DN38`). Every following line belongs to that item
   until the next boundary.
3. **Split each item's text** on `KEY :` patterns, commas and "and" into atomic requirements.
   Each record stores: `item_no`, `product_type`, `attribute`, `operator`, `value`, `unit`,
   `raw_text`, file, page and bbox.
4. **Consistency checks**:
   - every item in the list has ≥ 1 requirement;
   - no requirement text matches two item titles;
   - a requirement mentioning a size or rating that contradicts its item title (DN38 inside item
     11, DN50) is flagged `needs_review`.

### Rule 2: Rebuild layout from word boxes, not from text order (T1, T2, V3, V7, V10)

- **Tables without ruling lines**: cluster words into rows by y and into columns by x gaps, using
  the header line's x-positions as column anchors.
- **OCR pages**: same row clustering on Tesseract word boxes. A label and a value are a pair only
  if they sit on the same row band. This fixes the "1500 lpm under the wrong label" shift.
- **Grid charts** (head vs discharge): detect a numeric header row plus a model column, then emit
  `(model, flow, head)` triples as spec facts (one per cell), each with its cell bbox.
- **Two-column prose**: read in column order, using PyMuPDF blocks sorted by column then y.

### Rule 3: One chunk = one product variant (V1, V4, V5, V6)

- A row chunk may name **at most one model**. A cell holding several model codes is split by row
  bands; if that is impossible, the chunk is marked `multi_model` and can never be the only
  evidence for a match.
- **Continuation lines** (starting with `/`, `or`, lowercase, or with no label cell) are merged
  into the row above, cell by cell.
- **Orphan units/bounds** (`bar`, `to`, `+140 °C` alone in a column) mean a spanning value: merge
  the cells back into one value and apply it to every column it spans.
- **Symbol-only cells** (ticks, dots) are not readable → availability = unknown, never a
  confident "no".

### Rule 4: Clean the document before chunking (V2, V8, V9, T6, T7)

- **Boilerplate**: lines repeated on ≥ 50 % of pages (company footer, slogans, distributor
  banners) are dropped from chunks and never used as headings.
- **Header detection by content**: a header row has short label words and no numbers; a long
  sentence ("PERFORMANCE CHART FOR …") is a caption. The caption goes to `section`, and the next
  row becomes the header.
- **Repeated headers** after a page break are dropped (already partly done).
- **Bilingual cells**: keep the Latin-script half.

### Rule 5: Matching is keyed, not free search (V1, B10)

For each `(item, requirement, manufacturer)`:

1. **Product filter**: item product type → that manufacturer's documents and chunks of that type.
2. **Pick a model or variant once per item**: choose the candidate model whose facts cover the
   critical requirements (size, rating, standard, body material). All later evidence must come
   from that same model or variant, or from chunks that apply to the whole series.
3. **Attribute lookup** in `spec_facts` for that model; hybrid search only to find additional
   text evidence.
4. **Numbers are decided by the comparator** (ranges, units, direction). Text search never decides
   a number (B4).

### Rule 6: Verify the row, not just the page (V5, V10, T2)

A quote counts as evidence only if:

- it is present verbatim in the page's raw text (current rule), **and**
- the label and the value it supports lie in the **same row band / cell group** (bbox check), and
- the chunk has no integrity flag (`multi_model`, `orphan_unit`, `ocr_shifted`).

Otherwise the result is downgraded to `unsupported` + `needs_review`. Being unsure costs a little
coverage; a wrong citation costs evidence-quality points.

### Chunk integrity flags (stored in `chunk.meta`)

| Flag | Detects | Used for |
|---|---|---|
| `multi_model` | > 1 model code in one chunk | never sole evidence |
| `orphan_unit` | a cell that is only a unit or bound | trigger the spanning-cell merge |
| `caption_header` | header longer than N words | re-read the table with the next row as header |
| `boilerplate` | text repeated across pages | drop |
| `ocr_low_conf` / `ocr_shifted` | OCR confidence low, or label/value count mismatch in a row band | confidence down, `needs_review` |
| `symbol_cells` | cells empty in text but with drawn marks | availability unknown |

## 5. Test cases

Each line becomes a unit or integration test; the real files stay local and fixtures are small
synthetic copies of each layout (marked as synthetic, never presented as real evidence).

| # | Case | Input (layout) | Expected |
|---|---|---|---|
| 1 | T1 item bleed | 3 items in an unruled table; item 1 text continues over the line break | 3 items; "Screwed female NPT" only on item 1 |
| 2 | T1 boundary check | item text names DN38 inside the DN50 item | `needs_review` |
| 3 | T2 OCR row pairing | OCR words: labels left, values right, values 2 px lower | label↔value pairs correct |
| 4 | T3 OCR codes | `55304`, `Ipm`, `415410%` | `ss304`, `lpm`, 415 V ± 10 % (code variants only from the ontology) |
| 5 | T4 key:value cell | `SIZE : 250 MM PRESSURE RATING : CLASS #150` | two requirements with units |
| 6 | T5 duplicate items | items 10 and 20 with different sizes | two separate requirement sets |
| 7 | T6 repeated header | table continues on the next page with header repeated | no header-row requirements |
| 8 | T8 admin rows | bid dates, e-mails, EMD | `not_applicable` / `vendor_documents` |
| 9 | T9 make in header | `(Make: M/s. X)` in a column header | named-make requirement on every item |
| 10 | V1 multi-model cell | one cell lists 3 models with 3 kW values | 3 chunks or `multi_model` flag |
| 11 | V2 caption header | first row is a long title sentence | caption → section; real header used |
| 12 | V3 header as data | first row = series names | series names become column headers |
| 13 | V4 continuation | `Cast Iron / Bronze` + next line `/Stainless Steel` | one cell "Cast Iron / Bronze / Stainless Steel" |
| 14 | V5 spanning value | `Up to 16` · `bar` in adjacent columns | "Up to 16 bar" for both columns |
| 15 | V6 tick marks | availability shown as symbols | availability unknown → `unsupported` |
| 16 | V7 grid chart | model rows × flow columns → head | (model, flow, head) triples; duty-point lookup works |
| 17 | V8 boilerplate | same footer on every page | not a section, not a chunk |
| 18 | V10 two columns | two text columns side by side | sentences not interleaved |
| 19 | B10 model mixing | size in model A, material in model B | no FULL by combining |
| 20 | Row verification | quote on the page but the value is in another row | downgraded to `unsupported` |
| 21 | L2/L3/L4 links | empty response, octet-stream DOCX, same link twice | skipped, typed correctly, stored once |
| 22 | Out-of-category tender | thermocouple tender vs valve catalogues | all `unsupported` / not found |

Gold labels: the hand-checked results in `docs/manual_match_check.md` (5 valve tenders) plus 2
pump tenders (8071357 vs KBL, 7959356 vs KSB) to label next.

## 6. Where this lands in the build plan

| Fix | Module | Step |
|---|---|---|
| Boilerplate, caption-header, header-as-data, continuation and spanning-cell merge, integrity flags | `ingestion/table_extractor.py`, `chunker.py`, new `layout.py` | fix inside done Steps 3–4 (next) |
| Word-box tables + OCR row pairing + grid charts | new `ingestion/layout.py`, `ocr.py` | same |
| Item list + segmentation + key:value split + boundary checks | `tender/items.py`, `splitter.py`, `normalizer.py` | Step 8 |
| Model selection, keyed lookup | `matching/engine.py` | Step 10 |
| Row-level verification | `matching/evidence_verifier.py` | Step 10 |
| Test cases 1–22 | `tests/unit/...`, `tests/integration/...` | with each fix |
