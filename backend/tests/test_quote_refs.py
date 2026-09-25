from datetime import UTC, datetime

from app.data.providers.base import Quote
from app.market import service


async def test_premarket_trade_uses_yesterday_close(db):
    service._quotes.clear()
    service._session_ref.clear()
    # REST quote after Tuesday's close: last = Tuesday close 741.21, pc = Monday close 747.46
    await service.store_quotes(
        [
            Quote(
                ticker="QQQ",
                ts=datetime(2026, 9, 23, 20, 0, tzinfo=UTC),
                last=741.21,
                prev_close=747.46,
                change_pct=-0.84,
                source="finnhub",
            )
        ]
    )
    # Wednesday pre-market trade: the previous close is Tuesday's close, not Monday's
    pc = service.prev_close_for_trade("QQQ", datetime(2026, 9, 24, 12, 37, tzinfo=UTC), 747.46)
    assert pc == 741.21
    # same-day trade keeps the REST previous close
    assert service.prev_close_for_trade("QQQ", datetime(2026, 9, 23, 19, 0, tzinfo=UTC), None) == 747.46


async def test_older_rest_quote_does_not_overwrite_newer_stream(db):
    service._quotes.clear()
    service._session_ref.clear()
    await service.store_quotes(
        [
            Quote(
                ticker="AAPL",
                ts=datetime(2026, 9, 24, 12, 37, tzinfo=UTC),
                last=337.31,
                prev_close=337.02,
                source="finnhub-ws",
            )
        ]
    )
    await service.store_quotes(
        [
            Quote(
                ticker="AAPL",
                ts=datetime(2026, 9, 23, 20, 0, tzinfo=UTC),
                last=337.02,
                prev_close=339.75,
                source="finnhub",
            )
        ]
    )
    assert service._quotes["AAPL"].last == 337.31
    # but the REST session reference is still recorded for later trades
    assert service._session_ref["AAPL"][1] == 337.02


async def test_rest_refresh_heals_stale_streamed_prev_close(db):
    service._quotes.clear()
    service._session_ref.clear()
    # a streamed trade loaded from disk with a two-days-old previous close
    await service.store_quotes(
        [
            Quote(
                ticker="QQQ",
                ts=datetime(2026, 9, 24, 12, 41, tzinfo=UTC),
                last=733.7,
                prev_close=747.46,
                change_pct=-1.84,
                source="finnhub-ws",
            )
        ]
    )
    await service.store_quotes(
        [
            Quote(
                ticker="QQQ",
                ts=datetime(2026, 9, 23, 20, 0, tzinfo=UTC),
                last=741.21,
                prev_close=747.46,
                source="finnhub",
            )
        ]
    )
    q = service._quotes["QQQ"]
    assert (
        q.last == 733.7 and q.prev_close == 741.21 and abs(q.change_pct - (733.7 / 741.21 - 1) * 100) < 1e-9
    )
