from app.data.providers.yfinance_provider import YFinanceProvider


def test_parse_url_handles_percent_encoded_caret():
    p = YFinanceProvider()
    assert p.parse_url("https://finance.yahoo.com/quote/%5EGSPC/") == "^GSPC"
    assert p.parse_url("https://finance.yahoo.com/quote/TEVA.TA?p=TEVA.TA") == "TEVA.TA"
