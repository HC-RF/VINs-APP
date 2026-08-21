# VIN Decoder

Turn a Vehicle Identification Number into a verified vehicle profile — with every
field carrying its source, its confidence, and an honest answer when sources disagree.

```
5UXKR0C56JL070851  →  2018 BMW X5 xDrive35i
                      3.0L turbocharged inline-6 · 300 hp · AWD · 8-speed automatic
                      18/24/20 mpg · built in Greer, South Carolina
                      Confidence: HIGH · Sources: VIN structure, NHTSA vPIC, spec catalog
                      API cost: $0.00
```

Runs on free data sources out of the box. No API key, no paid account, no trial
credits — decode as many VINs as you like at zero cost.

**Two frontends, one backend.** The decoding stack is a plain Python library, so
it is driven either way with identical behaviour:

| | Command | Best for |
|---|---|---|
| **Streamlit** | `streamlit run streamlit_app.py` | Streamlit Community Cloud — one-click deploy |
| **FastAPI + SPA** | `uvicorn app.main:app` | A real JSON API, custom UI, any container host |

---

## Why this is not just an API wrapper

Most VIN tools call one API and print whatever comes back. That is fine until the
API is wrong, incomplete, or disagrees with another source — and then the tool
quietly publishes a falsehood.

This one is built around three rules:

**1. Every value knows where it came from.** No field is ever stored as a bare
value. Each carries its source, confidence, timestamp, and whether it was read
directly out of the 17 VIN characters or enriched from an external database.

**2. Disagreements are surfaced, never resolved silently.** When two sources
conflict, both values are kept, the record is flagged, and the UI says so. The
higher-confidence value is shown — and labelled as contested.

**3. Missing data stays missing.** A field no source supplied renders as
*"Not available"*. Nothing is estimated, inferred, or filled in to look complete.

A real example the system finds on its own, with no configuration:

> **Data discrepancy detected** — Transmission: NHTSA vPIC reports `Automatic`,
> the specification catalog reports `Dual-Clutch`.

Both are defensible. The 2018 Audi Q5 uses a 7-speed S tronic dual-clutch gearbox;
vPIC records the generic style. A single-source decoder would have picked one and
told you nothing. This one shows you the disagreement and lets you decide.

---

## Quick start

**Requirements:** Python 3.11+. That is the entire list — no Node, no Docker, no
database server needed to run it.

```bash
git clone <your-repo> && cd "VIN App"
```

**Windows (PowerShell)**

```powershell
.\run.ps1
```

**macOS / Linux**

```bash
./run.sh
```

The script creates a virtual environment, installs dependencies, copies
`.env.example` to `.env`, and starts the FastAPI server.

Then open **http://127.0.0.1:8000**.

For the Streamlit frontend instead:

```bash
.venv/Scripts/streamlit run streamlit_app.py     # Windows
.venv/bin/streamlit run streamlit_app.py         # macOS / Linux
```

Then open **http://localhost:8501**. Both read and write the same cache.

<details>
<summary>Manual setup, if you prefer</summary>

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows:  .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env
python -m uvicorn app.main:app --reload
```

</details>

Try it immediately — click **Load sample VINs**, or paste these:

```
WA1ANAFY5J2213924
WBXHT3C38J5K23394
5UXKR0C56JL070851
WBA2J3C53JVA52449
WBA5R7C59KAE82587
WBA4J1C58JBG77203
```

To warm the cache and see a coverage report from the command line:

```bash
.venv/Scripts/python scripts/seed_demo.py
```

---

## What it does

| | |
|---|---|
| **Decode** | One VIN or hundreds, pasted line by line. Format and check-digit validation, automatic uppercasing, duplicate detection. |
| **Verify** | Cross-checks every field across sources and flags conflicts. |
| **Bulk** | Sortable, filterable results table across year, make, model, fuel, engine, cylinders, horsepower, drivetrain. |
| **Compare** | Two to six vehicles side by side, with differing rows highlighted and the leading value marked. |
| **Export** | CSV and Excel. The Excel workbook includes separate sheets for field-level provenance and detected discrepancies. |
| **Cache** | Decoded VINs are stored, so the same vehicle is never paid for twice. |

---

## How a decode works

```
  VIN
   │
   ├─ 1. Validate ──────── 17 chars, legal charset, check digit, model year
   │                       Broken input never reaches a provider.
   │
   ├─ 2. Cache ─────────── Fresh row in `vehicles`? Return it. Zero cost.
   │
   ├─ 3. Free tier ─────── VIN structure  ┐ run concurrently
   │                       NHTSA vPIC     ┘ (batched: 50 VINs per HTTP call)
   │
   ├─ 4. Enrich ────────── Local spec catalog fills what the VIN cannot carry:
   │                       fuel economy, torque, gearbox detail.
   │
   ├─ 5. Escalate ──────── Commercial providers — only if a required field is
   │                       still missing, or `verify` was requested.
   │                       With no API key this step does nothing.
   │
   ├─ 6. Merge ─────────── Field by field. Conflicts reported, not hidden.
   │
   └─ 7. Persist ───────── Cache the record with its full provenance.
```

### How the winner is chosen

When several sources offer a value for the same field:

1. **Higher confidence wins** (HIGH > MEDIUM > LOW).
2. **Then VIN-decoded beats looked-up** — the 17 characters are ground truth.
3. **Then provider priority** — the registry ordering.
4. **Then arrival order**, so results are deterministic.

Whatever loses is kept as an alternative on the field. If it genuinely *conflicts*
rather than merely losing, a discrepancy is raised — and the winning value is
downgraded from HIGH, because a contested value is not a high-confidence one.

Formatting differences are not conflicts. `AWD/All-Wheel Drive` and `AWD` agree.
`BMW Manufacturing Co. (Spartanburg, USA)` and `BMW Manufacturer Corporation /
BMW North America` agree. `300 hp` and `303 hp` agree, within tolerance.
`Gasoline` and `Diesel` do not.

---

## Data providers

Everything implements one interface, `VINDecoderProvider`. The decode service
knows nothing about HTTP, API keys or vendor payload shapes.

| Provider | Type | Cost | Supplies |
|---|---|---|---|
| **VIN structure** (ISO 3779) | Local | Free | Model year, manufacturer, WMI region — decoded directly from the VIN |
| **NHTSA vPIC** | Free API | Free, no key | Year, make, model, trim, engine, fuel, drivetrain, plant, safety equipment |
| **Specification catalog** | Local | Free | Horsepower, torque, transmission detail, EPA fuel economy |
| **Auto.dev** | Commercial | Per call | Higher trim and equipment fidelity — *disabled until a key is set* |

### Adding a commercial provider

Subclass `VINDecoderProvider`, map the vendor payload to canonical field names,
and add one line to `PROVIDER_CLASSES` in `app/providers/registry.py`. Nothing
else in the system changes. `app/providers/autodev.py` is a complete worked
example — adapting it to DataOne, CarsXE, Vehicle Databases or ChromeData means
editing two things in that one file:

```python
# app/providers/registry.py
PROVIDER_CLASSES = (
    LocalVinProvider,
    NhtsaProvider,
    SpecCatalogProvider,
    AutoDevProvider,
    YourNewProvider,      # ← that is the whole integration
)
```

### Cost control

* Free sources always run first.
* Paid providers are called **only** when a required field
  (`year, make, model, fuel, engine_cylinders`) is still missing after the free
  tier — or when the caller explicitly passes `verify: true`.
* A daily ceiling (`MAX_COMMERCIAL_CALLS_PER_DAY`) caps spend; on reaching it the
  system serves free data and says so rather than failing.
* Every VIN is cached, so a repeated lookup costs nothing.
* Spend and cache hit rate are visible in the UI (the database icon in the header)
  and at `GET /api/v1/usage`.

---

## API

Interactive docs at **`/api/docs`**. OpenAPI schema at `/api/openapi.json`.

### `POST /api/v1/decode`

```json
{
  "vins": ["WA1ANAFY5J2213924", "5UXKR0C56JL070851"],
  "refresh": false,
  "verify": false
}
```

`text` is accepted instead of, or alongside, `vins` for raw pasted input.

```json
{
  "vin": "5UXKR0C56JL070851",
  "valid": true,
  "status": "OK",
  "check_digit_valid": true,
  "year": 2018,
  "make": "BMW",
  "model": "X5",
  "trim": "xDrive35i",
  "engine": {
    "displacement_l": 3.0,
    "type": "Turbocharged",
    "configuration": "In-Line",
    "cylinders": 6,
    "horsepower": 300,
    "torque_lb_ft": 300
  },
  "horsepower": 300,
  "fuel": "Gasoline",
  "drivetrain": "AWD",
  "transmission": "Automatic",
  "mpg_city": 18, "mpg_highway": 24, "mpg_combined": 20,
  "manufacturer": "BMW Manufacturing Co. (Spartanburg, USA)",
  "plant_country": "United States (USA)",
  "confidence": { "overall": "HIGH", "make": "HIGH", "horsepower": "MEDIUM" },
  "sources": ["nhtsa_vpic", "spec_catalog", "vin_structure"],
  "discrepancies": [],
  "fields": {
    "horsepower": {
      "field": "horsepower",
      "label": "Horsepower",
      "value": 300,
      "source": "nhtsa_vpic",
      "source_kind": "FREE",
      "confidence": "MEDIUM",
      "origin": "ENRICHED",
      "retrieved_at": "2026-08-21T09:28:28Z",
      "disputed": false,
      "alternatives": [
        { "value": 300, "source": "spec_catalog", "confidence": "MEDIUM" }
      ]
    }
  },
  "total_cost": 0.0
}
```

`origin` is the field the brief cares most about: `VIN_DECODED` means it came out
of the 17 characters; `ENRICHED` means a database supplied it.

### Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/decode/{vin}` | Decode one VIN |
| `GET` | `/api/v1/validate/{vin}` | Validate only — never calls a provider, always free |
| `GET` | `/api/v1/vehicles/recent` | Recently decoded vehicles |
| `GET` | `/api/v1/vehicles/{vin}` | Fetch a cached record |
| `DELETE` | `/api/v1/vehicles/{vin}` | Drop a VIN from the cache |
| `POST` | `/api/v1/export` | CSV or Excel (`{"vins": [...], "format": "csv"}`) |
| `POST` | `/api/v1/compare` | Side-by-side comparison of 2–6 vehicles |
| `GET` | `/api/v1/providers` | Provider status and cost class (never credentials) |
| `GET` | `/api/v1/usage` | Calls, cache hit rate and spend |
| `GET` | `/api/v1/health` | Health check (exempt from rate limiting) |

### Errors

One envelope, always, with a stable machine-readable code:

```json
{
  "error": {
    "code": "TOO_MANY_ITEMS",
    "message": "Too many VINs in one request: 150 supplied, limit is 100.",
    "details": { "limit": 100, "given": 150 }
  }
}
```

Handled explicitly: invalid VIN, wrong length, illegal characters, failing check
digit, unsupported manufacturer, no database result, provider timeout, quota
exceeded, authentication failure, conflicting data, missing specifications,
oversized batches and rate limiting.

An invalid VIN never reaches a provider — it would cost money and always fail.

---

## Database

PostgreSQL in production; SQLite automatically when `DATABASE_URL` is blank, so the
app runs with zero infrastructure. The ORM models, repositories and application
code are identical either way.

| Table | Holds |
|---|---|
| `vehicles` | Merged canonical record per VIN — **this is the cache** |
| `vehicle_specifications` | One row per resolved field with source, confidence, origin, disputed flag |
| `source_discrepancies` | Recorded disagreements between providers |
| `data_sources` | Registered providers, seeded from the registry at startup |
| `provider_responses` | Raw payloads, so a normalization fix can be replayed without re-paying |
| `vin_lookups` | Audit log of every request, cached or not |
| `api_usage` | Per-provider, per-day call and spend counters |

To use PostgreSQL:

```bash
createdb vin_decoder
psql -d vin_decoder -f app/db/schema_postgres.sql
```

```ini
DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/vin_decoder
```

The SQL file is the canonical DDL — it adds what the portable ORM cannot express:
JSONB columns, GIN indexes, partial indexes, CHECK constraints, and four
operational views (`v_field_coverage`, `v_provider_health`, `v_cache_effectiveness`,
`v_open_discrepancies`).

`v_field_coverage` answers the question that decides whether to buy a commercial
API at all: *which fields does the free tier actually fail to supply?*

---

## Configuration

Copy `.env.example` to `.env`. Every setting is documented there. The ones that
matter most:

| Variable | Default | Effect |
|---|---|---|
| `DATABASE_URL` | *(blank)* | Blank uses SQLite. Set for PostgreSQL. |
| `CACHE_TTL_HOURS` | `720` | Cache lifetime; `0` never expires |
| `PREFER_FREE_PROVIDERS` | `true` | Call paid APIs only when free data is insufficient |
| `MAX_COMMERCIAL_CALLS_PER_DAY` | `250` | Hard spend ceiling; `0` unlimited |
| `MAX_VINS_PER_REQUEST` | `100` | Batch size limit |
| `RATE_LIMIT_REQUESTS` | `60` | Per `RATE_LIMIT_WINDOW_SECONDS`, per client IP |
| `AUTODEV_API_KEY` | *(blank)* | Blank keeps the commercial provider disabled |

### Security

* **API keys never reach the frontend.** Credentials are read from settings
  server-side and attached to outbound requests only. `GET /api/v1/providers`
  returns names, availability and cost class — never a secret. A test asserts this.
* **All input is validated and sanitized.** VINs are charset-restricted before
  they touch a query or an outbound URL; request bodies are Pydantic-validated;
  batch sizes are capped.
* **All rendered output is escaped.** Every dynamic string in the frontend passes
  through `esc()` before insertion.
* **Rate limiting** is applied per client IP on `/api/` routes. `X-Forwarded-For`
  is trusted only in production, where a proxy sets it — otherwise any client
  could spoof its identity to bypass the limit.
* **Internal errors are not leaked.** Unhandled exceptions log the detail and
  return a generic message.

---

## Tests

```bash
.venv/Scripts/python -m pytest        # Windows
./run.sh test                         # macOS / Linux
```

**316 tests, no network access required.** NHTSA tests run against a captured
vPIC payload, so the normalization contract is pinned without depending on a live
service.

| File | Covers |
|---|---|
| `test_vin_validation.py` | Check digit (including which corruptions the standard provably cannot catch), charset, length, model-year cycle disambiguation, WMI/country tables, list parsing and deduplication |
| `test_normalize.py` | Placeholder rejection, numeric parsing, canonicalization, conflict-vs-formatting discrimination |
| `test_merge.py` | Selection order, discrepancy reporting, confidence roll-up |
| `test_providers.py` | Payload mapping, catalog matching, failure isolation, credential safety |
| `test_api.py` | Every endpoint, error paths, caching, cost policy, rate limiting, export integrity |

Two of these tests exist because they caught real bugs during development: one
where `"Compressed Natural Gas (CNG)"` was classified as **Gasoline** (the pattern
`\bgas\b` matched "Natural Gas"), and one where a cache hit reported the *original*
decode's API cost, overstating what the request actually spent.

---

## Project layout

```
app/
├── main.py                     FastAPI app factory
├── config.py                   Settings; the only place credentials live
├── api/v1/router.py            All HTTP endpoints
├── core/
│   ├── errors.py               Uniform error envelope
│   └── rate_limit.py           Sliding-window limiter
├── vin/
│   ├── validate.py             Structure, check digit, direct decoding
│   ├── wmi.py                  Manufacturer and country tables
│   └── year.py                 Model-year cycle decoding
├── providers/
│   ├── base.py                 VINDecoderProvider interface
│   ├── registry.py             The one place providers are registered
│   ├── local_vin.py            Free · the VIN itself
│   ├── nhtsa.py                Free · NHTSA vPIC, with batch support
│   ├── spec_catalog.py         Free · local specification data
│   ├── autodev.py              Commercial · worked example, key-gated
│   └── data/spec_catalog.json  Curated specifications
├── services/
│   ├── decode_service.py       Orchestration, caching, cost policy
│   ├── merge.py                Field resolution and discrepancy detection
│   ├── normalize.py            Junk rejection and canonicalization
│   ├── compare_service.py      Comparison matrix
│   └── export_service.py       CSV and Excel
├── db/
│   ├── models.py               SQLAlchemy models
│   ├── repository.py           Cache, provenance, usage accounting
│   └── schema_postgres.sql     Canonical PostgreSQL DDL
└── static/                     SPA frontend (ES modules, no build step)

streamlit_app.py                Streamlit frontend — the Streamlit Cloud entrypoint
.streamlit/
├── config.toml                 Theme and server settings
└── secrets.toml.example        Secrets template (the real file is gitignored)
scripts/seed_demo.py            Warm the cache and print a coverage report
```

---

## Deployment

### Streamlit Community Cloud

Streamlit Cloud runs `streamlit run <script>` — it does **not** run ASGI servers,
so the FastAPI app cannot be deployed there. `streamlit_app.py` exists for this:
it calls `DecodeService` in-process, with no HTTP hop, so both frontends share
one backend and behave identically.

1. Push this repository to GitHub.
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app**.
3. Select the repository, branch `main`, and set **Main file path** to
   `streamlit_app.py`.
4. Under **Advanced settings**, choose **Python 3.11 or newer** — the codebase
   uses `datetime.UTC` and PEP 604 unions.
5. Deploy. Dependencies install from `requirements.txt` automatically.

**Optional secrets.** The app runs at $0.00 with no configuration. To add any,
use **Manage app → Settings → Secrets** and paste in TOML form —
`.streamlit/secrets.toml.example` is the template. Keys are bridged to
environment variables automatically, so the names match `.env.example`:

```toml
DATABASE_URL = "postgresql+psycopg://user:pass@host:5432/vin_decoder"
```

> **The SQLite cache is ephemeral on Streamlit Cloud.** The container filesystem
> resets whenever the app restarts or redeploys, so decoded VINs are re-fetched
> after a restart. That costs nothing on free providers, but set `DATABASE_URL`
> to a managed PostgreSQL instance (Neon, Supabase and Railway all have free
> tiers) if you want caching to survive — and you must, if you ever enable a
> paid provider.

Errors are redacted in the browser (`showErrorDetails = false` in
`.streamlit/config.toml`) so internals are not exposed publicly. Full tracebacks
are in **Manage app → Logs**.

### FastAPI, anywhere else

The app serves its own frontend, so it deploys as a single service.

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Works as-is on Render, Railway or Fly.io. Set `ENVIRONMENT=production`,
`DATABASE_URL` to a managed PostgreSQL instance, and `CORS_ORIGINS` to your
domain. Behind a proxy, production mode trusts `X-Forwarded-For` for rate
limiting.

For multiple workers or replicas, replace the in-process
`SlidingWindowLimiter` in `app/core/rate_limit.py` with a Redis-backed counter —
the middleware does not care which it talks to.

### Dependencies are split by deployment target

| File | Installs | Use for |
|---|---|---|
| `requirements.txt` | Streamlit + the decoding stack only | **Streamlit Cloud** (it installs from this file automatically) |
| `requirements-api.txt` | the above + FastAPI, uvicorn, psycopg | `uvicorn app.main:app` |
| `requirements-dev.txt` | the above + pytest | local development, running the tests |

```bash
pip install -r requirements-dev.txt      # everything, for local work
```

Two reasons this is split rather than one file:

**Streamlit Cloud should not install what it never imports.** `streamlit_app.py`
reaches only `streamlit`, `pandas`, `pydantic`, `pydantic-settings`, `httpx`,
`sqlalchemy` and `openpyxl`. Shipping FastAPI, uvicorn and pytest to the cloud
adds failure modes for packages the app does not use — a build failure in any
one of them takes down the whole deploy.

**`requirements.txt` uses ranges, not exact pins.** Streamlit Cloud picks the
Python version, and an exact pin with no wheel for that interpreter fails the
install outright. `psycopg-binary` in particular publishes **no source
distribution**, so on a Python version it has not built for yet, pip cannot fall
back to compiling — it just fails. That is why PostgreSQL support is commented
out by default; uncomment it in `requirements.txt` when you set `DATABASE_URL`.

One constraint to preserve if you edit `requirements-api.txt`: Streamlit 1.62
requires `starlette` 1.x, which FastAPI only supports from **0.116** onward.
Pinning FastAPI below that fails at import with
`Router.__init__() got an unexpected keyword argument 'on_startup'`.

---

## Known limits

Stated plainly, because the point of this application is not overstating what it
knows:

* **The specification catalog is a seed dataset**, not a complete database. It
  covers the sample VINs and around 35 common models. An unmatched vehicle
  returns no specification data rather than an approximation — extend
  `app/providers/data/spec_catalog.json`, or register a commercial provider.
* **NHTSA vPIC is strongest for the North American market.** Vehicles never sold
  in the US decode less completely.
* **Check digits are a North American requirement.** Imported vehicles often fail
  it legitimately, so a failure is a warning, not a rejection.
* **EPA fuel economy comes only from the local catalog** — vPIC does not carry it.
* **The Auto.dev provider is untested against the live vendor.** It is written
  from their documented response shape and gated off by default; verify the field
  mapping against a real response before relying on it in production.
