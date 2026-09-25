"""Unit-root diagnostics (ADF + KPSS via arch) and the Granger–Newbold spurious-regression check. Pure numpy."""

from __future__ import annotations

import math
import warnings

import numpy as np
import polars as pl

MIN_OBS = 20


def _arr(x) -> np.ndarray:
    if isinstance(x, pl.Series):
        x = x.to_numpy()
    elif isinstance(x, pl.DataFrame):
        x = x["value"].to_numpy()
    arr = np.asarray(x, dtype=float)
    return arr[np.isfinite(arr)]


def _finite(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def adf_kpss(x, alpha: float = 0.05) -> dict:
    """ADF (H0: unit root) + KPSS (H0: stationary) → verdict 'stationary' | 'I(1)-like' | 'ambiguous'."""
    arr = _arr(x)
    n = int(arr.size)
    out: dict = {
        "n": n,
        "adf_stat": None,
        "adf_p": None,
        "kpss_stat": None,
        "kpss_p": None,
        "verdict": "insufficient",
    }
    if n < MIN_OBS or np.std(arr) == 0:
        return out
    from arch.unitroot import ADF, KPSS

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            adf = ADF(arr, trend="c")
            out["adf_stat"], out["adf_p"] = _finite(adf.stat), _finite(adf.pvalue)
        except Exception:
            pass
        try:
            kp = KPSS(arr, trend="c")
            out["kpss_stat"], out["kpss_p"] = _finite(kp.stat), _finite(kp.pvalue)
        except Exception:
            pass
    adf_p, kpss_p = out["adf_p"], out["kpss_p"]
    if adf_p is None or kpss_p is None:
        out["verdict"] = "ambiguous"
    elif adf_p < alpha and kpss_p >= alpha:
        out["verdict"] = "stationary"
    elif adf_p >= alpha and kpss_p < alpha:
        out["verdict"] = "I(1)-like"
    else:
        out["verdict"] = "ambiguous"
    return out


def durbin_watson(resid: np.ndarray) -> float | None:
    resid = np.asarray(resid, dtype=float)
    if resid.size < 3:
        return None
    denom = float(np.sum(resid**2))
    if denom == 0:
        return None
    return float(np.sum(np.diff(resid) ** 2) / denom)


def granger_newbold_warning(a, b) -> dict:
    """Level-on-level OLS of B on A. Granger & Newbold (1974): R² > Durbin–Watson flags a likely spurious regression."""
    x, y = _arr(a), _arr(b)
    n = min(x.size, y.size)
    x, y = x[:n], y[:n]
    out: dict = {"n": int(n), "r2": None, "dw": None, "spurious": False, "message": None}
    if n < MIN_OBS or np.std(x) == 0 or np.std(y) == 0:
        return out
    X = np.column_stack([np.ones(n), x])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    ss_res = float(np.sum(resid**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    dw = durbin_watson(resid)
    out["r2"], out["dw"] = round(r2, 4), (round(dw, 4) if dw is not None else None)
    if dw is not None and r2 > dw:
        out["spurious"] = True
        out["message"] = (
            f"Levels regression has R²={r2:.2f} > DW={dw:.2f}: likely spurious (Granger–Newbold). Use differences/returns or test cointegration."
        )
    return out
