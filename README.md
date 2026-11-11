# Tender Matcher

Takes a GeM tender PDF, extracts its requirements and checks each one against manufacturer
catalogues. For every requirement and manufacturer it reports a status, a score and the evidence
behind it: document, page and verbatim quote. It also gives each manufacturer a compatibility %,
a coverage % and a rank.

The core rule is **never guess**. If evidence is missing or a quote cannot be found on its page,
the result is `unsupported`, not a confident match.

## Run with Docker

You need Docker with Compose v2, 2.7 GB for the images plus room for the downloaded models, and
the catalogue PDFs.

```sh
git clone https://github.com/prathmesh1001/tender_matcher.git
cd tender_matcher
cp .env.example backend/.env        # optional; the stack also starts without this file
docker compose up -d --build
docker compose exec backend python -m app.cli ingest-manufacturers
```

Then open <http://localhost:8080>, upload a tender `bid.pdf`, pick manufacturers and start a run.

The images are built locally; none are published to a registry.

**PDFs are not in git or the images.** Each `data/manufacturers/<name>/sources.yaml` records the
URL and sha256 of its files. Put the PDFs in those folders before ingesting. `./data` is mounted
into the backend, and uploaded tenders and their fetched attachments are written there too.

| Service | Address | What it is |
|---|---|---|
| `frontend` | <http://localhost:8080> | Web app; nginx also forwards `/api` to the backend |
| `backend` | <http://localhost:8000/api/v1/docs> | API and Swagger docs |
| `db` | `localhost:5433` | PostgreSQL with pgvector |

Migrations run each time the backend starts. Stop the stack with `docker compose down`; add `-v`
only if you also want to delete the database and caches.

Times measured on one tender (9-page bid, 12 catalogues) with Docker Desktop limited to 3.5 GB:

- Ingesting all 12 catalogues: about 4 minutes.
- First run on an empty cache: about 2.5 minutes, because it processes the catalogues.
- Run with the LLM judge on CPU: about 7 minutes.

### Embeddings are slow on CPU

Rules-only runs need no vectors. Hybrid search does, and the image has CPU PyTorch only: 20 chunks
took about 160 seconds, so all 1,794 would take hours. Embed from a host with a GPU instead. Both
share the same database, so the vectors are then available to the container:

```sh
cd backend
python -m app.cli index
```

The model cache is a Docker volume and the models download on first use. To reuse a Hugging Face
cache you already have, set this in the shell or in a `.env` next to `docker-compose.yml`:

```sh
HF_CACHE_DIR=C:/Users/you/.cache/huggingface
```

### Keep to one API process

Use a single uvicorn worker, and do not run a host uvicorn against the same database as the
container. Startup marks unfinished runs as failed, and the job queue lives inside that process,
so a second API would fail the first one's running jobs.

## Settings

Every backend variable is documented in [.env.example](.env.example). The ones you are most likely
to change:

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | none | Needed by the judge and `extract-specs` |
| `LLM_MODEL` | `gemini-3.8-flash` | Model id at the provider |
| `LLM_JUDGE_ENABLED` | `true` | API runs send free-text lines to the LLM judge (CLI: `match-tender --judge`). Without `GEMINI_API_KEY` runs are rules only, with a warning in the log; `false` turns the judge off |
| `LLM_JUDGE_MAX_CALLS` | `35` | Most judge calls per tender; further lines stay `unsupported`. Caps the cost (about Rs 0.10 a call, so under Rs 5 a tender) |
| `LLM_JUDGE_MAX_PASSAGES` | `5` | Best catalogue passages sent to the judge per requirement |
| `LLM_JUDGE_THINKING_LEVEL` | `low` | Gemini thinking level of judge calls; `low` adds no billed thinking tokens (empty: model default) |
| `LLM_JUDGE_LOW_SCORE_HITS` | `3` | When the right product's passages all score below `RERANK_MIN_SCORE`, this many go to the judge (0 turns it off) |
| `RANKING_MIN_COVERAGE_PCT` | `50` | Below this, a manufacturer is listed last as low coverage |
| `TENDER_UPLOAD_MAX_MB` | `50` | Largest `bid.pdf` the API accepts |

Judge answers are cached in the `judge_answers` table, keyed by a hash of the prompt version,
model and full prompt (requirement text and passage chunk ids). A rerun gives the same answers and
calls nothing; failed calls are not cached. Empty the table to ask again.

Read by Compose from the shell or a `.env` next to `docker-compose.yml`, not from `backend/.env`:

| Variable | Default | Purpose |
|---|---|---|
| `WEB_PORT` / `API_PORT` | `8080` / `8000` | Host ports |
| `POSTGRES_PORT` | `5433` | Host port of the database |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `tender` / `tender` / `tender_matcher` | Database login; change them outside local use |
| `HF_CACHE_DIR` | Docker volume | Existing Hugging Face cache folder |

Compose sets `DATABASE_URL`, the data folders and the Tesseract paths for the container, and these
win over `backend/.env`.

## Example output

From tender GEM/2026/B/8119613 against four manufacturers. All four are marked low coverage, so
the percentages rest on part of the tender only:

| Rank | Manufacturer | Compatibility | Coverage | Decided | Full / partial / missing |
|---|---|---|---|---|---|
| 1 | LnT_Valves | 71.4% | 38.9% | 14 of 36 | 6 / 8 / 0 |
| 2 | Delval | 50.0% | 27.8% | 10 of 36 | 2 / 6 / 2 |
| 3 | KSB | none | 0% | 0 of 36 | no catalogue for this product |
| 4 | Kirloskar Brothers | none | 0% | 0 of 36 | no catalogue for this product |

One result, shortened:

```json
{
  "requirement_id": "10.8",
  "status": "full_match",
  "decided_by": "rule",
  "evidence": [{
    "document": "delval_series_44_49_hpbfv_spec.pdf",
    "page_number": 2,
    "label": "Body",
    "value": "ASTM A216 Gr WCB / ASTM A351 Gr CF8 / ASTM A351 Gr CF8M",
    "verified": true
  }]
}
```

| Status | Meaning | Score |
|---|---|---|
| `full_match` | Evidence satisfies all conditions | 1.0 |
| `partial_match` | Some conditions met, only some models, optional accessory, or near miss | 0.5 |
| `missing_capability` | The catalogue covers the attribute and the value fails | 0.0 |
| `unsupported` | Not technically verifiable, or no evidence in the catalogues | left out |
| `not_applicable` | Nothing to compare (buyer details, bid dates, procedure) | left out |

Compatibility % is the total score divided by the decided requirements. Coverage % is the decided
requirements divided by all requirements except `not_applicable`.

## Develop without Docker

```sh
docker compose up -d db
cd backend
python -m venv .venv                # Python 3.14; activate it
pip install -e ".[dev]"
cp ../.env.example .env
alembic upgrade head
uvicorn app.main:create_app --factory --port 8000 --workers 1
```

```sh
cd frontend
npm ci
npm run dev                         # http://localhost:5173, forwards /api to :8000
```

Checks:

```sh
cd backend  && ruff check . && mypy && pytest      # DB tests skip if the db container is down
cd frontend && npm run lint && npm run typecheck && npm test
```

Accuracy against the hand labels in `eval/gold/` (writes `eval/results.md` and `eval/diffs/`):

```sh
cd backend
python ../eval/run_eval.py            # rules only
python ../eval/run_eval.py --judge    # also rules + LLM judge; calls Gemini
```

`backend/constraints.txt` pins the versions the image installs.

## How good it is

**A first-pass assistant with checked evidence.** Every decision cites a page and a quote found on
that page. A person reviews the `unsupported` and `partial_match` rows. It is not fit for automatic
bidder ranking.

Against 101 hand-labelled rows on 5 tenders ([eval/results.md](eval/results.md)), final run of
2026-11-11 (judge on gold-labelled pairs only, 43 new Gemini calls):

| Measure | Rules | Rules + judge |
|---|---|---|
| Coverage | 52% (53/101) | 58% (59/101) |
| Decided accuracy | 79% (42/53) | 78% (46/59) |
| `full_match` precision | 95% (37/39) | 93% (40/43) |
| Stronger than label | 4 | 5 |
| Citation accuracy | 69% (29/42) | 70% (32/46) |

Details, the step by step results and their causes are in the [write-up](docs/writeup.md).

## Known limits

- 6 of 19 tenders give wrong reports; the extraction warning flags 9 of 19 and sends their results
  to review.
- Scanned pages are partly fixed: two-column scanned tables are rebuilt from OCR word boxes, other
  layouts (8157758, 8167974) keep plain OCR text. 7959356's pump table came out as one merged line.
- Only 101 labelled rows on 5 tenders, so one row moves accuracy by about 2 points.
- The judge varies by about 1 labelled row between fresh runs; the cache only fixes reruns. It can
  over-infer from free text ("prevents spring failure" read as a spring match).
- A range title naming several products in a form the ontology misses (L&T API 600 p16) leaves
  that page under the previous product heading.
- Spec fact extraction has never run on real catalogues, so `spec_facts` is empty.
- No Redis queue: jobs run inside the API process, so a restart fails any run in progress.
- Files are not in Postgres; they stay in the mounted `data/` folder.
- Docker images are not published; build them with `docker compose up --build`.
- Duncan and Intervalve Poonawala catalogues are missing.

## Next steps

1. Label more tenders, especially pumps.
2. Run spec fact extraction on the real catalogues.
3. Gemini vision extraction for the flagged tenders and scanned pages.
