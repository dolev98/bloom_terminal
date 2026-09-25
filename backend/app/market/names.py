"""Human-readable names for tickers: SEC company map for US listings + a curated list for indices, FX,
futures, crypto and Tel Aviv listings (SEC does not cover those)."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

CURATED: dict[str, tuple[str, str]] = {
    # ticker: (name, kind)
    "^GSPC": ("S&P 500 index", "index"),
    "^NDX": ("Nasdaq-100 index", "index"),
    "^DJI": ("Dow Jones Industrial Average", "index"),
    "^RUT": ("Russell 2000 index", "index"),
    "^VIX": ("VIX volatility index", "index"),
    "^SOX": ("PHLX Semiconductor index", "index"),
    "^MOVE": ("MOVE bond volatility index", "index"),
    "^TA125.TA": ("TA-125 index", "index"),
    "TA35.TA": ("TA-35 index", "index"),
    "SPY": ("SPDR S&P 500 ETF", "etf"),
    "QQQ": ("Invesco Nasdaq-100 ETF", "etf"),
    "IWM": ("iShares Russell 2000 ETF", "etf"),
    "DIA": ("SPDR Dow Jones ETF", "etf"),
    "TLT": ("iShares 20+ Year Treasury ETF", "etf"),
    "HYG": ("iShares High Yield Corporate Bond ETF", "etf"),
    "LQD": ("iShares Investment Grade Corporate Bond ETF", "etf"),
    "GLD": ("SPDR Gold ETF", "etf"),
    "USO": ("US Oil Fund", "etf"),
    "UUP": ("Invesco US Dollar Index ETF", "etf"),
    "EEM": ("iShares MSCI Emerging Markets ETF", "etf"),
    "XLE": ("Energy Select Sector ETF", "etf"),
    "XLF": ("Financial Select Sector ETF", "etf"),
    "XLK": ("Technology Select Sector ETF", "etf"),
    "XLU": ("Utilities Select Sector ETF", "etf"),
    "XLRE": ("Real Estate Select Sector ETF", "etf"),
    "ILS=X": ("US dollar / Israeli shekel", "fx"),
    "EURUSD=X": ("Euro / US dollar", "fx"),
    "JPY=X": ("US dollar / Japanese yen", "fx"),
    "DX-Y.NYB": ("US dollar index (DXY)", "fx"),
    "GC=F": ("Gold futures", "commodity"),
    "SI=F": ("Silver futures", "commodity"),
    "HG=F": ("Copper futures", "commodity"),
    "CL=F": ("WTI crude oil futures", "commodity"),
    "BZ=F": ("Brent crude oil futures", "commodity"),
    "NG=F": ("Natural gas futures", "commodity"),
    "BTC-USD": ("Bitcoin", "crypto"),
    "ETH-USD": ("Ether", "crypto"),
    "TEVA.TA": ("Teva Pharmaceutical (Tel Aviv)", "stock"),
    "NICE.TA": ("NICE Ltd (Tel Aviv)", "stock"),
    "ESLT.TA": ("Elbit Systems (Tel Aviv)", "stock"),
    "ICL.TA": ("ICL Group (Tel Aviv)", "stock"),
    "LUMI.TA": ("Bank Leumi (Tel Aviv)", "stock"),
    "POLI.TA": ("Bank Hapoalim (Tel Aviv)", "stock"),
    "DSCT.TA": ("Israel Discount Bank (Tel Aviv)", "stock"),
    "MZTF.TA": ("Mizrahi Tefahot Bank (Tel Aviv)", "stock"),
    "BEZQ.TA": ("Bezeq (Tel Aviv)", "stock"),
    "AZRG.TA": ("Azrieli Group (Tel Aviv)", "stock"),
    "TSEM.TA": ("Tower Semiconductor (Tel Aviv)", "stock"),
    "NVMI.TA": ("Nova (Tel Aviv)", "stock"),
    "PHOE.TA": ("Phoenix Holdings (Tel Aviv)", "stock"),
}

# Companies listed in Tel Aviv and in the US (same shares). The US line files with the SEC, so its financial
# statements apply to both. Kept explicit: matching on symbols alone would link unrelated companies.
DUAL_LISTINGS: dict[str, str] = {
    "TEVA.TA": "TEVA",
    "NICE.TA": "NICE",
    "ESLT.TA": "ESLT",
    "ICL.TA": "ICL",
    "TSEM.TA": "TSEM",
    "NVMI.TA": "NVMI",
}
_DUAL_REVERSE = {us: ta for ta, us in DUAL_LISTINGS.items()}


def other_listing(ticker: str) -> str | None:
    """The same company's listing on the other exchange (TEVA.TA ↔ TEVA), if it has one."""
    t = ticker.upper()
    return DUAL_LISTINGS.get(t) or _DUAL_REVERSE.get(t)


def kind_of(ticker: str) -> str:
    t = ticker.upper()
    if t in CURATED:
        return CURATED[t][1]
    if t.startswith("^"):
        return "index"
    if t.endswith("=X"):
        return "fx"
    if t.endswith("=F"):
        return "commodity"
    if t.endswith("-USD"):
        return "crypto"
    return "stock"


async def _sec_map() -> dict[str, dict]:
    try:
        from app.data.registry import get_registry

        reg = get_registry()
        edgar = reg.get("edgar")
        if not reg.is_usable(edgar)[0]:
            return {}
        return await edgar.ticker_map()  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover - network
        log.debug("sec ticker map unavailable: %s", e)
        return {}


def _title(name: str) -> str:
    # SEC titles are often ALL CAPS ("APPLE INC.") — soften for display
    if name.isupper():
        small = {
            "inc",
            "inc.",
            "corp",
            "corp.",
            "ltd",
            "ltd.",
            "plc",
            "co",
            "co.",
            "llc",
            "n.v.",
            "s.a.",
            "ag",
            "se",
        }
        return " ".join(w.capitalize() if w.lower() not in small else w.capitalize() for w in name.split())
    return name


async def profile(ticker: str) -> dict:
    out = await _profile(ticker)
    if other := other_listing(ticker):
        out["other_listing"] = other
    return out


async def _profile(ticker: str) -> dict:
    t = ticker.upper()
    if t in CURATED:
        name, kind = CURATED[t]
        return {"ticker": t, "name": name, "kind": kind, "currency": "ILS" if t.endswith(".TA") else "USD"}
    m = await _sec_map()
    base = t.split(".")[0]
    if t in m:
        return {
            "ticker": t,
            "name": _title(m[t]["name"]),
            "kind": "stock",
            "currency": "USD",
            "cik": m[t]["cik"],
        }
    if t.endswith(".TA") and base in m:
        return {
            "ticker": t,
            "name": f"{_title(m[base]['name'])} (Tel Aviv)",
            "kind": "stock",
            "currency": "ILS",
        }
    return {"ticker": t, "name": None, "kind": kind_of(t), "currency": "ILS" if t.endswith(".TA") else "USD"}


async def search(q: str, limit: int = 12) -> list[dict]:
    ql = q.strip().lower()
    if not ql:
        return []
    out: list[tuple[int, dict]] = []
    for t, (name, kind) in CURATED.items():
        score = _score(ql, t.lower(), name.lower())
        if score:
            out.append((score, {"ticker": t, "name": name, "kind": kind}))
    for t, v in (await _sec_map()).items():
        score = _score(ql, t.lower(), v["name"].lower())
        if score:
            out.append((score, {"ticker": t, "name": _title(v["name"]), "kind": "stock"}))
    out.sort(key=lambda x: (-x[0], len(x[1]["ticker"])))
    seen, res = set(), []
    for _, r in out:
        if r["ticker"] in seen:
            continue
        seen.add(r["ticker"])
        res.append(r)
        if len(res) >= limit:
            break
    return res


def _score(q: str, ticker: str, name: str) -> int:
    if ticker == q:
        return 100
    if ticker.startswith(q):
        return 80 - len(ticker)
    if name.startswith(q):
        return 60
    if f" {q}" in f" {name}":
        return 40
    return 0
