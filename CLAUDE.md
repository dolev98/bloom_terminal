# Personal Terminal — developer guide (read fully before editing)

Self-hosted single-user "Bloomberg at home". Backend: FastAPI + APScheduler (one process), SQLite (metadata) + Parquet/DuckDB (time series), polars. Frontend: Vite + React 19 + TS (hash router, one page at a time), Lightweight Charts, ECharts, TanStack Query, zustand, cmdk. Plan (Hebrew): `~/.claude/plans/sequential-soaring-lobster.md`.

## Commands
- Backend tests: `cd backend && uv run pytest -q` (all tests must pass; no network in tests — mock HTTP with `respx`).
- Lint/format: `uv run ruff check backend --fix && uv run ruff format backend` (line length 110).
- Frontend: `cd frontend && npx tsc -b && npm run build`.
- Run: `uv run uvicorn app.main:app --app-dir backend --port 8710` (serves `frontend/dist`).
- Never `git commit` unless the user asks. Never install packages with `uv add`/`npm install` inside a subagent — report the need instead.

## Backend conventions
- Python 3.12, async everywhere. DB access only via `async with session_scope() as s:` (`app.data.store.sqlite`). ORM `Base` from the same module. **New tables**: put ORM classes in `app/<package>/models.py` and register the module in `app/data/store/all_models.py` (that file is the only place `create_all` learns about tables).
- Frames are **polars**. Observation frames have exactly `ts: Datetime(us)` (naive UTC/date) and `value: Float64`. Convert to pandas only at a library boundary (statsmodels/arch) and back.
- Data sources subclass `Provider` (`app/data/providers/base.py`): set `id`, `capabilities`, `rate` (RateSpec), `license` (LicenseSpec; `grey=True` for unofficial sources), `egress="il"` for Israel-geofenced sources, `requires=("<settings field>",)` for API keys. Do HTTP through `app.data.http.get_client()` and wrap requests with `async for attempt in retrying(): with attempt: resp = await client.get(...); raise_for_retry(resp)`. Providers are registered in `app/data/registry.py::build_registry` (the integrator does that; a package may expose a `PROVIDERS` list).
- Every call to a provider from services goes through `registry.call(provider, op, fn, key)` (rate limit + fetch log + grey gate). Series catalog rows: `app/data/catalog/loader.py` (`get_spec`, `upsert_spec`, `list_specs`); series data: `app/data/series_service.py` (`read_series`, `refresh_series`, `write_manual`); OHLCV: `app/market/service.py`.
- Settings: `app.core.config.get_settings()` (env prefix `TERMINAL_`, secrets also from macOS Keychain). Prefs table: `app.data.store.models.Pref`.
- LLM: use `app/llm/client.py` (`llm.parse(schema, system, content, purpose=...)`) — it handles model ids, prompt caching, usage/cost logging and the monthly budget. Default model ids: `claude-sonnet-5` (extraction/summaries), `claude-haiku-4-5` (bulk triage), `claude-opus-5` (deep, rare).
- Alerts/notifications: `app.alerts.channels.telegram.send_telegram(text, ops=False)`.
- Jobs: async functions in `app/jobs/tasks/<name>.py`; registered in `app/jobs/scheduler.py::register_jobs` by the integrator. Jobs must be idempotent and safe to run after laptop sleep.
- Routers: `app/api/routers/<name>.py` with `router = APIRouter(prefix="/api/<name>")`; included in `app/main.py` by the integrator. Return plain JSON-able dicts (datetimes ok — FastAPI serialises them; for WebSocket use `json.dumps(default=str)`).
- Provenance on every stored number: source, source key/concept, filed/as-of date, url/accession, confidence.
- Tests live in `backend/tests/<package>/test_*.py`. Fixtures in `backend/tests/conftest.py`: `db` (fresh SQLite + parquet store, async), `client` (sync TestClient running the real lifespan against a temp data dir), `settings`. Use small synthetic fixtures; mock HTTP with `respx`; never hit the network.

## Frontend conventions (UI redesign, 2026-09-24 — clarity first)
- **Shell**: `src/app/App.tsx` (sidebar + header search + market status). Routing is a hash router: `src/lib/router.ts` (`useRoute`, `navigate(path, query)`, `href()`, `useTitle`). Route table: `src/app/routes.tsx` (pages other than the Dashboard are `lazy(() => import(...))` — add new pages the same way). One page at a time — no dockview.
- **Pages** live in `src/pages/**` and default-export a component taking plain props (e.g. `{ ticker }`), NOT dockview props. Company tabs live in `src/pages/company/<Tab>.tsx` and receive `{ ticker }`; the company header (name, price, change, tabs) is rendered by `src/pages/company/CompanyPage.tsx` — tab bodies must NOT repeat the ticker/price header.
- **UI kit** `src/ui/index.tsx`: `Page` (title/sub/actions), `Card` (title/hint/actions/foot), `Stat`, `Change`, `Price`, `Empty`, `Loading`, `ErrorBox`, `Segmented`, `Tabs`, `Importance`, `Flag`, `Help`. Formatting: `src/lib/format.ts` (`price`, `pct`, `ratioPct`, `money`, `times`, `dateLabel`, `dayLabel`, `timeIL`, `dateTimeIL`, `ago`). Styles: `src/theme/tokens.css` (classes `card`, `stats/stat`, `tiles/tile`, `table` (+`click`, `r`, `total`), `table-wrap`, `tabs`, `chip`/`chip on`, `badge ok|warn|err|info|grey`, `notice warn|err|info`, `empty`, `list`/`list-item`/`li-*`, `grid-2`/`grid-3`, `stack`, `row`, `muted`, `faint`, `up`, `down`, `num`, `ticker`, `segmented`). Charts: `components/charts/EChart.tsx` (ECharts wrapper), `CandleChart.tsx`, `SeriesLineChart.tsx`. Chart colours: use the CSS palette (text #e7eaf0, muted #8a93a3, grid #262d38, accent #f5a524, up #2fbf71, down #f0565a, info #5aa9ff).
- **Clarity rules (mandatory, "no fake clarity")**:
  1. Plain-English labels first, codes second: "US 10-year Treasury yield" with `fred:DGS10` small/muted — never a bare id as the only label. Tickers shown with the company/instrument name.
  2. Sentence-case headings; no ALL-CAPS labels; monospace only for tickers/codes.
  3. Every number has a unit and a date context: one "as of …" line per card/table (not per number). Money in compact form with currency ($391.0B, ₪1.2B). Percent vs ratio explicit.
  4. Don't show internal plumbing on user pages (source ids, provider names, cache/fetch details, job ids) — put provenance in a hover `title` or a small footer ("Source: SEC EDGAR XBRL"). The Status page is where plumbing belongs.
  5. Honest freshness: if data is stale or delayed say so in words ("Delayed quote (Yahoo)", "Last close Sep 23"). Never display a fetch time as if it were a trade time.
  6. Empty states explain why there is no data and offer the one action that fixes it (e.g. button "Load financial statements").
  7. Jargon gets a `Help` tooltip the first time it appears on a page (e.g. FCF, WACC, TTM, z-score).
  8. At most one primary action per card; secondary controls go in a compact toolbar (Segmented for period/range choices).
  9. Defaults should show the most useful view immediately; advanced controls collapsed behind "Options" / "Advanced".
  10. Optional things are not warnings. Only show a warning when something the user asked for cannot work.
- Fetch through `api()` (`src/lib/api.ts`) + TanStack Query; live quotes via `useQuote`/`useQuoteMap` (`src/lib/ws.ts`). Navigate with `navigate()`/`href()` (the old `useUI().openPanel` still works and maps to routes).
- Verify pages against the live API (`curl` the endpoint you render and check field names/units) — never render a field you have not seen in a real response.

## Product rules (from the approved plan)
- Grey sources are gated by `prefs.grey_sources_enabled` and never the sole basis for an alert. Israeli sources need an Israeli IP.
- Licensed quotes (Finnhub) drive alerts; yfinance is a fallback. TASE equities quote in agorot → stored in ILS.
- Statements: US 10-K/10-Q via XBRL; 20-F filers are annual-only in XBRL → 6-K parsing; IFRS via ifrs-full map; TASE-only via Maya PDF → Claude extraction → **manual approval** before use.
- Valuation engine is pluggable (`ValuationModel` protocol, `user_models/` auto-discovery, external CSV/XLSX fair values); alert rules reference a named reference value, not a model.
- UI language English; Hebrew data/labels/search supported.
