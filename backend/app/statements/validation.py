"""Arithmetic validation for parsed / LLM-extracted statements (pure).

Checks: assets = liabilities + equity (±0.5%), gross_profit = revenue - cost_of_revenue (±0.5%),
net_change_in_cash = cfo + cfi + cff + fx (±1%), and consistency of comparatives with facts already stored.
"""

from __future__ import annotations

BS_TOL = 0.005
GP_TOL = 0.005
CF_TOL = 0.01
COMPARATIVE_TOL = 0.01
COMPARATIVE_FIELDS = ("revenue", "net_income", "total_assets", "total_equity", "cfo")


def _rel(lhs: float, rhs: float) -> float:
    base = max(abs(lhs), abs(rhs), 1.0)
    return abs(lhs - rhs) / base


def _check(name: str, lhs: float, rhs: float, tol: float, note: str = "") -> dict:
    diff = _rel(lhs, rhs)
    return {
        "name": name,
        "ok": diff <= tol,
        "lhs": lhs,
        "rhs": rhs,
        "diff_pct": round(diff * 100, 3),
        "tol_pct": tol * 100,
        "note": note,
    }


def validate_period(v: dict[str, float | None]) -> dict:
    """Run every check whose inputs are present. `passed` requires >=1 check and all of them OK."""
    checks: list[dict] = []
    a, l_, e = v.get("total_assets"), v.get("total_liabilities"), v.get("total_equity")
    if a is not None and l_ is not None and e is not None:
        c = _check("balance_sheet", a, l_ + e, BS_TOL, "assets = liabilities + equity")
        mi = v.get("minority_interest")
        if not c["ok"] and mi:
            c2 = _check(
                "balance_sheet", a, l_ + e + mi, BS_TOL, "assets = liabilities + equity + minority interest"
            )
            if c2["ok"]:
                c = c2
        checks.append(c)
    r, cogs, gp = v.get("revenue"), v.get("cost_of_revenue"), v.get("gross_profit")
    if r is not None and cogs is not None and gp is not None:
        checks.append(
            _check("gross_profit", gp, r - cogs, GP_TOL, "gross_profit = revenue - cost_of_revenue")
        )
    cfo, cfi, cff, chg = v.get("cfo"), v.get("cfi"), v.get("cff"), v.get("net_change_in_cash")
    if None not in (cfo, cfi, cff, chg):
        fx = v.get("fx_effect") or 0.0
        checks.append(
            _check(
                "cash_flow", chg, cfo + cfi + cff + fx, CF_TOL, "net_change_in_cash = cfo + cfi + cff + fx"
            )
        )  # type: ignore[operator]
    return {
        "checks": checks,
        "n_checks": len(checks),
        "passed": bool(checks) and all(c["ok"] for c in checks),
    }


def compare_with_existing(
    new: dict[str, float | None], existing: dict[str, float | None], tol: float = COMPARATIVE_TOL
) -> dict | None:
    """Comparative column vs already-stored values for the same period (None when nothing overlaps)."""
    diffs = []
    for f in COMPARATIVE_FIELDS:
        a, b = new.get(f), existing.get(f)
        if a is None or b is None:
            continue
        diffs.append((f, _rel(a, b)))
    if not diffs:
        return None
    worst = max(diffs, key=lambda t: t[1])
    return {
        "name": "comparatives",
        "ok": all(d <= tol for _, d in diffs),
        "lhs": new.get(worst[0]),
        "rhs": existing.get(worst[0]),
        "diff_pct": round(worst[1] * 100, 3),
        "tol_pct": tol * 100,
        "note": f"worst field: {worst[0]} ({len(diffs)} compared)",
    }


def validate_extraction(periods: list[dict], existing_by_end: dict | None = None) -> dict:
    """periods: [{"period_end": iso, "values": {...}}]. Returns per-period results and an overall verdict."""
    per: list[dict] = []
    all_ok = True
    any_check = False
    for p in periods:
        res = validate_period(p["values"])
        ex = (existing_by_end or {}).get(p["period_end"])
        if ex:
            cmp_ = compare_with_existing(p["values"], ex)
            if cmp_:
                res["checks"].append(cmp_)
                res["n_checks"] += 1
                res["passed"] = res["passed"] and cmp_["ok"]
        any_check = any_check or res["n_checks"] > 0
        all_ok = all_ok and (res["passed"] or res["n_checks"] == 0)
        per.append({"period_end": p["period_end"], **res})
    return {"periods": per, "passed": any_check and all_ok}
