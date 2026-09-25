import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel, money, ratioPct } from '../../lib/format'
import { toast } from '../../lib/toast'
import { navigate } from '../../lib/router'
import { Card, Change, Empty, ErrorBox, Loading, Segmented, Stat } from '../../ui'
import FinancialCharts from '../../components/financials/FinancialCharts'
import { RatiosTable, StatementTable } from '../../components/financials/StatementTable'
import { CoverageSummary, PdfUpload, PendingDocs } from '../../components/financials/DataCoverage'
import { Disclosure } from '../../components/financials/shared'
import {
  BALANCE,
  CASHFLOW,
  HELP,
  INCOME,
  LAG,
  NON_COMPANY,
  lastPeriods,
  periodName,
  yoyChange,
  type CoverageRow,
  type EntityResp,
  type PeriodKind,
  type Profile,
  type RatiosResp,
  type StatementsResp,
} from '../../components/financials/model'

type Sheet = 'IS' | 'BS' | 'CF' | 'R'
const PERIOD_OPTIONS: [PeriodKind, string][] = [
  ['FY', 'Annual'],
  ['Q', 'Quarterly'],
  ['TTM', 'Trailing 12 months'],
]
const SHEETS: [Sheet, string][] = [
  ['IS', 'Income statement'],
  ['BS', 'Balance sheet'],
  ['CF', 'Cash flow'],
  ['R', 'Ratios'],
]
const VISIBLE = 8

const isFinQuery = (q: { queryKey: readonly unknown[] }) => /^(fin|val)/.test(String(q.queryKey[0]))

function errStatus(e: unknown): number | null {
  const m = /^(\d{3}):/.exec(String((e as Error)?.message ?? ''))
  return m ? Number(m[1]) : null
}

export default function Financials({ ticker }: { ticker: string }) {
  const t = ticker.toUpperCase()
  const enc = encodeURIComponent(t)
  const qc = useQueryClient()
  const [period, setPeriod] = useState<PeriodKind>('FY')
  const [sheet, setSheet] = useState<Sheet>('IS')
  const [showAll, setShowAll] = useState(false)
  const [uploadSignal, setUploadSignal] = useState(0)

  const profile = useQuery({ queryKey: ['fin-profile', t], queryFn: () => api<Profile>(`/api/market/profile/${enc}`), staleTime: 3_600_000, retry: false })
  const kind = profile.data?.kind
  const nonCompany = kind && kind !== 'stock' ? (NON_COMPANY[kind] ?? `a ${kind}`) : null
  const enabled = !profile.isLoading && !nonCompany

  const stm = useQuery({ queryKey: ['fin-stm', t, period], queryFn: () => api<StatementsResp>(`/api/statements/${enc}?period=${period}&restated=true`), enabled, retry: false })
  const hasRows = !!stm.data?.periods.length
  const ratios = useQuery({ queryKey: ['fin-ratios', t, period], queryFn: () => api<RatiosResp>(`/api/statements/${enc}/ratios?period=${period}`), enabled: enabled && hasRows, retry: false })
  const coverage = useQuery({ queryKey: ['fin-coverage', t], queryFn: () => api<CoverageRow[]>(`/api/statements/${enc}/coverage`), enabled, retry: false })
  const entity = useQuery({ queryKey: ['fin-entity', t], queryFn: () => api<EntityResp>(`/api/statements/${enc}/entity`), enabled, retry: false, staleTime: 3_600_000 })

  const refresh = useMutation({
    mutationFn: () => api<{ edgar?: { status?: string } }>(`/api/statements/${enc}/refresh`, { method: 'POST' }),
    onSuccess: (r) => {
      if (r?.edgar?.status === 'skipped') toast('SEC EDGAR has no filings for this company — upload a PDF report instead.', 'info')
      else toast('Financial statements updated from SEC filings.', 'ok')
      qc.invalidateQueries({ predicate: isFinQuery })
    },
    onError: (e) => toast(`Could not load SEC filings: ${String((e as Error).message ?? e)}`, 'err'),
  })

  const filer = entity.data?.filer_type
  const isTase = t.endsWith('.TA') || filer === 'tase_only'
  const secFiler = !isTase && (filer === 'us_10k' || filer === 'foreign_20f' || (!filer && !entity.isError))
  const companyName = entity.data?.name && !isTase ? entity.data.name : (profile.data?.name ?? t)

  const full = stm.data
  const view = useMemo(() => (full ? lastPeriods(full, showAll ? 0 : VISIBLE) : null), [full, showAll])
  const chartData = useMemo(() => (full ? lastPeriods(full, VISIBLE) : null), [full])

  if (profile.isLoading) return <Loading lines={4} />
  if (nonCompany)
    return (
      <Empty title="No financial statements">
        Financial statements apply to companies; this is {nonCompany}.
      </Empty>
    )

  const pendingCount = (coverage.data ?? []).reduce((a, r) => a + r.sources.reduce((b, s) => b + s.pending, 0), 0)
  const covTypes = new Set((coverage.data ?? []).map((r) => r.period_type))
  const units = full?.currency ? `${full.currency}, millions` : ''

  const toolbar = (
    <div className="row between" style={{ marginBottom: 14 }}>
      <div className="row" style={{ gap: 12 }}>
        <Segmented value={period} options={PERIOD_OPTIONS} onChange={(p) => setPeriod(p)} />
        {units && hasRows && <span className="muted small">{units}</span>}
        {stm.isFetching && !stm.isLoading && <span className="faint small">Updating…</span>}
      </div>
      {secFiler && (
        <button onClick={() => refresh.mutate()} disabled={refresh.isPending} title="Re-read the latest 10-K / 10-Q (or 20-F / 6-K) filings from SEC EDGAR">
          {refresh.isPending ? 'Loading filings…' : 'Refresh from filings'}
        </button>
      )}
    </div>
  )

  const advanced = (
    <Disclosure title="Advanced: upload a report (PDF)" hint="For companies listed only in Tel Aviv" openSignal={uploadSignal}>
      <PdfUpload ticker={t} />
    </Disclosure>
  )
  const coverageSection = (
    <Disclosure title="Data coverage" hint={pendingCount > 0 ? `${pendingCount} numbers waiting for your approval` : 'Which filings the numbers come from'}>
      <div className="stack" style={{ gap: 14 }}>
        {coverage.isError ? <div className="muted">No data has been loaded yet.</div> : coverage.isLoading ? <Loading lines={2} /> : <CoverageSummary rows={coverage.data ?? []} entity={entity.data} />}
        <PendingDocs ticker={t} />
      </div>
    </Disclosure>
  )

  // ---- empty / error states -------------------------------------------------------------------------------
  let empty: React.ReactNode = null
  const notLoaded = errStatus(stm.error) === 404 || (full && !hasRows && covTypes.size === 0)
  if (stm.isError && !notLoaded) empty = <ErrorBox error={stm.error} what="financial statements" />
  else if (notLoaded) {
    if (isTase && profile.data?.other_listing)
      empty = (
        <Empty
          title="Financial statements are under the US listing"
          actions={
            <>
              <button className="primary" onClick={() => navigate(`company/${profile.data!.other_listing}/financials`)}>
                Open {profile.data.other_listing} financials →
              </button>
              <button onClick={() => setUploadSignal((n) => n + 1)}>Upload a PDF report instead</button>
            </>
          }
        >
          {companyName.replace(/ \(Tel Aviv\)$/, '')} is also listed in the US as {profile.data.other_listing} and files its reports with the SEC. Those statements cover the same company (usually reported in US dollars).
        </Empty>
      )
    else if (isTase || entity.isError || filer === 'unknown')
      empty = (
        <Empty title="No financial statements yet" actions={<button className="primary" onClick={() => setUploadSignal((n) => n + 1)}>Upload a PDF report</button>}>
          {isTase
            ? 'Companies listed only in Tel Aviv don’t file machine-readable statements with the SEC. '
            : `${companyName} has no annual or quarterly reports in SEC EDGAR. `}
          Upload the company’s annual or quarterly report (PDF, e.g. from Maya): Claude extracts the numbers, they are checked for consistency, and you approve them before they are used.
        </Empty>
      )
    else
      empty = (
        <Empty
          title="Financial statements haven’t been loaded yet"
          actions={
            <button className="primary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
              {refresh.isPending ? 'Loading filings…' : 'Load from SEC filings'}
            </button>
          }
        >
          {companyName} files {filer === 'foreign_20f' ? 'annual 20-F reports and 6-K press releases' : '10-K annual and 10-Q quarterly reports'} with the SEC. Loading reads them directly from SEC EDGAR
          (takes a few seconds).
        </Empty>
      )
  } else if (full && !hasRows) {
    const what = period === 'Q' ? 'quarterly' : period === 'TTM' ? 'trailing 12-month' : 'annual'
    const why =
      period === 'TTM'
        ? 'Trailing 12-month figures are calculated from four consecutive quarters, and quarterly figures are missing.'
        : filer === 'foreign_20f' && period === 'Q'
          ? 'This company files a full report only once a year (20-F); quarterly numbers exist only when its 6-K press releases can be read.'
          : isTase
            ? 'Only the reports you uploaded are available.'
            : 'The loaded filings don’t include this kind of period.'
    empty = (
      <Empty title={`No ${what} figures`} actions={covTypes.has('FY') && period !== 'FY' ? <button className="primary" onClick={() => setPeriod('FY')}>Show annual figures</button> : undefined}>
        {why}
      </Empty>
    )
  }

  if (stm.isLoading || (!full && !stm.isError) || (full && !hasRows && coverage.isLoading)) return (
    <div className="stack">
      {toolbar}
      <Loading lines={6} />
    </div>
  )

  if (empty || !full || !view || !chartData)
    return (
      <div className="stack">
        {toolbar}
        {empty}
        {coverage.data && coverage.data.length > 0 && coverageSection}
        {advanced}
      </div>
    )

  // ---- key figures ------------------------------------------------------------------------------------------
  const n = full.periods.length
  const last = full.periods[n - 1]
  const lag = LAG[period] ?? 1
  const prev = n > lag ? full.periods[n - 1 - lag] : null
  const get = (k: string, i = n - 1) => (i >= 0 ? (full.fields[k]?.[i] ?? null) : null)
  const cur = full.currency
  const rev = get('revenue')
  const revYoy = prev ? yoyChange(rev, get('revenue', n - 1 - lag)) : null
  const oi = get('operating_income')
  const ratioRow = ratios.data?.periods.find((r) => r.period_end === last.period_end)
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  const netDebt = num(ratioRow?.net_debt)
  const roic = num(ratioRow?.roic)
  const pn = periodName(last)
  const revProv = full.provenance.revenue?.[n - 1]
  const tone = (v: number | null) => (v === null ? '' : v < 0 ? 'down' : '')
  const annualNote = period === 'Q' ? ', annualized' : ''
  const sourceLine = revProv?.source === 'edgar_xbrl' ? 'SEC EDGAR (XBRL)' : revProv?.source === 'pdf_llm' ? 'company report (PDF, extracted by AI and approved by you)' : revProv?.source === '6k_parsed' ? 'SEC 6-K press release' : 'company filings'
  const keyFoot = `${pn}: ${last.period_start ? `${dateLabel(last.period_start)} – ` : ''}${dateLabel(last.period_end)}. Source: ${sourceLine}${revProv?.filed_at ? `, filed ${dateLabel(revProv.filed_at)}` : ''}.`

  const ratioRows = (ratios.data?.periods ?? []).filter((r) => view.periods.some((p) => p.period_end === r.period_end))

  return (
    <div className="stack">
      {toolbar}

      <Card title="Key figures" hint={pn} foot={keyFoot}>
        <div className="stats" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <Stat
            label="Revenue"
            value={money(rev, cur)}
            sub={
              <>
                {pn}
                {prev && revYoy !== null && (
                  <>
                    {' · '}
                    <Change value={revYoy * 100} digits={1} /> vs {periodName(prev)}
                  </>
                )}
              </>
            }
          />
          <Stat label="Operating margin" value={ratioPct(rev && oi !== null ? oi / rev : null)} sub={pn} help="Operating income as a share of revenue." tone={tone(oi)} />
          <Stat label="Net income" value={money(get('net_income'), cur)} sub={pn} tone={tone(get('net_income'))} />
          <Stat label="Free cash flow" value={money(get('fcf'), cur)} sub={pn} help={HELP.fcf} tone={tone(get('fcf'))} />
          <Stat label="Net debt" value={ratios.isLoading ? '…' : money(netDebt, cur)} sub={`at ${dateLabel(last.period_end)}`} help={HELP.netDebt} />
          <Stat label="ROIC" value={ratios.isLoading ? '…' : ratioPct(roic)} sub={`${pn}${annualNote}`} help={HELP.roic} tone={tone(roic)} />
        </div>
      </Card>

      <FinancialCharts data={chartData} />

      <Card
        title="Financial statements"
        actions={
          <>
            {n > VISIBLE && (
              <button className="ghost" onClick={() => setShowAll((v) => !v)}>
                {showAll ? `Show last ${VISIBLE} periods` : `Show all ${n} periods`}
              </button>
            )}
            <Segmented value={sheet} options={SHEETS} onChange={setSheet} />
          </>
        }
      >
        {sheet === 'IS' && <StatementTable data={view} full={full} sections={INCOME} filerType={filer} note="Hover a number to see which filing it comes from." />}
        {sheet === 'BS' && <StatementTable data={view} full={full} sections={BALANCE} filerType={filer} note="Balances at the end of each period. Hover a number to see its filing." />}
        {sheet === 'CF' && <StatementTable data={view} full={full} sections={CASHFLOW} filerType={filer} note="Hover a number to see which filing it comes from." />}
        {sheet === 'R' &&
          (ratios.isLoading ? (
            <Loading lines={5} />
          ) : ratios.isError ? (
            <ErrorBox error={ratios.error} what="ratios" />
          ) : (
            <RatiosTable rows={ratioRows} currency={cur} periodType={period} />
          ))}
      </Card>

      {coverageSection}
      {advanced}
    </div>
  )
}
