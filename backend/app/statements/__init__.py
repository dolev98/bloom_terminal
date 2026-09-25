"""Financial statements module (M2): canonical facts with provenance from SEC XBRL, 6-K exhibits,
filings.xbrl.org (IFRS/ESEF) and Maya PDFs (Claude extraction, manual approval), plus ratios.

Pure, network-free building blocks: `fields`, `periods`, `concept_map`, `edgar_xbrl`, `canonical`, `ratios`,
`validation`, `sixk` (parser), `xbrl_org` (converter), `pdf_pipeline` (locator/trimmer/validator).
Side effects (DB, HTTP, LLM) live in `service`.
"""
