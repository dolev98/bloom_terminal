# ADR-002: Licensed vs grey price sources (2026-09-23)

- **US quotes/OHLCV (licensed, free):** Finnhub free tier (real-time quote + websocket ≤50 symbols) and Alpaca Market Data Basic (IEX bars). Alerts evaluate only on licensed quotes with a staleness guard (`quote_age_s`).
- **Non-US indices, FX, futures, TASE (grey):** yfinance behind `license.grey=True`, gated by `prefs.grey_sources_enabled`, throttled (~0.4 req/s, single ticker, cache). Never the sole basis for an alert.
- **Correlation legs:** prefer FRED mirrors (SP500, NASDAQCOM, VIXCLS, DCOILWTICO, DEXUSEU) at T+1.
- **Paid upgrade path (one vendor):** FMP Starter after the day-1 check; EODHD/Massive only if the trial proves TASE/real-time needs.
