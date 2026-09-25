"""SEC EDGAR filings as news items (8-K with item codes, 10-K/Q, 20-F, 6-K, Form 4, 13D/G, DEF 14A).
Uses the registry's EdgarProvider (submissions JSON inlines ~1,000 recent filings — enough for polling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.data.providers.base import LicenseSpec, RateSpec
from app.news.sources.base import NewsSource, RawNewsItem, to_naive_utc

FORMS = {
    "8-K",
    "8-K/A",
    "10-K",
    "10-Q",
    "20-F",
    "6-K",
    "4",
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
    "DEF 14A",
}
ITEM_CODES: dict[str, str] = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety – Reporting of Shutdowns",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure/Election of Directors or Officers; Compensation",
    "5.03": "Amendments to Articles or Bylaws; Change in Fiscal Year",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans",
    "5.05": "Amendments to Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "6.02": "Change of Servicer or Trustee",
    "6.03": "Change in Credit Enhancement",
    "6.04": "Failure to Make a Required Distribution",
    "6.05": "Securities Act Updating Disclosure",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}
FORM_LABELS = {
    "10-K": "Annual report (10-K)",
    "10-Q": "Quarterly report (10-Q)",
    "20-F": "Annual report (20-F)",
    "6-K": "Report of foreign private issuer (6-K)",
    "4": "Form 4 insider transaction",
    "SC 13D": "Schedule 13D — activist/5%+ stake",
    "SC 13D/A": "Schedule 13D/A — stake amendment",
    "SC 13G": "Schedule 13G — passive 5%+ stake",
    "SC 13G/A": "Schedule 13G/A — passive stake amendment",
    "DEF 14A": "Proxy statement (DEF 14A)",
}
# item codes that are boilerplate and should not lead a title
_BOILERPLATE = {"9.01"}


def parse_items(items: str | list | None) -> list[str]:
    if not items:
        return []
    if isinstance(items, list):
        return [str(i).strip() for i in items if str(i).strip()]
    return [i.strip() for i in str(items).split(",") if i.strip()]


def filing_title(form: str, items: list[str], company: str) -> str:
    if form.startswith("8-K"):
        lead = [i for i in items if i not in _BOILERPLATE] or items
        if lead:
            desc = ITEM_CODES.get(lead[0], "")
            head = f"{form} Item {lead[0]}" + (f" {desc}" if desc else "")
            if len(lead) > 1:
                head += f" (+{', '.join(lead[1:])})"
        else:
            head = form
    else:
        head = FORM_LABELS.get(form, form)
    return f"{head} — {company}" if company else head


def archive_url(cik: int | str, accession: str, primary_doc: str | None) -> str:
    accn = accession.replace("-", "")
    doc = primary_doc or f"{accession}-index.htm"
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}"


def parse_submissions(sub: dict, forms: set[str] | None = None, limit: int = 60) -> list[dict]:
    """Same shape as EdgarProvider.recent_filings plus acceptance time and size."""
    rec = sub.get("filings", {}).get("recent", {})
    n = len(rec.get("form", []))

    def col(name: str) -> list:
        v = rec.get(name)
        return v if isinstance(v, list) and len(v) == n else [None] * n

    out = []
    for i, form in enumerate(rec.get("form", [])):
        if forms and form not in forms:
            continue
        out.append(
            {
                "form": form,
                "accession": col("accessionNumber")[i],
                "filed": col("filingDate")[i],
                "accepted": col("acceptanceDateTime")[i],
                "report_date": col("reportDate")[i],
                "primary_doc": col("primaryDocument")[i],
                "items": col("items")[i] or "",
                "size": col("size")[i],
                "cik": sub.get("cik"),
            }
        )
        if len(out) >= limit:
            break
    return out


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return to_naive_utc(datetime.fromisoformat(str(s).replace("Z", "+00:00")))
    except ValueError:
        return None


@dataclass
class SecFilingsSource(NewsSource):
    id: str = "sec_filings"
    name: str = "SEC EDGAR filings"
    kind: str = "filing"
    cadence_s: int = 600
    keep_unmatched: bool = True
    rate: RateSpec = field(default_factory=lambda: RateSpec(per_second=8, concurrency=4))
    license: LicenseSpec = field(
        default_factory=lambda: LicenseSpec(grey=False, attribution="Source: SEC EDGAR")
    )
    requires: tuple[str, ...] = ("sec_user_agent",)
    limit_per_ticker: int = 60

    async def fetch(
        self, tickers: list[str], since: datetime | None, names: dict[str, str] | None = None
    ) -> list[RawNewsItem]:
        from app.data.registry import get_registry

        reg = get_registry()
        edgar = reg.get("edgar")
        tmap = await reg.call(edgar, "ticker_map", edgar.ticker_map, key="company_tickers")
        out: list[RawNewsItem] = []
        for t in tickers:
            t = t.upper()
            info = tmap.get(t)
            if not info:
                continue  # non-SEC filer (e.g. TASE-only)
            try:
                sub = await reg.call(edgar, "submissions", lambda t=t: edgar.submissions(t), key=t)
            except Exception as e:
                self.note_error(t, e)
                continue
            company = sub.get("name") or (names or {}).get(t) or info.get("name") or t
            cik = int(sub.get("cik") or info["cik"])
            for f in parse_submissions(sub, FORMS, self.limit_per_ticker):
                filed = _parse_dt(f["filed"])
                accepted = _parse_dt(f["accepted"])
                ts = accepted or filed
                if ts is None or (since and ts < since):
                    continue
                items = parse_items(f["items"])
                url = archive_url(cik, f["accession"], f["primary_doc"])
                out.append(
                    RawNewsItem(
                        source_id=self.id,
                        external_id=f["accession"],
                        url=url,
                        title=filing_title(f["form"], items, company),
                        snippet=", ".join(f"{i} {ITEM_CODES.get(i, '')}".strip() for i in items) or None,
                        published_at=ts,
                        publisher="SEC EDGAR",
                        tickers={t: 1.0},
                        filing={
                            "accession": f["accession"],
                            "cik": cik,
                            "ticker": t,
                            "form": f["form"],
                            "items": items,
                            "filed_at": filed,
                            "accepted_at": accepted,
                            "primary_doc_url": url,
                            "size": f["size"],
                        },
                        raw={"form": f["form"], "items": items, "report_date": f["report_date"], "cik": cik},
                    )
                )
        return out
