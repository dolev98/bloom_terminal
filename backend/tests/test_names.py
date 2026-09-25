from app.market import names


async def test_curated_profile_and_score():
    p = await names.profile("ta35.ta")
    assert p["name"] == "TA-35 index" and p["kind"] == "index" and p["currency"] == "ILS"
    assert names._score("aapl", "aapl", "apple inc.") == 100
    assert names._score("app", "aapl", "apple inc.") == 60
    assert names._score("zzz", "aapl", "apple inc.") == 0
    assert names.kind_of("EURUSD=X") == "fx" and names.kind_of("CL=F") == "commodity"
    assert names._title("APPLE INC.") == "Apple Inc."


async def test_search_curated_by_name(monkeypatch):
    async def fake_map():
        return {"AAPL": {"cik": 320193, "name": "APPLE INC."}, "APP": {"cik": 1, "name": "AppLovin Corp"}}

    monkeypatch.setattr(names, "_sec_map", fake_map)
    res = await names.search("apple")
    assert res[0]["ticker"] == "AAPL" and res[0]["name"] == "Apple Inc."
    res = await names.search("gold")
    assert any(r["ticker"] == "GC=F" for r in res)


async def test_other_listing_links_dual_listed_companies():
    from app.market import names

    assert names.other_listing("TEVA.TA") == "TEVA" and names.other_listing("teva") == "TEVA.TA"
    assert names.other_listing("LUMI.TA") is None and names.other_listing("AAPL") is None
    p = await names.profile("TEVA.TA")
    assert p["other_listing"] == "TEVA" and p["currency"] == "ILS"
    assert "other_listing" not in await names.profile("^GSPC")
