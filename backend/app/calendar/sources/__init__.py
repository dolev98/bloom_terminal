"""Calendar sources. `build_sources()` returns the default set (official schedules, rule engines, vendors, corporate feeds)."""

from __future__ import annotations

from app.calendar.types import Source


def build_sources() -> list[Source]:
    from app.calendar.sources.bls_bea import BeaSource, BlsIcsSource
    from app.calendar.sources.cbs_rules import CbsRulesSource, TaseSource
    from app.calendar.sources.central_banks import CentralBankSource
    from app.calendar.sources.finnhub_corp import FinnhubCorporateSource
    from app.calendar.sources.fmp import FmpCorporateSource, FmpEconomicSource
    from app.calendar.sources.forexfactory import ForexFactorySource
    from app.calendar.sources.fred_releases import FredReleasesSource
    from app.calendar.sources.geo import GeoSource, MsciSource
    from app.calendar.sources.opex import NagerHolidaysSource, OpexSource
    from app.calendar.sources.treasury import TreasuryAuctionSource

    return [
        FredReleasesSource(),
        BlsIcsSource(),
        BeaSource(),
        CentralBankSource(),
        CbsRulesSource(),
        TaseSource(),
        TreasuryAuctionSource(),
        OpexSource(),
        NagerHolidaysSource(),
        GeoSource(),
        MsciSource(),
        FmpEconomicSource(),
        FmpCorporateSource(),
        ForexFactorySource(),
        FinnhubCorporateSource(),
    ]


OFFICIAL_SOURCES = {
    "fred_releases",
    "bls_ics",
    "bea",
    "central_banks",
    "cbs_rules",
    "tase",
    "treasury",
    "opex",
    "nager",
    "geo",
    "msci",
}
VENDOR_SOURCES = {"fmp_econ", "forexfactory"}
CORPORATE_SOURCES = {"finnhub_corp", "fmp_corp", "tase"}
ACTUALS_SOURCES = {"fred_releases", "fmp_econ", "treasury"}
