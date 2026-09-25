import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel } from '../../lib/format'
import { toast } from '../../lib/toast'
import { ErrorBox, Loading } from '../../ui'
import { SubHead } from './shared'
import { SOURCE_WORDS, coverageLabel, secFilingUrl, type CoverageRow, type EntityResp, type StatementDoc } from './model'

const TYPE_WORDS: Record<string, [string, string, string]> = {
  FY: ['Annual', 'fiscal year', 'fiscal years'],
  Q: ['Quarterly', 'quarter', 'quarters'],
  H: ['Half-yearly', 'half-year', 'half-years'],
}
const PERIOD_TYPE_WORDS: Record<string, string> = { FY: 'Annual report', Q: 'Quarterly report', H: 'Half-year report' }

function joinWords(xs: string[]): string {
  return xs.length <= 1 ? (xs[0] ?? '') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`
}

/** Plain-words summary of which periods came from which source, plus the filings behind the latest numbers. */
export function CoverageSummary({ rows, entity }: { rows: CoverageRow[]; entity?: EntityResp }) {
  if (!rows.length) return <div className="muted">No financial data has been loaded for this company yet.</div>
  const lines: string[] = []
  const exceptions: string[] = []
  for (const t of ['FY', 'Q', 'H'] as const) {
    const rs = rows.filter((r) => r.period_type === t).sort((a, b) => a.period_end.localeCompare(b.period_end))
    if (!rs.length) continue
    const main = new Map<string, number>()
    for (const r of rs) for (const s of r.sources) if (s.source !== 'derived') main.set(s.source, (main.get(s.source) ?? 0) + 1)
    const ranked = [...main.entries()].sort((a, b) => b[1] - a[1])
    const [word, one, many] = TYPE_WORDS[t]
    const primary = ranked[0]?.[0]
    const from = ranked.length ? `from ${joinWords(ranked.map(([s]) => SOURCE_WORDS[s] ?? s))}` : 'calculated from other periods'
    lines.push(`${word}: ${rs.length} ${rs.length === 1 ? one : many} (${coverageLabel(rs[0])} – ${coverageLabel(rs[rs.length - 1])}), ${from}.`)
    for (const [src] of ranked.slice(1)) {
      const which = rs.filter((r) => r.sources.some((s) => s.source === src)).map(coverageLabel)
      exceptions.push(`${which.slice(-6).join(', ')}${which.length > 6 ? ` and ${which.length - 6} more` : ''}: ${SOURCE_WORDS[src] ?? src}${primary ? ` (the rest: ${SOURCE_WORDS[primary] ?? primary})` : ''}.`)
    }
  }
  if (rows.some((r) => r.period_type === 'TTM')) lines.push('Trailing 12 months: calculated by adding up the last four quarters.')
  const pending = rows.reduce((a, r) => a + r.sources.reduce((b, s) => b + s.pending, 0), 0)

  const filings = new Map<string, { filed: string | null; periods: string[] }>()
  for (const r of rows) {
    if (r.period_type === 'TTM') continue
    for (const s of r.sources) {
      if (!s.accession_or_url || s.source === 'derived') continue
      const e = filings.get(s.accession_or_url) ?? { filed: s.filed_at, periods: [] }
      e.periods.push(coverageLabel(r))
      filings.set(s.accession_or_url, e)
    }
  }
  const recent = [...filings.entries()].sort((a, b) => (b[1].filed ?? '').localeCompare(a[1].filed ?? '')).slice(0, 5)

  return (
    <div className="stack" style={{ gap: 10 }}>
      <ul style={{ margin: 0, paddingLeft: 18, color: 'var(--text-2)', lineHeight: 1.7 }}>
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
        {exceptions.map((l) => (
          <li key={l} className="muted">
            {l}
          </li>
        ))}
      </ul>
      {pending > 0 && (
        <div className="notice warn">
          <span>!</span>
          <div className="grow">{pending} extracted numbers are waiting for your approval and are not used yet (see below).</div>
        </div>
      )}
      {recent.length > 0 && (
        <div>
          <SubHead>Latest filings behind the numbers</SubHead>
          <table className="table compact">
            <thead>
              <tr>
                <th>Filed</th>
                <th>Covers</th>
                <th>Document</th>
              </tr>
            </thead>
            <tbody>
              {recent.map(([acc, e]) => {
                const url = secFilingUrl(acc, entity?.cik)
                const name = acc.startsWith('http') ? (acc.split('/').pop() ?? acc) : acc
                return (
                  <tr key={acc}>
                    <td className="num">{dateLabel(e.filed)}</td>
                    <td>{[...new Set(e.periods)].join(', ')}</td>
                    <td className="mono faint">
                      {url ? (
                        <a href={url} target="_blank" rel="noreferrer">
                          {name}
                        </a>
                      ) : (
                        name
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function checkWords(d: StatementDoc): { ok: boolean; text: string; detail: string } {
  const v = d.validation ?? {}
  const checks = (v.periods ?? []).flatMap((p) => p.checks ?? [])
  const detail = checks.map((c) => `${c.note || c.name}: ${c.ok ? 'OK' : `off by ${c.diff_pct}% (allowed ${c.tol_pct}%)`}`).join('\n')
  if (v.error) return { ok: false, text: 'Extraction failed', detail: v.error }
  if (!checks.length) return { ok: false, text: 'No arithmetic checks could be run', detail }
  const failed = checks.filter((c) => !c.ok)
  if (!failed.length) return { ok: true, text: `All ${checks.length} arithmetic checks passed`, detail }
  return { ok: false, text: `${failed.length} of ${checks.length} arithmetic checks failed`, detail }
}

/** Extracted documents waiting for a decision. */
export function PendingDocs({ ticker }: { ticker: string }) {
  const qc = useQueryClient()
  const docs = useQuery({ queryKey: ['fin-docs', ticker], queryFn: () => api<StatementDoc[]>(`/api/statements/docs?status=pending&ticker=${encodeURIComponent(ticker)}`) })
  const decide = useMutation({
    mutationFn: ({ id, ok }: { id: number; ok: boolean }) => api(`/api/statements/docs/${id}/${ok ? 'approve' : 'reject'}`, { method: 'POST' }),
    onSuccess: (_r, v) => {
      toast(v.ok ? 'Approved — the numbers are now used in the statements.' : 'Rejected — the extracted numbers were discarded.', 'ok')
      qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('fin') || String(q.queryKey[0]).startsWith('val') })
    },
    onError: (e) => toast(`Could not save the decision: ${String((e as Error).message ?? e)}`, 'err'),
  })
  if (docs.isLoading) return <Loading lines={2} />
  if (docs.isError) return <ErrorBox error={docs.error} what="documents waiting for approval" />
  const list = docs.data ?? []
  return (
    <div>
      <SubHead>Waiting for your approval</SubHead>
      <p className="muted" style={{ marginBottom: 8, fontSize: 13 }}>
        Numbers extracted from PDFs or press releases by AI are only used after you approve them.
      </p>
      {list.length === 0 ? (
        <div className="faint" style={{ fontSize: 13 }}>
          Nothing is waiting for approval.
        </div>
      ) : (
        <table className="table compact">
          <thead>
            <tr>
              <th>Document</th>
              <th>Checks</th>
              <th>Added</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {list.map((d) => {
              const c = checkWords(d)
              const kind = d.kind === 'pdf' ? 'PDF' : d.kind === '6k' ? '6-K press release' : d.kind
              return (
                <tr key={d.id}>
                  <td title={d.path_or_url}>
                    {PERIOD_TYPE_WORDS[d.period_type ?? ''] ?? 'Report'} {d.fiscal_year ?? ''} <span className="faint">· {kind}</span>
                  </td>
                  <td title={c.detail}>
                    <span className={`badge ${c.ok ? 'ok' : 'warn'}`}>{c.text}</span>
                  </td>
                  <td className="num faint">{dateLabel(d.created_at)}</td>
                  <td className="r nowrap">
                    <button className="primary" disabled={decide.isPending} onClick={() => decide.mutate({ id: d.id, ok: true })}>
                      Approve
                    </button>{' '}
                    <button disabled={decide.isPending} onClick={() => decide.mutate({ id: d.id, ok: false })}>
                      Reject
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** PDF upload for Tel Aviv–only companies (Maya reports): Claude extracts, arithmetic checks, you approve. */
export function PdfUpload({ ticker, onUploaded }: { ticker: string; onUploaded?: () => void }) {
  const qc = useQueryClient()
  const [file, setFile] = useState<File | null>(null)
  const [fy, setFy] = useState(String(new Date().getFullYear() - 1))
  const [pt, setPt] = useState('FY')
  const upload = useMutation({
    mutationFn: async () => {
      const fd = new FormData()
      fd.append('file', file as File)
      if (fy) fd.append('fiscal_year', fy)
      fd.append('period_type', pt)
      return api<{ status?: string; error?: string | null }>(`/api/statements/${encodeURIComponent(ticker)}/pdf`, { method: 'POST', body: fd })
    },
    onSuccess: (r) => {
      if (r?.error) toast(`The report was read but extraction failed: ${r.error}`, 'err')
      else toast('Report processed — review the extracted numbers under “Data coverage” and approve them.', 'ok')
      setFile(null)
      qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith('fin') })
      onUploaded?.()
    },
    onError: (e) => toast(`Upload failed: ${String((e as Error).message ?? e)}`, 'err'),
  })
  return (
    <div className="stack" style={{ gap: 12 }}>
      <p className="muted" style={{ fontSize: 13, maxWidth: 760 }}>
        For companies listed only in Tel Aviv, download the annual or quarterly report (PDF) from Maya and upload it here. Claude reads the financial statements, the numbers are checked
        for consistency (assets = liabilities + equity, gross profit, cash-flow totals), and nothing is used until you approve it under “Data coverage”. Each upload uses a small part
        of your monthly AI budget.
      </p>
      <div className="form" style={{ gridTemplateColumns: '160px minmax(0, 1fr)', maxWidth: 620 }}>
        <label htmlFor="fin-pdf">Report (PDF)</label>
        <input id="fin-pdf" type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <label htmlFor="fin-fy">Fiscal year</label>
        <input id="fin-fy" className="num" style={{ width: 100 }} inputMode="numeric" value={fy} onChange={(e) => setFy(e.target.value.replace(/\D/g, '').slice(0, 4))} />
        <label htmlFor="fin-pt">Report type</label>
        <select id="fin-pt" style={{ width: 200 }} value={pt} onChange={(e) => setPt(e.target.value)}>
          <option value="FY">Annual report</option>
          <option value="Q">Quarterly report</option>
          <option value="H">Half-year report</option>
        </select>
        <span />
        <div className="row">
          <button className="primary" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>
            {upload.isPending ? 'Extracting numbers…' : 'Extract numbers'}
          </button>
          {upload.isPending && <span className="faint">This can take a minute for a long report.</span>}
        </div>
      </div>
    </div>
  )
}
