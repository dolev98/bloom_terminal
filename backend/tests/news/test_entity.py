from app.news.entity import Alias, EntityMatcher, derived_aliases, normalize_name, seed_entities
from app.news.sources import build_sources


def _matcher():
    return EntityMatcher(
        aliases=[
            Alias("AAPL", "apple", 0.7),
            Alias("AAPL", "apple inc", 1.0),
            Alias("TEVA", "טבע", 1.0, "he"),
            Alias("TEVA", "teva pharmaceutical industries", 1.0),
            Alias("ICL", "icl group", 1.0),
            Alias("CHKP", "check point software technologies", 1.0),
        ],
        tickers={"AAPL", "TEVA", "ICL", "CHKP", "WIX"},
    )


def test_short_ticker_requires_cashtag_or_name():
    m = _matcher()
    assert "ICL" not in m.link("ICL rises on potash prices")
    assert m.link("$ICL rises on potash prices")["ICL"][1] == "cashtag"
    assert m.link("ICL Group raises dividend")["ICL"] == (1.0, "alias")
    assert m.link("(NYSE: ICL) announces")["ICL"][1] == "cashtag"


def test_long_ticker_bare_and_name_and_suffix_stripping():
    m = _matcher()
    r = m.link("TEVA shares fall after Apple Inc. earnings")
    assert r["TEVA"] == (0.6, "alias") and r["AAPL"] == (1.0, "alias")
    assert "AAPL" not in m.link("apple pie recipes")  # common word, lower-case, no finance context
    assert normalize_name("Check Point Software Technologies Ltd.") == "check point software technologies"
    # single-word names keep their legal form as the distinctive alias; no first-word alias for stop words
    assert derived_aliases("NICE", "NICE Ltd.") == [("nice", 1.0), ("nice ltd", 1.0)]
    assert derived_aliases("AAPL", "Apple Inc.") == [("apple", 1.0), ("apple inc", 1.0)]
    assert derived_aliases("TEVA", "Teva Pharmaceutical Industries Limited")[1] == ("teva", 0.7)


def test_hebrew_alias_with_prefix_and_provider_union():
    m = _matcher()
    r = m.link("המניה של טבע עלתה; גם בטבע מרוצים", {"WIX": 0.9})
    assert r["TEVA"] == (0.8, "context") and r["WIX"] == (0.9, "provider")  # טבע is also "nature"
    assert "TEVA" not in m.link("טיול בטבע")  # "a hike in nature"


async def test_seed_entities_from_sec_map_and_hebrew_yaml(db):
    tmap = {
        "AAPL": {"cik": 320193, "name": "Apple Inc."},
        "TEVA": {"cik": 818686, "name": "Teva Pharmaceutical Industries Ltd"},
    }
    res = await seed_entities(["AAPL", "TEVA.TA", "NICE"], tmap)
    assert res["entities"] == 3
    m = await EntityMatcher.load()
    assert m.link("אפל מציגה אייפון חדש")["AAPL"][0] == 1.0
    assert "TEVA.TA" in m.link("טבע מדווחת")  # Hebrew alias attached to the TASE line
    assert m.link("Apple Inc. reports")["AAPL"] == (1.0, "alias")


def _common_matcher():
    return EntityMatcher(
        aliases=[
            Alias("AAPL", "apple", 1.0),
            Alias("AAPL", "apple inc", 1.0),
            Alias("TGT", "target", 1.0),
            Alias("NVDA", "nvidia", 1.0),
            Alias("TEVA", "טבע", 1.0, "he"),
            Alias("TEVA", "teva", 0.7),
        ],
        tickers={"AAPL", "TGT", "NVDA", "TEVA"},
    )


# Real Google News headlines from the live feed (2026-09-23) that were wrongly linked to AAPL.
NOT_APPLE = [
    "Apple Picking And Fall Color Offer Easy Autumn Trips From Hickory",
    "New Fall Beers: Samuel Adams Apple Spice Ale & More",
    "Busti Apple Festival is Saturday, Sunday, in Busti -",
    "Apple Cider Century to roll on Sept. 27",
    "North Mankato special education teacher receives Golden Apple Award",
    "Backroads and Backstories | A Bite at the Apple Festival",
    "West central Michigan apple maturity report – September 23, 2026",
    "Apple orchards in the Ozarks battle heat as fall season begins",
    "Crisp and juicy: Apple crop flourishing thanks to cooler weather, rain",
    "Apple Valley News Now @ 5 p.m. - September 22, 2026",
    "Mobile PD seeks ID on suspects believed to have stolen 2 Apple Watches",
    "AAPL Sep 2026 160.000 call (AAPL260925C00160000) stock price, news, quote and history",
]
# Genuine Apple finance/company headlines from the same feed.
APPLE = [
    ("Apple (AAPL) Gets a Hold from UBS", "cashtag"),
    ("Commerce Bank Invests $1.24 Billion in Apple Inc. $AAPL", "alias"),
    ("Why Apple's stock chart is probably putting a smile on the face of new CEO John Ternus", "context"),
    ("BofA Sends Stark Message to Apple Stock Investors on iPhone 18", "context"),
    ("Apple Just Misses $5 Trillion Market Cap. The Milestone Is Likely Coming Soon.", "context"),
    ("Apple sued in Oregon over alleged AirTag stalking", "context"),
    ("Apple says new Macs beat cloud AI on cost per token", "context"),
    ("Apple Just Released Its Most Affordable Noise-Cancelling AirPods Ever", "context"),
    ("Apple To Face Fresh EU Penalty Under Digital Markets Rules, Sources Say", "context"),
    ("DOJ Partially Backs Apple In Epic High Court Contempt Case", "context"),
    ("Final Trade: AAPL, XLF, AUR, GOOGL", "alias"),
]


def test_common_word_company_names_need_finance_context():
    m = _common_matcher()
    for title in NOT_APPLE:
        assert "AAPL" not in m.link(title), title
    for title, method in APPLE:
        r = m.link(title)
        assert "AAPL" in r and r["AAPL"][1] == method, (title, r)
    # a finance phrase containing the name is not a mention; a corporate verb after the name is
    assert "TGT" not in m.link("Analyst raises Nvidia price target to $250")
    assert m.link("Analyst raises Nvidia price target to $250")["NVDA"] == (1.0, "alias")
    assert m.link("Target cuts prices ahead of the holidays")["TGT"] == (0.8, "context")
    assert "TGT" not in m.link("Hitting the target: a guide to archery")
    # all-caps text is no capitalisation signal for bare tickers, but finance words still count
    assert "AAPL" in m.link("APPLE SHARES FALL AFTER EARNINGS")


def test_hebrew_common_word_name_teva_nature():
    m = _common_matcher()
    for title in [
        "משפחה, טבע וכיף: נפגשים בחול המועד בהפנינג חקלאי בגלבוע",
        "בלי להירקב בבית: יותר מ-20 פעילויות בטבע בחול המועד",
        "פסטיבל 'הולכים על פתוח' בשיתוף החברה להגנת הטבע חוזר",
        "מטבע קריפטו חדש זינק ב-20 דולר",  # מטבע = coin, not "from Teva"
    ]:
        assert "TEVA" not in m.link(title), title
    assert m.link("אופנהיימר מעלים את הרף לטבע: מחיר יעד של 50 דולר")["TEVA"][1] == "context"
    assert "TEVA" in m.link("מניית טבע זינקה בבורסה")
    assert m.link("Teva to Host Conference Call")["TEVA"] == (0.7, "alias")  # English 'teva' is distinctive


def test_search_query_hints_are_not_trusted():
    trust = {s.id: s.trust_provider_tickers for s in build_sources()}
    assert trust["google_news"] is False and trust["gdelt"] is False
    assert trust["finnhub_news"] and trust["sec_filings"] and trust["wires"]
