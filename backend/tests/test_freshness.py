from datetime import datetime

from app.data.freshness import freshness
from app.data.providers.base import SeriesSpec

NOW = datetime(2026, 9, 24, 12, 0)


def _spec(freq: str, **kw) -> SeriesSpec:
    return SeriesSpec(series_id="fred:X", provider="fred", provider_key="X", freq=freq, **kw)


def _meta(last_ts, status="ok", n=10) -> dict:
    return {"last_ts": last_ts, "n_obs": n, "last_status": status}


def test_daily_ok_and_late():
    assert freshness(_spec("1d"), _meta(datetime(2026, 9, 18)), NOW)["state"] == "ok"  # weekly FX release lag
    assert freshness(_spec("1d"), _meta(datetime(2026, 9, 1)), NOW)["state"] == "late"


def test_monthly_uses_period_start_and_publication_lag():
    assert freshness(_spec("1mo"), _meta(datetime(2026, 6, 1)), NOW)["state"] == "ok"  # 3-month averages
    assert freshness(_spec("1mo"), _meta(datetime(2024, 3, 1)), NOW)["state"] == "late"
    lagged = _spec("1mo", publication_lag_days=60)
    assert freshness(lagged, _meta(datetime(2026, 4, 1)), NOW)["state"] == "ok"


def test_failed_never_paused_and_iso_input():
    assert freshness(_spec("1d"), _meta("2026-09-23T00:00:00", status="error"), NOW)["state"] == "failed"
    assert freshness(_spec("1d"), _meta(None, n=0), NOW)["state"] == "never"
    assert freshness(_spec("1d"), None, NOW)["state"] == "never"
    assert freshness(_spec("1d", enabled=False), _meta(datetime(2026, 9, 23)), NOW)["state"] == "paused"
    r = freshness(_spec("1w"), _meta("2026-09-16T00:00:00"), NOW)
    assert r["state"] == "ok" and r["expected_by"].startswith("2026-10-07")


def test_irregular_is_never_late():
    assert freshness(_spec("irregular"), _meta(datetime(2020, 1, 1)), NOW)["state"] == "ok"


def test_manual_csv_accepts_tabs_from_spreadsheets():
    from app.data.providers.manual import parse_csv_observations

    df = parse_csv_observations("date\tvalue\n2026-07\t49.1\n2026-08-01\t48.7\n")
    assert df["value"].to_list() == [49.1, 48.7]
