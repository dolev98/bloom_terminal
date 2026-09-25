"""News jobs: poll (15 min, per-source cadence inside), enrich pending clusters (30 min), nightly derived series.
Register in app/jobs/scheduler.py::register_jobs:
  news.poll -> IntervalTrigger(minutes=15); news.enrich -> IntervalTrigger(minutes=30);
  news.sentiment_series -> CronTrigger(hour=2, minute=30)."""

from __future__ import annotations

import logging

from app.news import enrich, importance, service

log = logging.getLogger(__name__)


async def news_poll_job() -> str:
    res = await service.poll(force=False)
    srcs = res["sources"]
    ok = sum(1 for v in srcs.values() if v.get("status") in ("ok", "partial"))
    ins = sum(int(v.get("inserted", 0)) for v in srcs.values())
    return f"{ok}/{len(srcs)} sources ran, {ins} new items"


async def news_enrich_job(limit: int = 20) -> str:
    from app.llm.client import configured

    if not configured():
        return "LLM not configured"
    thr = await importance.llm_threshold()
    ids = await enrich.pending_cluster_ids(thr, limit)
    done = 0
    for cid in ids:
        if await enrich.enrich_cluster(cid) is not None:
            done += 1
        else:
            break  # budget/LLM errors: stop the batch, retry next run
    return f"enriched {done}/{len(ids)} (threshold {thr})"


async def news_sentiment_series_job() -> list[dict]:
    tickers = await service.watchlist_tickers()
    names = await service.entity_names(tickers)
    out = []
    for t in tickers:
        try:
            out.append({"status": "ok", **(await service.sentiment_series(t))})
        except Exception as e:
            out.append({"status": "error", "ticker": t, "error": str(e)[:200]})
        try:
            out.append({"status": "ok", **(await service.write_gdelt_tone(t, names.get(t)))})
        except Exception as e:
            out.append({"status": "error", "ticker": t, "error": f"gdelt: {e}"[:200]})
    return out
