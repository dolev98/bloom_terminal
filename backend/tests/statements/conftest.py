"""Shared synthetic fixtures for the statements tests (offline: no network, no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.routing import Mount


@pytest.fixture(scope="session", autouse=True)
def _mount_statements_router():
    """The integrator wires the router into main.py; tests attach it themselves, ahead of the SPA mount."""
    from app.api.routers import statements
    from app.main import app

    if not any(getattr(r, "path", "").startswith("/api/statements") for r in app.router.routes):
        app.include_router(statements.router)
        mounts = [r for r in app.router.routes if isinstance(r, Mount)]
        for m in mounts:
            app.router.routes.remove(m)
            app.router.routes.append(m)


def _f(start, end, val, filed, accn="0001-24-000001", form="10-K"):
    d = {"end": end, "val": val, "accn": accn, "fy": int(end[:4]), "fp": "FY", "form": form, "filed": filed}
    if start:
        d["start"] = start
    return d


@pytest.fixture
def companyfacts() -> dict:
    """Two fiscal years (calendar FYE), 10-Q 3M vs YTD facts, CFO only as YTD, a restated FY2023 revenue, a dup."""
    rev = [
        _f("2023-01-01", "2023-03-31", 200, "2023-05-01", "q1-23", "10-Q"),
        _f("2023-04-01", "2023-06-30", 240, "2023-08-01", "q2-23", "10-Q"),
        _f("2023-01-01", "2023-06-30", 440, "2023-08-01", "q2-23", "10-Q"),
        _f("2023-07-01", "2023-09-30", 260, "2023-11-01", "q3-23", "10-Q"),
        _f("2023-01-01", "2023-09-30", 700, "2023-11-01", "q3-23", "10-Q"),
        _f("2023-01-01", "2023-12-31", 1000, "2024-02-15", "k-23"),
        _f("2023-01-01", "2023-12-31", 1010, "2025-02-15", "k-24"),  # restated in the FY2024 10-K
        _f("2024-01-01", "2024-03-31", 250, "2024-05-01", "q1-24", "10-Q"),
        _f("2024-04-01", "2024-06-30", 300, "2024-08-01", "q2-24", "10-Q"),
        _f("2024-01-01", "2024-06-30", 550, "2024-08-01", "q2-24", "10-Q"),
        _f("2024-07-01", "2024-09-30", 320, "2024-11-01", "q3-24", "10-Q"),
        _f("2024-01-01", "2024-09-30", 870, "2024-11-01", "q3-24", "10-Q"),
        _f("2024-01-01", "2024-12-31", 1200, "2025-02-15", "k-24"),
        _f("2024-01-01", "2024-12-31", 1200, "2025-02-15", "k-24"),  # exact duplicate -> deduped
    ]
    cfo = [
        _f("2024-01-01", "2024-03-31", 100, "2024-05-01", "q1-24", "10-Q"),
        _f("2024-01-01", "2024-06-30", 220, "2024-08-01", "q2-24", "10-Q"),
        _f("2024-01-01", "2024-09-30", 350, "2024-11-01", "q3-24", "10-Q"),
        _f("2024-01-01", "2024-12-31", 500, "2025-02-15", "k-24"),
    ]
    assets = [
        _f(None, "2023-12-31", 5000, "2024-02-15", "k-23"),
        _f(None, "2024-03-31", 5100, "2024-05-01", "q1-24", "10-Q"),
        _f(None, "2024-06-30", 5200, "2024-08-01", "q2-24", "10-Q"),
        _f(None, "2024-09-30", 5300, "2024-11-01", "q3-24", "10-Q"),
        _f(None, "2024-12-31", 5500, "2025-02-15", "k-24"),
    ]
    equity = [
        _f(None, "2023-12-31", 2800, "2024-02-15", "k-23"),
        _f(None, "2024-12-31", 3000, "2025-02-15", "k-24"),
    ]
    cogs = [
        _f("2024-01-01", "2024-12-31", 700, "2025-02-15", "k-24"),
        _f("2023-01-01", "2023-12-31", 600, "2024-02-15", "k-23"),
    ]
    eps = [
        _f("2024-01-01", "2024-03-31", 0.5, "2024-05-01", "q1-24", "10-Q"),
        _f("2024-01-01", "2024-06-30", 1.0, "2024-08-01", "q2-24", "10-Q"),
        _f("2024-01-01", "2024-09-30", 1.5, "2024-11-01", "q3-24", "10-Q"),
        _f("2024-01-01", "2024-12-31", 2.0, "2025-02-15", "k-24"),
    ]
    return {
        "cik": 1,
        "entityName": "Test Co",
        "facts": {
            "us-gaap": {
                "Revenues": {"units": {"USD": rev}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": cfo}},
                "Assets": {"units": {"USD": assets}},
                "StockholdersEquity": {"units": {"USD": equity}},
                "CostOfRevenue": {"units": {"USD": cogs}},
                "EarningsPerShareBasic": {"units": {"USD/shares": eps}},
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [_f(None, "2024-12-31", 1000, "2025-02-15", "k-24")]}
                }
            },
        },
    }


def _row(label: str, vals: list[str], dollar: bool = False, span2: bool = False) -> str:
    cells = [f"<td>{label}</td>"]
    for v in vals:
        if dollar:
            cells.append(f"<td>$</td><td>{v}</td>")
        elif span2:
            cells.append(f"<td colspan='2'>{v}</td>")
        else:
            cells.append(f"<td></td><td>{v}</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def _table(title: str, header: str, years: tuple[str, str], rows: list[tuple]) -> str:
    h1 = f"<tr><td></td><td colspan='2'>{header}</td><td colspan='2'>{header}</td></tr>" if header else ""
    h2 = f"<tr><td></td><td colspan='2'>{years[0]}</td><td colspan='2'>{years[1]}</td></tr>"
    body = "".join(_row(r[0], list(r[1]), dollar=(len(r) > 2 and r[2])) for r in rows)
    return f"<p><b>{title}</b></p><p>U.S. dollars in thousands, except per share data</p><table>{h1}{h2}{body}</table>"


IS_ROWS = [
    ("Revenues", ("1,500", "1,300"), True),
    ("Cost of revenues", ("900", "800")),
    ("Gross profit", ("600", "500")),
    ("Research and development, net", ("100", "90")),
    ("Selling and marketing", ("150", "140")),
    ("General and administrative", ("80", "70")),
    ("Operating income", ("270", "200")),
    ("Financial expenses, net", ("10", "8")),
    ("Income before taxes on income", ("260", "192")),
    ("Taxes on income", ("40", "30")),
    ("Net income", ("220", "162"), True),
    ("Net income attributable to Testco shareholders", ("210", "160")),
    ("Earnings per share attributable to shareholders:", ("", "")),
    ("Basic", ("0.21", "0.16"), True),
    ("Diluted", ("0.20", "0.15"), True),
    ("Weighted average number of shares outstanding:", ("", "")),
    ("Basic", ("1,000,000", "1,000,000")),
    ("Diluted", ("1,050,000", "1,040,000")),
]
BS_ROWS = [
    ("Cash and cash equivalents", ("500", "450"), True),
    ("Short-term bank deposits", ("200", "200")),
    ("Trade receivables, net", ("300", "280")),
    ("Inventories", ("100", "90")),
    ("Total current assets", ("1,100", "1,020")),
    ("Property and equipment, net", ("400", "380")),
    ("Goodwill", ("300", "300")),
    ("Total assets", ("1,800", "1,700"), True),
    ("Trade payables", ("150", "140")),
    ("Short-term debt", ("100", "100")),
    ("Total current liabilities", ("250", "240")),
    ("Long-term debt", ("300", "320")),
    ("Total liabilities", ("550", "560")),
    ("Total shareholders' equity", ("1,250", "1,140")),
    ("Total liabilities and shareholders' equity", ("1,800", "1,700"), True),
]
CF_ROWS = [
    ("Net cash provided by operating activities", ("300", "250"), True),
    ("Purchase of property and equipment", ("(50)", "(40)")),
    ("Net cash used in investing activities", ("(60)", "(45)")),
    ("Repayment of long-term debt", ("(20)", "(20)")),
    ("Net cash used in financing activities", ("(190)", "(100)")),
    ("Effect of exchange rate changes on cash", ("—", "—")),
    ("Net increase in cash and cash equivalents", ("50", "105"), True),
]


def sixk_html(bs_rows: list[tuple] | None = None) -> str:
    q = ("2024", "2023")
    return (
        "<html><body><h1>Testco Reports First Quarter 2024 Results</h1>"
        + _table("CONSOLIDATED STATEMENTS OF INCOME", "Three months ended March 31,", q, IS_ROWS)
        + _table(
            "CONSOLIDATED BALANCE SHEETS", "", ("March 31, 2024", "December 31, 2023"), bs_rows or BS_ROWS
        )
        + _table("CONSOLIDATED STATEMENTS OF CASH FLOWS", "Three months ended March 31,", q, CF_ROWS)
        + "</body></html>"
    )


@pytest.fixture
def sixk_document() -> str:
    return sixk_html()


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, pages: list[list[str]]) -> Path:
    """Minimal multi-page PDF (Helvetica, ASCII text lines) readable by pdfplumber / pypdf."""
    objects: list[bytes] = []

    def add(b: bytes) -> int:
        objects.append(b)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add(b"")
    page_ids = []
    for lines in pages:
        content = "BT /F1 11 Tf 40 760 Td 14 TL " + " ".join(f"({_esc(ln)}) Tj T*" for ln in lines) + " ET"
        c = add(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream".encode())
        p = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Contents {c} 0 R /Resources << /Font << /F1 {font} 0 R >> >> >>".encode()
        )
        page_ids.append(p)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>".encode()
    )
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


PDF_PAGES = [
    [
        "Testco Ltd.",
        "Annual report 2024",
        "Table of contents",
        "Statement of financial position 3",
        "Statement of profit or loss 4",
        "Statement of cash flows 5",
    ],
    [
        "Testco Ltd.",
        "Consolidated statements of financial position",
        "NIS in thousands",
        "December 31, 2024 2023",
    ]
    + [f"Line item {i} {1000 + i * 13:,} {900 + i * 11:,}" for i in range(16)],
    [
        "Testco Ltd.",
        "Consolidated statements of profit or loss",
        "NIS in thousands",
        "Year ended December 31, 2024 2023",
    ]
    + [f"Line item {i} {2000 + i * 17:,} {1800 + i * 15:,}" for i in range(16)],
    ["Testco Ltd.", "Consolidated statements of cash flows", "NIS in thousands"]
    + [f"Line item {i} {3000 + i * 19:,} {2700 + i * 12:,}" for i in range(16)],
    ["Notes to the financial statements", "Note 1 - General", "The company was incorporated in Israel."],
]


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "testco_2024.pdf", PDF_PAGES)


def extraction_payload(bs_ok: bool = True) -> dict:
    """A StatementsExtraction dict (values printed in NIS thousands) with a comparative column."""
    liabilities = 600.0 if bs_ok else 500.0
    return {
        "company_name": "Testco Ltd.",
        "currency": "ILS",
        "unit_scale": 3,
        "confidence": 0.85,
        "notes": None,
        "periods": [
            {
                "period_end": "2024-12-31",
                "period_type": "FY",
                "fiscal_year": 2024,
                "fiscal_period": "FY",
                "is_comparative": False,
                "pages": [1, 2, 3],
                "values": {
                    "revenue": 2000.0,
                    "cost_of_revenue": 1200.0,
                    "gross_profit": 800.0,
                    "operating_income": 300.0,
                    "net_income": 220.0,
                    "total_assets": 1500.0,
                    "total_liabilities": liabilities,
                    "total_equity": 900.0,
                    "total_current_assets": 700.0,
                    "total_current_liabilities": 400.0,
                    "cash_and_equivalents": 250.0,
                    "cfo": 350.0,
                    "capex": 80.0,
                    "cfi": -120.0,
                    "cff": -150.0,
                    "fx_effect": 0.0,
                    "net_change_in_cash": 80.0,
                    "eps_basic": 2.2,
                    "shares_basic_weighted": 100000.0,
                },
            },
            {
                "period_end": "2023-12-31",
                "period_type": "FY",
                "fiscal_year": 2023,
                "fiscal_period": "FY",
                "is_comparative": True,
                "pages": [1, 2, 3],
                "values": {
                    "revenue": 1800.0,
                    "cost_of_revenue": 1100.0,
                    "gross_profit": 700.0,
                    "net_income": 180.0,
                    "total_assets": 1400.0,
                    "total_liabilities": 560.0,
                    "total_equity": 840.0,
                },
            },
        ],
    }


def fake_parse_factory(payload: dict, calls: list | None = None):
    """Build a `parse`-compatible coroutine returning `schema(**payload)` and a synthetic usage dict."""

    async def fake_parse(schema, system, content, **kw):
        if calls is not None:
            calls.append(
                {
                    "schema": schema.__name__,
                    "system": system[:40],
                    "content_types": [c.get("type") for c in content],
                    **kw,
                }
            )
        return schema(**payload), {
            "input_tokens": 1000,
            "output_tokens": 300,
            "cache_read": 0,
            "cache_write": 0,
            "cost_usd": 0.005,
        }

    return fake_parse
