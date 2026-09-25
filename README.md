# Personal Terminal

Self-hosted, single-user "Bloomberg at home": one local web app that centralizes prices, financial statements,
macro series, an economic calendar, news, correlations and a pluggable valuation engine with upside alerts.

Plan: `~/.claude/plans/sequential-soaring-lobster.md` (Hebrew). Status: **MVP complete (M0–M6)** — live quotes & charts, watchlists, notes, series catalog, financial statements (XBRL / 6-K / IFRS / Maya PDF→Claude with approval), correlation lab, pluggable valuation engine + upside alerts (Telegram), economic & corporate calendar, macro dashboards, news pipeline with Claude summaries, morning brief, status page, nightly backups, Alembic migrations.

## Pages

One page at a time, chosen from the sidebar or the search box at the top (`⌘K` or `/`). Addresses are plain hash URLs, so any page can be bookmarked.

| Page | Address | What it shows |
|---|---|---|
| Dashboard | `#/` | Market tiles (US, Israel, currencies/commodities/crypto) with honest freshness (real-time vs delayed), key events today & tomorrow, watchlist movers, rates & currency, important news, valuation upside |
| Watchlist | `#/watchlist` | Price, day change, 30-day trend, next earnings and price status per ticker; add by name or ticker |
| Company | `#/company/AAPL/overview` | Tabs: Overview (performance, key figures), Chart (candles/line, indicators, compare, CSV), Financials (FY/Q/TTM statements, ratios, charts, PDF upload for Tel Aviv–only companies with approval), Valuation (DCF + other methods, assumptions you can override, sensitivity, upside alert), News, Notes. Dual-listed Tel Aviv shares link to their US listing (e.g. `TEVA.TA` ↔ `TEVA`) |
| Calendar | `#/calendar` | Week / month / central banks; filter by country, importance and type; Israel and New York times; ICS export |
| Macro by country | `#/macro/US` | Latest official figures by theme, economic-regime chart (with the indicators behind it), next releases, cross-country comparison |
| News | `#/news` | Stories about your watchlist, grouped by event, important-only by default |
| Correlations | `#/correlation` | Compare two series (plain-English verdict, rolling correlation, scatter; tests under Options), correlation matrix, related-series finder, 60 seeded pairs |
| Notes / Alerts | `#/notes`, `#/alerts` | Research notes (Hebrew search); alert rules on fair-value upside, prices and data series, sent to Telegram |
| Data catalog / Series | `#/data`, `#/series/fred:DGS10` | Every stored data series, freshness, add by URL / CSV / formula |
| Status / Settings | `#/status`, `#/settings` | Data sources, jobs, LLM spend, backups; API keys (macOS Keychain) |

Morning brief: `GET /api/brief/preview`, sent to Telegram at 06:30 IL (or after wake). Migrations: `cd backend && uv run alembic upgrade head` (autogenerate with `ALEMBIC_URL=sqlite+aiosqlite:////tmp/x.sqlite uv run alembic revision --autogenerate -m ...`).

## Run

```bash
# backend (FastAPI + scheduler) on http://127.0.0.1:8710 — serves frontend/dist when built
uv run uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8710 --reload

# frontend dev server (Vite, proxies /api to the backend) on http://localhost:5173
npm run dev --prefix frontend

# production-ish: build once, then only the backend is needed
npm run build --prefix frontend
```

## Configure

Copy `.env.example` to `.env` **or** enter keys on the Settings page (stored in macOS Keychain, service `maor-terminal`).

| Key | Needed for | Where |
|---|---|---|
| `TERMINAL_SEC_USER_AGENT` = `"Name email"` | SEC EDGAR (mandatory) | — |
| `TERMINAL_FRED_API_KEY` | FRED/ALFRED | https://fredaccount.stlouisfed.org/apikeys |
| `TERMINAL_FINNHUB_API_KEY` | quotes, company news, earnings calendar | https://finnhub.io |
| `TERMINAL_TELEGRAM_BOT_TOKEN` / `_CHAT_ID` / `_OPS_CHAT_ID` | alerts | @BotFather, then "discover chat ids" on the Settings page |

Bank of Israel (SDMX) and Hebcal need no key. Israeli sources (CBS, TASE, Israeli RSS) only work from an Israeli IP.

## Search

`⌘K` or `/` → type a company name or ticker (`apple`, `AAPL`, `TEVA.TA`, `^GSPC`), a data series (`10-year`, `fred:DGS10`) or a page name.

## Tests

```bash
uv run pytest -q
npm run build --prefix frontend
```

## Layout

- `backend/app/data/providers/` — one adapter per source (`Provider` base in `base.py`); `registry.py` wraps calls with rate limits, retries and a fetch log.
- `backend/app/data/catalog/seed_series.yaml` — initial series catalog (edit from the Data catalog page afterwards).
- `backend/app/data/series_service.py` — fetch → validate → Parquet → meta; formula (derived) series.
- `backend/app/jobs/` — APScheduler jobs (refresh stale, daily full, backup, wake catch-up).
- `frontend/src/app/` — shell (sidebar, search, market status) and route table; `src/pages/` — one file per page (company tabs in `src/pages/company/`); `src/ui/` — shared UI kit; `src/theme/tokens.css` — design system. Pages other than the dashboard load on first visit.
- `data/` — SQLite, Parquet lake, derived, cache, PDFs, backups (git-ignored).
