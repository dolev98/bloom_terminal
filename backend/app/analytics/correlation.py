"""Pairwise correlation analytics over aligned frames (`ts, a, b`). Pure numpy/polars; pandas only at the
statsmodels boundary (RollingOLS, OLS-HAC, Engle–Granger, Johansen, Granger causality)."""

from __future__ import annotations

import math
import warnings

import numpy as np
import polars as pl
from scipy.stats import norm, pearsonr, rankdata, spearmanr

MIN_N = 10


# --- helpers -------------------------------------------------------------------------------------
def _pair(a, b) -> tuple[np.ndarray, np.ndarray]:
    x = a.to_numpy() if isinstance(a, pl.Series) else np.asarray(a, dtype=float)
    y = b.to_numpy() if isinstance(b, pl.Series) else np.asarray(b, dtype=float)
    x = x.astype(float)
    y = y.astype(float)
    n = min(x.size, y.size)
    x, y = x[:n], y[:n]
    m = np.isfinite(x) & np.isfinite(y)
    return x[m], y[m]


def _f(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _r(v, d: int = 4) -> float | None:
    f = _f(v)
    return None if f is None else round(f, d)


def lag1_autocorr(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=float)
    if x.size < 3:
        return 0.0
    x = x - x.mean()
    d = float(np.dot(x, x))
    if d == 0:
        return 0.0
    return float(np.dot(x[1:], x[:-1]) / d)


def effective_n(a, b) -> float:
    """Bartlett-style correction for autocorrelated inputs: n_eff = n·(1−ρa·ρb)/(1+ρa·ρb) (lag-1 autocorrelations)."""
    x, y = _pair(a, b)
    n = x.size
    if n < 3:
        return float(n)
    ra, rb = lag1_autocorr(x), lag1_autocorr(y)
    prod = ra * rb
    if prod <= -1:
        return float(n)
    n_eff = n * (1.0 - prod) / (1.0 + prod)
    return float(max(3.0, min(float(n), n_eff)))


def fisher_ci(r: float | None, n: float, alpha: float = 0.05) -> tuple[float | None, float | None]:
    if r is None or n < 4 or abs(r) >= 1:
        return None, None
    z = math.atanh(r)
    se = 1.0 / math.sqrt(n - 3.0)
    q = norm.ppf(1 - alpha / 2)
    return math.tanh(z - q * se), math.tanh(z + q * se)


def fisher_p(r: float | None, n: float) -> float | None:
    if r is None or n < 4 or abs(r) >= 1:
        return None
    z = math.atanh(r) * math.sqrt(n - 3.0)
    return float(2 * (1 - norm.cdf(abs(z))))


def pearson_spearman(a, b) -> dict:
    """Pearson + Spearman with p-values, Fisher-z 95% CI, and an effective-n corrected p-value/CI."""
    x, y = _pair(a, b)
    n = int(x.size)
    out: dict = {
        "n": n,
        "n_eff": None,
        "pearson": None,
        "pearson_p": None,
        "ci": [None, None],
        "spearman": None,
        "spearman_p": None,
        "pearson_p_eff": None,
        "ci_eff": [None, None],
        "r2": None,
    }
    if n < MIN_N or np.std(x) == 0 or np.std(y) == 0:
        return out
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pr = pearsonr(x, y)
        sr = spearmanr(x, y)
    r = _f(pr.statistic)
    n_eff = effective_n(x, y)
    lo, hi = fisher_ci(r, n)
    lo_e, hi_e = fisher_ci(r, n_eff)
    out.update(
        {
            "n_eff": round(n_eff, 1),
            "pearson": _r(r),
            "pearson_p": _r(pr.pvalue, 6),
            "ci": [_r(lo), _r(hi)],
            "spearman": _r(sr.statistic),
            "spearman_p": _r(sr.pvalue, 6),
            "pearson_p_eff": _r(fisher_p(r, n_eff), 6),
            "ci_eff": [_r(lo_e), _r(hi_e)],
            "r2": _r(r * r) if r is not None else None,
        }
    )
    return out


# --- rolling ---------------------------------------------------------------------------------------
def _rolling_spearman(x: np.ndarray, y: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    n = x.size
    out = np.full(n, np.nan)
    if n == 0:
        return out
    # partial leading windows
    for i in range(max(min_periods, 2) - 1, min(window - 1, n)):
        out[i] = _spearman_np(x[: i + 1], y[: i + 1])
    if n >= window:
        wa = np.lib.stride_tricks.sliding_window_view(x, window)
        wb = np.lib.stride_tricks.sliding_window_view(y, window)
        ra = rankdata(wa, axis=1).astype(float)
        rb = rankdata(wb, axis=1).astype(float)
        ra -= ra.mean(axis=1, keepdims=True)
        rb -= rb.mean(axis=1, keepdims=True)
        denom = np.sqrt((ra**2).sum(axis=1) * (rb**2).sum(axis=1))
        with np.errstate(divide="ignore", invalid="ignore"):
            out[window - 1 :] = np.where(denom > 0, (ra * rb).sum(axis=1) / denom, np.nan)
    return out


def _spearman_np(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 3:
        return float("nan")
    rx, ry = rankdata(x), rankdata(y)
    rx = rx - rx.mean()
    ry = ry - ry.mean()
    d = math.sqrt(float((rx**2).sum() * (ry**2).sum()))
    return float((rx * ry).sum() / d) if d > 0 else float("nan")


def rolling_corr(
    df: pl.DataFrame,
    window: int,
    min_periods: int | None = None,
    method: str = "pearson",
    a: str = "a",
    b: str = "b",
) -> pl.DataFrame:
    """Rolling correlation with Fisher-z 95% CI. Returns ts, corr, lo, hi, n."""
    window = max(int(window), 3)
    if min_periods is None:
        min_periods = max(3, int(round(0.8 * window)))
    min_periods = max(3, min(min_periods, window))
    if df.is_empty():
        return pl.DataFrame(
            schema={
                "ts": pl.Datetime("us"),
                "corr": pl.Float64,
                "lo": pl.Float64,
                "hi": pl.Float64,
                "n": pl.Int64,
            }
        )
    if method == "spearman":
        vals = _rolling_spearman(
            df[a].to_numpy().astype(float), df[b].to_numpy().astype(float), window, min_periods
        )
        out = df.select("ts").with_columns(pl.Series("corr", vals))
    else:
        out = df.select(
            pl.col("ts"),
            pl.rolling_corr(pl.col(a), pl.col(b), window_size=window, min_samples=min_periods).alias("corr"),
        )
    idx = pl.int_range(1, out.height + 1, eager=True)
    n = pl.Series("n", np.minimum(idx.to_numpy(), window).astype("int64"))
    out = out.with_columns(n)
    z = pl.col("corr").clip(-0.999999, 0.999999).arctanh()
    se = 1.0 / (pl.col("n").cast(pl.Float64) - 3.0).clip(1e-9).sqrt()
    q = float(norm.ppf(0.975))
    return out.with_columns(((z - q * se).tanh()).alias("lo"), ((z + q * se).tanh()).alias("hi")).select(
        "ts", "corr", "lo", "hi", "n"
    )


def ewma_corr(df: pl.DataFrame, halflife: float, a: str = "a", b: str = "b") -> pl.DataFrame:
    """Exponentially weighted correlation (pandas ewm at the boundary). Returns ts, corr."""
    if df.is_empty():
        return pl.DataFrame(schema={"ts": pl.Datetime("us"), "corr": pl.Float64})
    import pandas as pd

    x = pd.Series(df[a].to_numpy().astype(float))
    y = pd.Series(df[b].to_numpy().astype(float))
    c = x.ewm(halflife=float(halflife), min_periods=3).corr(y).to_numpy()
    return df.select("ts").with_columns(pl.Series("corr", c))


# --- lead / lag --------------------------------------------------------------------------------------
def lead_lag(a, b, max_lag: int = 10) -> dict:
    """corr(A_t, B_{t-k}) for k in -max_lag..max_lag. k>0: B leads A (B's past vs A's present); k<0: A leads B.
    `band` = 2/√n white-noise significance band."""
    x, y = _pair(a, b)
    n = x.size
    rows = []
    best = None
    for k in range(-max_lag, max_lag + 1):
        if k >= 0:
            xa, yb = x[k:], y[: n - k] if k else y
        else:
            xa, yb = x[: n + k], y[-k:]
        m = int(min(xa.size, yb.size))
        c = None
        if m >= MIN_N and np.std(xa) > 0 and np.std(yb) > 0:
            c = _r(np.corrcoef(xa[:m], yb[:m])[0, 1])
        rows.append({"lag": k, "corr": c, "n": m})
        if c is not None and (best is None or abs(c) > abs(best["corr"])):
            best = {"lag": k, "corr": c}
    band = round(2.0 / math.sqrt(n), 4) if n > 0 else None
    return {
        "lags": rows,
        "band": band,
        "best_lag": best["lag"] if best else None,
        "best_corr": best["corr"] if best else None,
        "n": int(n),
    }


# --- regression -------------------------------------------------------------------------------------------
def ols(y, x, hac_lags: int | None = None) -> dict:
    """Full-sample OLS y = α + βx with Newey–West (HAC) t-stats. Returns alpha, beta, t/p, r2, dw, n."""
    yy, xx = _pair(y, x)
    n = int(yy.size)
    out: dict = {
        "n": n,
        "alpha": None,
        "beta": None,
        "t_alpha": None,
        "t_beta": None,
        "p_beta": None,
        "r2": None,
        "dw": None,
        "hac_lags": None,
    }
    if n < MIN_N or np.std(xx) == 0:
        return out
    import statsmodels.api as sm
    from statsmodels.stats.stattools import durbin_watson

    if hac_lags is None:
        hac_lags = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    X = sm.add_constant(xx)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = sm.OLS(yy, X).fit(cov_type="HAC", cov_kwds={"maxlags": max(hac_lags, 0)})
    out.update(
        {
            "alpha": _r(res.params[0], 6),
            "beta": _r(res.params[1], 6),
            "t_alpha": _r(res.tvalues[0], 3),
            "t_beta": _r(res.tvalues[1], 3),
            "p_beta": _r(res.pvalues[1], 6),
            "r2": _r(res.rsquared),
            "dw": _r(durbin_watson(res.resid), 3),
            "hac_lags": int(hac_lags),
        }
    )
    return out


def rolling_beta(df: pl.DataFrame, window: int, y: str = "a", x: str = "b") -> pl.DataFrame:
    """Rolling OLS (statsmodels RollingOLS) of y on x. Returns ts, beta, alpha, r2."""
    empty = pl.DataFrame(
        schema={"ts": pl.Datetime("us"), "beta": pl.Float64, "alpha": pl.Float64, "r2": pl.Float64}
    )
    if df.is_empty() or df.height < 5:
        return empty
    import pandas as pd
    import statsmodels.api as sm
    from statsmodels.regression.rolling import RollingOLS

    window = max(int(window), 5)
    yy = pd.Series(df[y].to_numpy().astype(float))
    X = sm.add_constant(pd.Series(df[x].to_numpy().astype(float), name="x"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = RollingOLS(yy, X, window=min(window, df.height), min_nobs=max(5, int(0.8 * window))).fit()
    params = res.params
    beta = params["x"].to_numpy(dtype=float)
    alpha = params["const"].to_numpy(dtype=float)
    r2 = np.asarray(res.rsquared, dtype=float)
    return df.select("ts").with_columns(
        pl.Series("beta", beta), pl.Series("alpha", alpha), pl.Series("r2", r2)
    )


# --- cointegration ---------------------------------------------------------------------------------------
def half_life(spread: np.ndarray) -> float | None:
    """Half-life of mean reversion from an AR(1) on the spread: Δs_t = a + b·s_{t-1} → −ln2/ln(1+b)."""
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < MIN_N:
        return None
    lag = s[:-1]
    d = np.diff(s)
    X = np.column_stack([np.ones(lag.size), lag])
    coef, *_ = np.linalg.lstsq(X, d, rcond=None)
    b = float(coef[1])
    if b >= 0 or b <= -1:
        return None
    return float(-math.log(2) / math.log(1 + b))


def cointegration(y, x, z_window: int = 60) -> dict:
    """Engle–Granger (statsmodels.coint) + Johansen trace/max-eig; OLS hedge ratio; spread with rolling z-score;
    AR(1) half-life. Inputs are LEVELS."""
    yy, xx = _pair(y, x)
    n = int(yy.size)
    out: dict = {
        "n": n,
        "eg_stat": None,
        "eg_p": None,
        "hedge_ratio": None,
        "intercept": None,
        "spread_adf_p": None,
        "half_life": None,
        "johansen": None,
        "verdict": "insufficient",
        "spread": [],
        "zscore": [],
        "z_last": None,
    }
    if n < 30 or np.std(xx) == 0 or np.std(yy) == 0:
        return out
    from statsmodels.tsa.stattools import adfuller, coint
    from statsmodels.tsa.vector_ar.vecm import coint_johansen

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            stat, p, _ = coint(yy, xx, trend="c", autolag="aic", maxlag=min(12, max(1, n // 20)))
            out["eg_stat"], out["eg_p"] = _r(stat, 4), _r(p, 6)
        except Exception:
            pass
        X = np.column_stack([np.ones(n), xx])
        coef, *_ = np.linalg.lstsq(X, yy, rcond=None)
        alpha, beta = float(coef[0]), float(coef[1])
        spread = yy - alpha - beta * xx
        out["hedge_ratio"], out["intercept"] = round(beta, 6), round(alpha, 6)
        try:
            out["spread_adf_p"] = _r(adfuller(spread, regression="c", autolag="aic")[1], 6)
        except Exception:
            pass
        out["half_life"] = _r(half_life(spread), 2)
        try:
            j = coint_johansen(np.column_stack([yy, xx]), det_order=0, k_ar_diff=1)
            trace = [float(np.real(v)) for v in j.lr1]
            maxeig = [float(np.real(v)) for v in j.lr2]
            rank = 0
            for i in range(2):
                if trace[i] > float(j.cvt[i, 1]):
                    rank = i + 1
                else:
                    break
            ev = np.real(j.evec[:, 0])
            j_beta = float(-ev[1] / ev[0]) if ev[0] != 0 else None
            out["johansen"] = {
                "trace": [round(v, 3) for v in trace],
                "trace_crit_95": [round(float(v), 3) for v in j.cvt[:, 1]],
                "max_eig": [round(v, 3) for v in maxeig],
                "max_eig_crit_95": [round(float(v), 3) for v in j.cvm[:, 1]],
                "rank": rank,
                "hedge_ratio": _r(j_beta, 6),
            }
        except Exception:
            pass
    ps = pl.Series("s", spread)
    w = max(10, min(int(z_window), n))
    z = (ps - ps.rolling_mean(w, min_samples=max(5, w // 2))) / ps.rolling_std(w, min_samples=max(5, w // 2))
    out["spread"] = [_r(v, 6) for v in spread.tolist()]
    out["zscore"] = [_r(v, 4) for v in z.to_list()]
    zl = [v for v in out["zscore"] if v is not None]
    out["z_last"] = zl[-1] if zl else None
    eg_ok = out["eg_p"] is not None and out["eg_p"] < 0.05
    j_ok = bool(out["johansen"] and out["johansen"]["rank"] >= 1)
    out["verdict"] = "cointegrated" if (eg_ok and j_ok) else ("weak" if (eg_ok or j_ok) else "none")
    return out


# --- Granger causality --------------------------------------------------------------------------------------
def granger(a, b, maxlag: int = 5) -> dict:
    """Granger causality in both directions with the lag chosen by AIC of the unrestricted model."""
    x, y = _pair(a, b)
    n = int(x.size)
    out: dict = {"n": n, "maxlag": int(maxlag), "b_to_a": None, "a_to_b": None}
    if n < 5 * maxlag + MIN_N or np.std(x) == 0 or np.std(y) == 0:
        return out
    from statsmodels.tsa.stattools import grangercausalitytests

    def _dir(target: np.ndarray, cause: np.ndarray) -> dict | None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                res = grangercausalitytests(np.column_stack([target, cause]), maxlag=maxlag)
            except Exception:
                return None
        best = None
        for lag, (tests, models) in res.items():
            aic = float(models[1].aic)
            f, p = tests["ssr_ftest"][0], tests["ssr_ftest"][1]
            if best is None or aic < best["aic"]:
                best = {"lag": int(lag), "aic": round(aic, 3), "f": _r(f, 3), "p": _r(p, 6)}
        return best

    out["b_to_a"] = _dir(x, y)
    out["a_to_b"] = _dir(y, x)
    return out
