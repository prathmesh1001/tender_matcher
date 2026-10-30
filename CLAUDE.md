# CLAUDE.md — Tender Requirement Matching System

Instructions for Claude when working in this repository. Follow them on every task.

## 1. What this project is

A system that takes a GeM tender PDF, extracts every technical requirement, and checks it against
manufacturer catalogues/datasheets (KSB, Duncan, Delval Flow, Intervalve Poonawala, …). For each
requirement × manufacturer it outputs: capability, status, score, and **evidence (document, page,
verbatim quote)**. It also produces an overall compatibility % and a ranking per manufacturer.

Core principle: **never guess.** If evidence is missing or can't be verified, the answer is
"insufficient evidence", not a confident match. Every claim must point to a real page and quote.

## 2. How to work: step by step

- Work on **one step at a time** from the Build Plan (section 10). Don't start the next step until the user says so.
- Before coding a step, state in 2–3 lines what you are about to build.
- Keep each step small enough to review (roughly one module or feature).
- At the end of every step, reply with this short summary and nothing longer:

  ```
  ✅ Step N — <step name>
  Changed:
  - <file>: <one line on what was added/changed>
  - ...
  Run/verify: <one command to try it>
  Next: Step N+1 — <name>
  ```
- If a step uncovers a decision the user should make (library choice, threshold, definition), ask before proceeding.
- Don't rewrite or reformat files outside the current step's scope.

## 3. Tech stack

| Layer | Choice |
|---|---|
| Backend | Python 3.14 (3.14.4), FastAPI, Pydantic v2, SQLAlchemy 2.0, Alembic |
| DB | PostgreSQL + pgvector (SQLite allowed for local dev only) |
| Jobs | Background worker (RQ + Redis; FastAPI BackgroundTasks only for first prototype) |
| PDF | PyMuPDF (text + bbox), pdfplumber/Camelot (tables), Tesseract/PaddleOCR (scanned) |
| Search | BM25 (rank_bm25 or Postgres FTS) + dense embeddings + cross-encoder reranker |
| Units | `pint` |
| LLM | Behind an interface (`LLMClient`), provider set by config; always structured JSON output |
| Frontend | React + TypeScript + Vite, TanStack Query, React Router, Tailwind |
| Tests | pytest (backend), Vitest + Testing Library (frontend) |
| Quality | ruff, black, mypy (backend); eslint, prettier, tsc strict (frontend) |
| Deploy | Two Docker images (backend, frontend) + docker-compose |

## 4. Repository structure

```
tender-matcher/
├── backend/
│   ├── app/
│   │   ├── main.py                  # app factory only: create_app(), routers, middleware
│   │   ├── api/
│   │   │   ├── deps.py              # dependency injection (db session, services)
│   │   │   ├── router.py            # aggregates all route modules into api_router (/api/v1)
│   │   │   └── routes/              # thin HTTP layer, ONE FILE PER RESOURCE, never combined
│   │   │       ├── tenders.py
│   │   │       ├── runs.py
│   │   │       ├── reports.py
│   │   │       ├── manufacturers.py
│   │   │       └── health.py
│   │   ├── core/
│   │   │   ├── config.py            # pydantic-settings, all env vars here
│   │   │   ├── logging.py
│   │   │   └── exceptions.py        # domain exceptions + HTTP handlers
│   │   ├── schemas/                 # Pydantic request/response + domain models
│   │   ├── db/
│   │   │   ├── session.py
│   │   │   ├── models/              # SQLAlchemy ORM models
│   │   │   └── repositories/        # all DB queries live here, nowhere else
│   │   ├── ingestion/               # manufacturer KB pipeline
│   │   │   ├── registry.py          # reads sources.yaml
│   │   │   ├── pdf_parser.py        # page text + bbox
│   │   │   ├── ocr.py
│   │   │   ├── table_extractor.py
│   │   │   ├── chunker.py
│   │   │   ├── spec_extractor.py    # LLM → SpecFact
│   │   │   ├── quote_verifier.py
│   │   │   └── pipeline.py          # orchestrates the steps above
│   │   ├── tender/                  # tender pipeline
│   │   │   ├── parser.py
│   │   │   ├── classifier.py        # technical / commercial / legal / vague
│   │   │   ├── splitter.py          # compound → atomic
│   │   │   ├── normalizer.py        # operator, value, unit
│   │   │   └── pipeline.py
│   │   ├── retrieval/
│   │   │   ├── embedder.py
│   │   │   ├── bm25_index.py
│   │   │   ├── vector_index.py
│   │   │   ├── hybrid_search.py     # RRF fusion
│   │   │   ├── reranker.py
│   │   │   └── query_builder.py     # ontology synonyms
│   │   ├── matching/
│   │   │   ├── comparator.py        # deterministic numeric/enum/bool rules
│   │   │   ├── llm_judge.py
│   │   │   ├── evidence_verifier.py
│   │   │   ├── confidence.py
│   │   │   ├── scorer.py            # per-req score, overall %, ranking
│   │   │   └── engine.py            # orchestrates retrieve → compare → judge → verify
│   │   ├── reporting/
│   │   │   ├── json_report.py
│   │   │   └── excel_report.py
│   │   ├── llm/
│   │   │   ├── base.py              # LLMClient protocol
│   │   │   └── providers/
│   │   ├── services/                # business use-cases called by routes
│   │   │   ├── tender_service.py
│   │   │   ├── run_service.py
│   │   │   └── report_service.py
│   │   └── workers/
│   │       └── tasks.py
│   ├── ontology/                    # one YAML per product category (no code change for new category)
│   │   └── valves_pumps.yaml
│   ├── alembic/
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   └── fixtures/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                     # typed API client, one file per resource
│   │   ├── components/              # reusable, presentational
│   │   │   ├── ui/                  # Button, Badge, Table, Card, Spinner…
│   │   │   └── report/              # StatusBadge, EvidenceCell, ScoreBar…
│   │   ├── features/                # feature folders: upload/, runs/, report/
│   │   ├── hooks/
│   │   ├── pages/
│   │   ├── types/                   # mirrors backend schemas
│   │   ├── lib/                     # utils, formatters
│   │   └── App.tsx
│   ├── package.json
│   └── Dockerfile
├── data/
│   └── manufacturers/<manufacturer>/   # PDFs + sources.yaml (provenance)
├── eval/
│   ├── gold/                        # hand-labelled tenders
│   └── run_eval.py
├── docs/
│   └── writeup.md
├── docker-compose.yml
├── .env.example
└── README.md
```

## 5. Backend coding rules

**Layering (strict, one direction only):**
`routes → services → (ingestion | tender | retrieval | matching | reporting) → repositories → db`

- **Routes are thin**: validate input, call one service method, return a schema. No business logic, no SQL.
- **Every route group lives in its own file** under `api/routes/` (`health.py`, `tenders.py`, `runs.py`,
  `reports.py`, `manufacturers.py`, …). Never put routes for different resources in the same file, and
  never define routes in `main.py`.
  - Each file defines its own `router = APIRouter(prefix="/<resource>", tags=["<resource>"])`.
  - `api/router.py` is the only place that includes them: `api_router.include_router(tenders.router)`, etc.
  - `main.py` only does `app.include_router(api_router, prefix=settings.API_PREFIX)`.
  - A new resource means a new route file + a new service, registered in `api/router.py`.
  - Each route has a `response_model`, an explicit `status_code`, and a short docstring (shown in OpenAPI).
- **Services** orchestrate use cases. They receive dependencies via constructor / `Depends`, never import global singletons.
- **Repositories** are the only place that touch SQLAlchemy sessions/queries.
- **Pipelines** (`*/pipeline.py`) compose small pure functions; each stage is independently testable.

**Functions:**
- One function = one job. Aim for < 40 lines; split when longer.
- Full type hints everywhere; `mypy --strict` must pass.
- Prefer pure functions (input → output, no side effects) for parsing, normalizing, comparing, scoring.
- Pass typed Pydantic models / dataclasses between stages, never raw dicts.
- Google-style docstring on every public function: what it does, args, returns, raises.
- No magic numbers. Thresholds (rerank cutoff, confidence, chunk size) live in `core/config.py`.

**Errors and logging:**
- Raise domain exceptions (`TenderParseError`, `NoEvidenceError`, …) from `core/exceptions.py`; map to HTTP in one handler.
- Never swallow exceptions silently. Never use bare `except:`.
- Structured logging (JSON) with `run_id`, `tender_id`, `manufacturer` in context. No `print`.

**Config and secrets:**
- All config through `core/config.py` (pydantic-settings). Document every var in `.env.example`.
- No secrets, URLs, or paths hard-coded in code.

**Extensibility:**
- External dependencies (LLM, embedder, reranker, OCR, vector store) sit behind small interfaces/protocols so they can be swapped.
- Category-specific knowledge (attributes, synonyms, units) lives in `ontology/*.yaml`, never in Python.

## 6. Ingestion rules

**Manufacturer documents**
- Every document must be registered in `data/manufacturers/<mfr>/sources.yaml` with:
  `manufacturer, category, product_types, (sub_types), title, doc_type, url, fetched_at, publish_date, sha256`.
- `product_types` is checked by hand per document. Every chunk is tagged with a product type
  (`product_tagger.py`): only a declared type, from a section heading on the chunk's page or the
  chunk's own text, never guessed; unclear chunks stay untagged ("general"). A spec-table page
  naming no product continues the clear product of the page before it (catalogues put a
  product's tables on the page after its introduction); prose pages and pages whose text
  names another product never inherit.
- Parse **page by page**; every chunk and fact carries `doc_id` + `page`.
- Detect scanned pages (very little text layer) → OCR, mark `ocr=true`.
- Tables: each row becomes its own chunk **with header and product family/model repeated**.
  Exception: an ordering-code legend (columns of "1 - PN 10", "I - Cast Iron" options) gives one
  chunk per column, because its rows are not products.
- Prose: section-aware chunks, ~300–500 tokens, small overlap.
- **Every chunk keeps two versions of its text:**
  - `raw_text`: original, verbatim. Used for quotes and evidence verification. Never modified.
  - `search_text`: cleaned for retrieval only. Lowercased, plurals/inflections reduced
    ("valves" → "valve"), special characters folded (ligatures, curly quotes, dashes), codes in one
    canonical form (SS 316 / SS-316 → `ss316`).
  - One shared `normalize_for_search()` cleans both catalogue chunks and tender queries, so both
    sides always match. Quotes are never taken from `search_text`. See section 12 for the cases it must handle.
- Spec extraction outputs `SpecFact {manufacturer, product_family, model, attribute, value, unit, min, max, doc_id, page, quote}`.
- **Every `quote` must be verified** to exist on the cited page (normalized fuzzy match ≥ 0.9). Drop facts that fail.
- Normalize units with `pint` to canonical units defined in the ontology.
- Keep conflicting facts; mark `conflict=true`. Source priority: datasheet > catalogue > brochure, then newest date.
- Ingestion must be **idempotent**: re-running on the same sha256 does not duplicate data.
- **Sections** (`document_sections`, both sides): a section is a run of chunks under one heading,
  with parent (heading numbers "10.1" → "10"), page range and a category from
  `config/section_categories.yaml` (costing, eligibility, financial, experience, certificates,
  delivery, warranty, legal, submission, buyer, quality, size, material, performance, technical).
  Every chunk stores its `section_id` and `section_category`: heading phrase first, else the
  chunk's own text (GeM bid rows have no heading), else `other`. Categories route requirements to
  evidence; they never decide compliance. `classify-sections` rebuilds them without re-parsing.

**Tender documents**
- Extract the GeM specification tables and free-text sections with page numbers. Two-column
  tables in tender files are `label: value` pairs (GeM bid layout), not header + rows.
- **Every** requirement is extracted, not only technical ones. Each category says where vendor
  evidence comes from (`evidence` in `section_categories.yaml`): `catalogue` (checked now),
  `vendor_documents` (costing, turnover, experience, certificates, delivery, warranty: no vendor
  documents are ingested, so these report "no evidence" → `unsupported` + `needs_review`, never a
  failure), `none` (buyer details, bid schedule → `not_applicable`).
- Classify each line: `technical | commercial | legal | compliance_doc | vague`.
- Split compound lines into atomic requirements linked by `parent_id`.
- Normalize to `Requirement {id, parent_id, attribute, operator(=,>=,<=,range,in,bool,text), value, unit, raw_text, page, type}`.

## 7. Retrieval & matching rules

- Section mapping first: only requirements whose category has `evidence: catalogue` are searched
  in catalogues; the category's `vendor_categories` rank catalogue sections (a hint, not a filter).
  Other categories are reported without searching (see section 6, tender documents).
- Per requirement × manufacturer, in this order:
  1. **Product filter first** (cheap, by keyword): find the tender item's product type with ontology
     synonyms; keep only that manufacturer's chunks of that type plus untagged chunks of documents
     declaring it (`ChunkRepository.list_for_product`). No document for the type → `unsupported`
     for every requirement of the item; nothing else is searched.
  2. Structured lookup in `spec_facts` by canonical attribute within that product.
  3. Hybrid search inside the filtered set: BM25 on `search_text` (weighted first) + dense on the
     stored vectors, query expanded with ontology synonyms, fused with RRF.
  4. Cross-encoder rerank → top-k (config). A hit is evidence if its score ≥ threshold **or** it
     contains one of the requirement's codes (`en593`, `pn10`, or a grade code of a material it
     names via ontology `equivalents`, e.g. SGI → `a536`); bare numbers never count (B4).
     Code-only hits keep their low score, so confidence goes down. No evidence hit →
     `unsupported`, skip LLM.
  5. Numbers via the comparator, then per-manufacturer scoring (below).
- Sub-type is never a filter (B2): a sub-type mismatch lowers the status, it does not hide evidence.
- **Like-for-like comparison (always):** a tender requirement is compared only with the vendor
  value for the **same attribute, same component and same condition**, mapped through the ontology:
  - same attribute: tender `pressure_rating` vs catalogue `pressure_rating` (never vs `test_pressure`
    or another number on the same row)
  - same component: tender "Disc MOC" vs catalogue "Disc", never "Body" or "Seat"
  - same condition: same model/variant column, and same basis (duty point vs max, 50 Hz vs 60 Hz,
    working vs test pressure)
  If the catalogue has no value for that exact attribute + component, the result is `unsupported`
  (no evidence), never a match taken from a different attribute that happens to share a number or word.
  The evidence quote must contain the attribute label (or its ontology synonym) and the value.
- **Rules before LLM**: numeric/enum/bool requirements are decided by `comparator.py` (units + direction aware).
- LLM judge only for free-text/ambiguous cases; must return JSON with `status, capability, chunk_id, quote, reasoning`.
- `evidence_verifier` re-checks every quote against the page. Failed verification → downgrade to `unsupported` + `needs_review`.
- Match against **one product model/family per manufacturer**; never combine specs from different models.

**Status definitions (keep consistent everywhere):**

| Status | Meaning | Score |
|---|---|---|
| `full_match` | Evidence satisfies all conditions | 1.0 |
| `partial_match` | Some sub-conditions met, only some models, optional accessory, or near-miss | 0.5 |
| `missing_capability` | Catalogue covers the attribute and the value fails | 0.0 |
| `unsupported` | Not technically verifiable, or no evidence in catalogues | excluded |
| `not_applicable` | Nothing to compare (buyer details, bid dates, procedure) | excluded |

- Overall % = Σ(score × weight) / Σ(weight) over verifiable technical requirements. Also report evidence coverage %.
- Confidence goes down for LLM decisions, OCR, brochure-only sources, conflicts, and low rerank scores. Below the threshold → `needs_review=true`.

## 8. Frontend rules

- TypeScript strict; no `any`.
- **Feature-folder structure**; pages compose features, features compose components.
- Components are small and presentational; data fetching only via hooks in `api/` + TanStack Query.
- All API types in `src/types/`, mirroring backend schemas.
- Every async view handles loading, error, and empty states.
- Required screens:
  1. **Upload**: drag-drop tender PDF, choose category/manufacturers, start run.
  2. **Runs**: list past runs with status (queued/running/done/failed) and polling.
  3. **Report**: summary cards (compatibility %, coverage %, ranking), status counts, filterable requirement table, colour-coded status badges, evidence cell with doc/page/quote and a link that opens the PDF at that page, `needs_review` filter, export JSON/Excel.
- Consistent design tokens (colours for each status, spacing, typography) in one place.
- Accessible: semantic HTML, labels, keyboard navigable, sufficient contrast.

## 9. Testing & quality

- Every new module ships with unit tests in the same step.
- Test the hard cases explicitly: units (kg/cm² vs bar), direction (≥, ≤, ranges), compound lines, vague lines, missing evidence, conflicting docs, scanned pages.
- Integration test: one small tender + one manufacturer fixture → full report.
- `eval/run_eval.py` compares output against gold labels: per-status precision/recall, confusion matrix, citation accuracy.
- Before finishing a step, run: `ruff check`, `mypy`, `pytest` (backend) / `npm run lint`, `npm run typecheck`, `npm test` (frontend). Report failures honestly.

## 10. Build plan (do in order, one step per turn)

1. **Scaffold**: repo structure, pyproject, config, logging, exceptions, health route, app factory.
2. **DB layer**: SQLAlchemy models (documents, pages, chunks, spec_facts, tenders, requirements, runs, results), Alembic, repositories.
3. **Source registry + PDF parsing**: sources.yaml loader, page text/bbox extraction, OCR fallback.
4. **Table extraction + chunking**: table-row chunks with headers, prose chunks with metadata.
5. **Ontology + unit normalization**: valves_pumps.yaml, pint helpers.
6. **Spec fact extraction + quote verifier** (LLM client interface + provider).
7. **Indexing**: embeddings, vector index, BM25; ingestion pipeline + CLI/endpoint. Source files are read through a `FileStore` interface (`LocalFileStore` for now; files live on the dev machine, never in git).
8. **Tender parsing**: link fetcher (follow spec/BOQ/ATC links in `bid.pdf`, depth ≤ 3, see D1), document sections + categories (done, both sides), parser, classifier, splitter, normalizer, tender pipeline. Requirements of every category are extracted, with category, product type and mandatory flag.
9. **Retrieval**: query builder, hybrid search with RRF, reranker.
10. **Matching**: comparator, LLM judge, evidence verifier, confidence, model selection.
11. **Scoring & reporting**: scorer, JSON report, Excel report.
12. **API + services + worker**: upload, start run, run status, get report, list manufacturers.
13. **Frontend scaffold**: Vite + TS + Tailwind, routing, API client, UI primitives.
14. **Frontend features**: upload, runs list, report view with evidence.
15. **Evaluation harness**: gold label format, metrics script.
16. **Docker**: backend + frontend images, docker-compose, `.env.example`. Add `PostgresFileStore` (vendor + tender files stored in Postgres, keyed by sha256) and a one-off import of the local files; switch via config.
17. **Docs**: README (setup, docker pull/run, ports, env vars, example output), write-up.

## 11. Don'ts

- Don't put logic in routes or SQL in services.
- Don't put multiple resources' routes in one file, or any routes in `main.py`.
- Don't create one giant file; split by responsibility.
- Don't invent evidence, page numbers, or quotes, in code or in test fixtures presented as real.
- Don't hard-code manufacturer names, attributes, or thresholds in Python.
- Don't add a dependency without saying why in the step summary.

## 12. Known retrieval failure cases (handle at ingestion + query time)

Example: tender says "balls valve", catalogue says "Ball Valves". Plain BM25 sees `balls`≠`ball` and
`valve`≠`valves` → zero term overlap → the right chunk may not be retrieved, or ranks below a chunk that
merely says "valve" many times. Dense search usually recovers it, but must not be relied on alone.

**Core rule:** every chunk stores `raw_text` (verbatim, used for quotes + verification) **and**
`search_text` (normalized, used only for BM25/embeddings). One shared `normalize_for_search()` is used by
both the indexer and the query builder, so both sides are normalized identically. Quotes are never taken
from `search_text`. Vocabularies (synonyms, abbreviations, spelling variants) live in `ontology/*.yaml`.

**A. Text variants: normalize identically on both sides**

| # | Failure case | Example | Handling |
|---|---|---|---|
| A1 | Plural / inflection | ball valve ↔ balls valve ↔ Ball Valves | lowercase + stemming (Snowball) in `search_text` |
| A2 | Typos in tender | "buterfly", "gate vlave", "globle" | query side only: fuzzy-correct to ontology vocabulary (edit distance ≤ 2, tokens ≥ 5 chars); log the correction |
| A3 | Unicode / ligatures | "Butterﬂy" (ﬂ ligature, seen in L&T catalogue), `3”`, en-dash, NBSP | NFKC + quote/dash/space folding |
| A4 | Spacing / hyphen in codes | SS316 / SS 316 / SS-316 / 316 SS; 17-4-PH / 17-4PH; A 216 Gr. WCB / A216 WCB | canonical code patterns from ontology (`ss316`, `a216_wcb`) |
| A5 | Abbreviations | NRV=check valve, BFV=butterfly, CI=cast iron, SG iron=ductile, NB/DN=size, #/lb=Class | ontology synonyms, expand query |
| A6 | Compound / word order | ball-valve, "valve, ball", "valves – ball type" | tokenization splits hyphens; phrase match tolerant to order |
| A7 | OCR misreads | SS316→55316, O↔0, l↔1, S↔5, B↔8 | OCR-confusion variants only for codes in ontology; mark `ocr=true` → lower confidence |
| A8 | Hindi / bilingual lines | Hindi heading + English spec | detect script; index the English part; never match on transliteration alone |

**B. Ranking failures: right words, wrong product**

| # | Failure case | Example | Handling |
|---|---|---|---|
| B1 | Term appears in other context | "ball" in ball bearing / float ball; "check" in "check the…"; "gate" in "gate unit" | match product **type** as a phrase; filter by product_family before rank |
| B2 | Sibling sub-type | butterfly query → triple-offset vs concentric; ball → floating vs trunnion | sub-type is an ontology attribute; mismatch = LEAST, not FULL |
| B3 | Dense confuses similar types | gate ↔ globe ↔ plug | type filter/boost first, embeddings second |
| B4 | Numbers inside ranges | tender 200 mm, catalogue "50 to 600 mm" → BM25 sees no "200" | numeric checks only via structured `spec_facts` + comparator, never via text match |
| B5 | Units / size systems | kg/cm² vs bar; 3" vs 80 mm vs DN80 | `pint` + ontology size tables (NPS↔DN) |
| B6 | PN ↔ Class | PN16 vs Class 150 | EN 1759 mapping table only, never via bar values |
| B7 | Negation / exclusion | "not suitable for steam", "without gear operator" | flagged to LLM judge; comparator never treats as positive |
| B8 | Long documents dominate | a 100-page catalogue repeats "valve" everywhere | BM25 length normalization; rank per chunk, not per document |
| B9 | Duplicate content | same table in catalogue + brochure | dedupe by normalized text hash; keep higher-priority source |
| B10 | Model mixing | size from model A, material from model B | all facts for a match from one model/family (section 7) |

**C. Ingestion failures that break retrieval later**

| # | Failure case | Handling |
|---|---|---|
| C1 | Table continues on next page without header | carry header forward to continuation table |
| C2 | Horizontally merged cells filled down wrongly | fix fill logic in `table_extractor` (known bug) |
| C3 | Column header split across two rows | merge multi-row headers before chunking |
| C4 | Outlined/vector text (no text layer, few images) | detect and OCR |
| C5 | OCR'd tables lose columns | rebuild rows from word bboxes |
| C6 | Model name only in section heading | repeat section/model in every chunk (already done) |
| C7 | Footnotes change meaning ("*on request", "optional") | keep footnote markers; map to `partial_match` |

**D. Tender-side failures**

| # | Failure case | Handling |
|---|---|---|
| D1 | Real spec is in a linked attachment, not bid.pdf | follow spec/BOQ/ATC links during tender ingestion |
| D2 | Compound line | "SS316 body & disc, PN16, flanged" → split into atomic requirements |
| D3 | Vague line ("good quality", "as per standard") | classify `vague` → `unsupported` |
| D4 | "or equivalent", named make | record flag; never auto-fail other makes |
| D5 | Same item repeated with different sizes | one requirement set per line item |

**Tests:** every row above gets at least one unit test (normalizer, query builder, comparator, or table
extractor). The retrieval eval must include a variant set: same query written ≥ 3 ways (plural, typo,
abbreviation) must return the same top-1 chunk.
