"""Universe analytics: pairwise-complete correlation matrix (optional Ledoit–Wolf shrinkage), hierarchical
clustering order with optimal leaf ordering, and the PCA first-component "common factor share"."""

from __future__ import annotations

import math
import warnings

import numpy as np
import polars as pl


def wide_frame(frames: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Outer-join `{id: (ts,value)}` into `ts` + one Float64 column per id (missing → null)."""
    wide: pl.DataFrame | None = None
    for sid, df in frames.items():
        f = df.select(["ts", "value"]).rename({"value": sid})
        wide = f if wide is None else wide.join(f, on="ts", how="full", coalesce=True)
    if wide is None:
        return pl.DataFrame({"ts": []})
    return wide.sort("ts")


def corr_matrix(
    wide: pl.DataFrame, min_overlap: int = 60, shrink: bool = False, method: str = "pearson"
) -> tuple[list[str], np.ndarray, np.ndarray, list[str]]:
    """Pairwise-complete correlation. Returns (labels, matrix, overlap counts, notes). Pairs with fewer than
    `min_overlap` common rows are NaN. With `shrink`, Ledoit–Wolf is fitted on complete-case rows when enough
    exist; otherwise the pairwise matrix is returned with a note."""
    labels = [c for c in wide.columns if c != "ts"]
    notes: list[str] = []
    k = len(labels)
    if k == 0:
        return [], np.zeros((0, 0)), np.zeros((0, 0), dtype=int), notes
    X = wide.select(labels).to_numpy().astype(float)
    mask = np.isfinite(X)
    counts = (mask.astype(int).T @ mask.astype(int)).astype(int)
    import pandas as pd

    pdf = pd.DataFrame(X, columns=labels)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m = pdf.corr(method=method, min_periods=max(3, min_overlap)).to_numpy()
    m = np.where(counts >= min_overlap, m, np.nan)
    np.fill_diagonal(m, 1.0)
    if shrink:
        complete = X[mask.all(axis=1)]
        if complete.shape[0] >= max(min_overlap, k + 2):
            from sklearn.covariance import LedoitWolf

            Z = (complete - complete.mean(axis=0)) / np.where(
                complete.std(axis=0) > 0, complete.std(axis=0), 1.0
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                lw = LedoitWolf().fit(Z)
            cov = lw.covariance_
            d = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
            m = cov / np.outer(d, d)
            np.fill_diagonal(m, 1.0)
            counts = np.full((k, k), complete.shape[0], dtype=int)
            notes.append(
                f"Ledoit–Wolf shrinkage on {complete.shape[0]} complete rows (δ={lw.shrinkage_:.2f})"
            )
        else:
            notes.append("shrinkage skipped: too few complete-case rows; pairwise-complete matrix shown")
    return labels, m, counts, notes


def _distance(m: np.ndarray) -> np.ndarray:
    r = np.nan_to_num(np.clip(m, -1.0, 1.0), nan=0.0)
    d = np.sqrt(np.clip(0.5 * (1.0 - r), 0.0, 1.0))
    d = 0.5 * (d + d.T)
    np.fill_diagonal(d, 0.0)
    return d


def cluster_order(m: np.ndarray, threshold: float = 0.6) -> tuple[list[int], list[int]]:
    """Average-linkage clustering on sqrt(0.5(1−ρ)) with optimal leaf ordering. Returns (leaf order, cluster id
    per ORIGINAL index; clusters cut at distance `threshold` ≈ ρ > 1−2·threshold²)."""
    k = m.shape[0]
    if k <= 2:
        return list(range(k)), [1] * k
    from scipy.cluster.hierarchy import fcluster, leaves_list, linkage, optimal_leaf_ordering
    from scipy.spatial.distance import squareform

    d = _distance(m)
    condensed = squareform(d, checks=False)
    Z = linkage(condensed, method="average")
    try:
        Z = optimal_leaf_ordering(Z, condensed)
    except Exception:
        pass
    order = [int(i) for i in leaves_list(Z)]
    clusters = [int(c) for c in fcluster(Z, t=threshold, criterion="distance")]
    return order, clusters


def pc1_share(m: np.ndarray) -> float | None:
    """Share of total variance explained by the first principal component of the correlation matrix."""
    k = m.shape[0]
    if k == 0:
        return None
    r = np.nan_to_num(np.clip(m, -1.0, 1.0), nan=0.0)
    r = 0.5 * (r + r.T)
    np.fill_diagonal(r, 1.0)
    try:
        w = np.linalg.eigvalsh(r)
    except np.linalg.LinAlgError:
        return None
    w = np.clip(w, 0.0, None)
    tot = float(w.sum())
    if tot <= 0 or not math.isfinite(tot):
        return None
    return round(float(w.max() / tot), 4)


def analyze_universe(
    frames: dict[str, pl.DataFrame],
    min_overlap: int = 60,
    shrink: bool = False,
    method: str = "pearson",
    threshold: float = 0.6,
) -> dict:
    """Matrix + clustered order + pc1 share for a universe of observation frames (already transformed)."""
    dropped = [sid for sid, df in frames.items() if df.is_empty() or df.height < min_overlap]
    kept = {sid: df for sid, df in frames.items() if sid not in dropped}
    wide = wide_frame(kept)
    labels, m, counts, notes = corr_matrix(wide, min_overlap=min_overlap, shrink=shrink, method=method)
    order, clusters = cluster_order(m, threshold=threshold)
    share = pc1_share(m)
    ordered_labels = [labels[i] for i in order]
    mat = m[np.ix_(order, order)]
    cnt = counts[np.ix_(order, order)]
    return {
        "labels": ordered_labels,
        "order": order,
        "matrix": [[(None if not math.isfinite(v) else round(float(v), 4)) for v in row] for row in mat],
        "counts": [[int(v) for v in row] for row in cnt],
        "clusters": [clusters[i] for i in order],
        "pc1_share": share,
        "n_series": len(labels),
        "dropped": dropped,
        "notes": notes,
    }
