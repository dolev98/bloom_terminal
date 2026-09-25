from pathlib import Path

from app.valuation.assumptions import Assumptions
from app.valuation.registry import ModelRegistry, reference_to_model
from tests.valuation.conftest import synthetic_inputs


def test_registry_discovers_example_user_model_and_runs_it():
    reg = ModelRegistry()
    info = reg.reload_user_models()
    assert "graham_number" in info["loaded"] and not info["errors"]
    assert reg.has("fcff") and reg.has("graham_number")
    m = reg.get("graham_number")
    res = m.run(synthetic_inputs(), Assumptions())
    assert res.value_per_share and res.value_per_share > 0
    res2 = m.run(synthetic_inputs(), Assumptions(custom={"multiplier": 30.0}))
    assert res2.value_per_share > res.value_per_share
    desc = {d["id"]: d for d in reg.describe()}
    assert (
        desc["graham_number"]["user"] is True
        and "multiplier" in desc["graham_number"]["assumptions_schema"]["properties"]
    )
    assert "wacc" in desc["fcff"]["assumptions_schema"]["properties"]


def test_registry_reports_broken_user_files(tmp_path: Path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp_path / "_ignored.py").write_text("x = 1\n")
    (tmp_path / "ok.py").write_text(
        "class M:\n    id = 'm1'\n    name = 'M'\n    version = '1'\n    def run(self, inputs, a):\n        return {'model_id': 'm1', 'value_per_share': 1.0}\n"
    )
    reg = ModelRegistry(user_dir=tmp_path)
    info = reg.reload_user_models()
    assert info["loaded"] == ["m1"] and "broken.py" in info["errors"]


def test_reference_mapping():
    assert reference_to_model("base_dcf") == "fcff" and reference_to_model("imported_fv") == "external"
    assert reference_to_model("model:graham_number") == "graham_number"
