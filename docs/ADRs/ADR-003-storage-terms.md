# ADR-003: Sources whose terms forbid storage (2026-09-23)

Cboe delayed-quote JSON and Massive market data are display-only. Decision: **view-only** panels, no stored IV history from them.
IV rank/percentile history (v1) comes from yfinance option chains (grey) or a Tradier/ORATS account if the user opts in later.
