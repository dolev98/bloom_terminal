from datetime import datetime

import polars as pl
import pytest

from app.data.derived import evaluate, formula_inputs


def _df(days, vals):
    return pl.DataFrame({"ts": [datetime(2024, 1, d) for d in days], "value": vals}).with_columns(
        pl.col("ts").cast(pl.Datetime("us"))
    )


def test_formula_inputs_parse_ids():
    ids = formula_inputs("fred:WALCL/1000 - fred:WTREGEN - boi:EXR/RER_USD_ILS + edgar:AAPL:Revenues")
    assert ids == ["fred:WALCL", "fred:WTREGEN", "boi:EXR/RER_USD_ILS", "edgar:AAPL:Revenues"]


def test_evaluate_net_liquidity_with_ffill():
    frames = {
        "fred:WALCL": _df([1, 8], [7_000_000.0, 7_100_000.0]),
        "fred:WTREGEN": _df([1, 8], [800.0, 700.0]),
        "fred:RRPONTSYD": _df([1, 2, 8], [500.0, 400.0, 300.0]),
    }
    out = evaluate("fred:WALCL/1000 - fred:WTREGEN - fred:RRPONTSYD", frames)
    assert out.height == 3
    assert out["value"][0] == 7000 - 800 - 500
    assert out["value"][1] == 7000 - 800 - 400  # WALCL/TGA forward-filled
    assert out["value"][2] == 7100 - 700 - 300


def test_evaluate_rejects_unsafe():
    with pytest.raises(ValueError):
        evaluate("fred:DGS10 + __import__('os')", {"fred:DGS10": _df([1], [1.0])})
