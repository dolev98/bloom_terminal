from datetime import datetime

import polars as pl

from app.data.store.parquet import ParquetStore


def _df(days, vals):
    return pl.DataFrame({"ts": [datetime(2024, 1, d) for d in days], "value": vals})


def test_write_merges_and_new_wins(tmp_path):
    st = ParquetStore(tmp_path)
    st.write("fred:DGS10", _df([1, 2, 3], [4.0, 4.1, 4.2]))
    merged = st.write("fred:DGS10", _df([3, 4], [9.9, 4.3]))
    assert merged.height == 4
    assert merged.filter(pl.col("ts") == datetime(2024, 1, 3))["value"][0] == 9.9
    assert st.stats("fred:DGS10")["n"] == 4
    assert st.list_ids() == ["fred:DGS10"]
    sub = st.read("fred:DGS10", start=datetime(2024, 1, 3))
    assert sub.height == 2
