from datetime import datetime
from zoneinfo import ZoneInfo

from app.analytics.market_hours import tase_market_open, us_market_open

IL = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")


def test_tase_mon_fri():
    assert tase_market_open(datetime(2026, 9, 21, 12, 0, tzinfo=IL))  # Monday
    assert tase_market_open(datetime(2026, 9, 25, 11, 0, tzinfo=IL))  # Friday short session
    assert not tase_market_open(datetime(2026, 9, 25, 15, 0, tzinfo=IL))  # Friday afternoon
    assert not tase_market_open(datetime(2026, 9, 27, 12, 0, tzinfo=IL))  # Sunday


def test_us_hours():
    assert us_market_open(datetime(2026, 9, 23, 10, 0, tzinfo=NY))
    assert not us_market_open(datetime(2026, 9, 23, 16, 30, tzinfo=NY))
