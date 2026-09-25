# user_models/ — your own valuation models

Any `*.py` file in this folder (not starting with `_`) is auto-discovered by the terminal's valuation
registry at startup and on **POST /api/valuation/models/reload** (the "reload" button in the VAL panel).
Nothing else needs to change: your model shows up in the model selector, runs with the same inputs and
assumptions as the built-ins, is stored in `valuation_runs` / `fair_value_daily`, and can drive alerts via
the reference `model:<your id>`.

## Minimal contract

```python
from app.valuation.models.base import BaseValuationModel, ValuationResult


class MyModel(BaseValuationModel):
    id = "my_model"  # unique, no spaces; alerts reference it as model:my_model
    name = "My model"
    version = "1.0"
    inputs_required = {"eps_diluted"}  # canonical fields that must exist (see below)
    # assumptions_schema = Assumptions  # any pydantic model; its JSON schema drives the UI form

    def run(self, inputs, a):
        eps = inputs.value("eps_diluted")
        return ValuationResult(
            model_id=self.id, model_version=self.version, value_per_share=eps * 15, components={"pe": 15}
        )


MODEL = MyModel()  # optional; otherwise any class with `run` and `id` is instantiated with no args
```

* `inputs` is a frozen `InputsSnapshot` (`app.valuation.inputs`): `inputs.value("revenue")` (TTM first, then the
  latest FY), `inputs.series("revenue")` (annual list, oldest→newest), `inputs.annual`, `inputs.ttm`,
  `inputs.market.price / price_source / price_ts / beta_candidates`, `inputs.macro.rf / erp / crp`,
  `inputs.consensus`, `inputs.total_debt()`, `inputs.cash_and_sti()`, `inputs.shares()`.
* `a` is an `Assumptions` object (all fields optional). `a.resolve(inputs)` gives you fully defaulted numbers
  (growth path, margins, WACC, terminal g, equity bridge) if you want to build on the FCFF logic; `a.get_path("wacc.beta")`
  reads a raw override.
* Return a `ValuationResult` (value_per_share is what matters; `low`/`high` feed the football field, `components`
  and `diagnostics` are shown in the panel, `warnings` are surfaced). A plain dict with the same keys also works.
* Optional `run(self, inputs, a, policies)` receives the policy switches (SBC as cash, leases as debt, mid-year, g cap).

Canonical statement fields: revenue, cost_of_revenue, gross_profit, operating_income, depreciation_amortization,
stock_based_comp, interest_expense, pretax_income, income_tax_expense, net_income, eps_diluted,
shares_diluted_weighted, shares_outstanding, cash_and_equivalents, short_term_investments, total_current_assets,
total_current_liabilities, short_term_debt, long_term_debt, long_term_lease_liabilities, minority_interest,
total_equity, total_assets, cfo, capex, fcf, dividends_paid, share_repurchases.

## Not Python? Use the external model

Put `key,value,unit,scenario,note` rows in a CSV / XLSX / Google Sheet (share as "anyone with the link") and import it
in the panel (or `POST /api/valuation/{ticker}/import`). `key=fair_value` gives a ready fair value; keys like
`wacc.beta`, `terminal.g`, `revenue_growth`, `target_ebit_margin`, `bridge.shares` override assumptions and the
FCFF model runs with them. Damodaran's `fcffsimpleginzu.xlsx` imports with `kind=ginzu` (cell map in
`app/valuation/models/ginzu.py`, overridable).

Errors in a user file never break the app: they are reported by `GET /api/valuation/models` (`errors`).
See `example_model.py` (Graham number) for a complete working example.
