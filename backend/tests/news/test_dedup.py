from app.news import dedup


def test_canonical_url_strips_tracking_and_sorts():
    a = dedup.canonical_url("https://WWW.Example.com/a/b/?utm_source=rss&z=1&fbclid=x&a=2#frag")
    b = dedup.canonical_url("http://example.com/a/b?a=2&z=1&gclid=9")
    assert a == "https://example.com/a/b?a=2&z=1" and a == b
    assert dedup.url_hash(a) == dedup.url_hash("https://example.com/a/b/?z=1&a=2&utm_medium=x")


def test_google_news_redirect_decoded_when_base64():
    u = "https://news.google.com/rss/articles/CBMiXmh0dHBzOi8vd3d3LnJldXRlcnMuY29tL3RlY2hub2xvZ3kvYXBwbGUtYmVhdHMtcXVhcnRlcmx5LXJldmVudWUtZXN0aW1hdGVzLTIwMjYtMDktMjMv0gEA?oc=5"
    assert dedup.canonical_url(u).startswith("https://reuters.com/technology/apple-beats")
    assert dedup.decode_google_news_url("https://news.google.com/rss/articles/opaque?oc=5") is None


def test_title_normalisation_and_fuzzy_match():
    a = dedup.normalize_title(
        "Apple beats quarterly revenue estimates on iPhone strength - Reuters", "Reuters"
    )
    b = dedup.normalize_title("Apple Beats Quarterly Revenue Estimates On iPhone Strength | Bloomberg")
    assert a == b == "apple beats quarterly revenue estimates on iphone strength"
    assert dedup.titles_similar(a, "apple beats revenue estimates on iphone strength, quarterly")
    assert not dedup.titles_similar(a, "microsoft cuts guidance after weak cloud quarter")


def test_simhash_near_duplicates():
    a = dedup.simhash64("Apple beats quarterly revenue estimates on iPhone strength, shares rise")
    b = dedup.simhash64("APPLE beats quarterly revenue estimates on iPhone strength -- shares rise!")
    c = dedup.simhash64("Teva settles opioid litigation for $4.25 billion")
    assert dedup.hamming(a, a) == 0 and dedup.simhash_near(a, b) and not dedup.simhash_near(a, c)
    signed = dedup.to_signed64(a)
    assert -(1 << 63) <= signed < (1 << 63) and dedup.from_signed64(signed) == a


def test_detect_lang_and_strip_html():
    assert dedup.detect_lang("טבע מדווחת על עלייה ברווח") == "he" and dedup.detect_lang("Apple Inc") == "en"
    assert dedup.strip_html("<p>a &amp; b</p>") == "a & b"
