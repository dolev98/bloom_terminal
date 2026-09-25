import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { href, navigate } from '../lib/router'
import { dateLabel } from '../lib/format'
import { Card, Empty, ErrorBox, Help, Loading, Page, Segmented } from '../ui'
import EChart from '../components/charts/EChart'
import { AdvancedResults, DEFAULT_OPTIONS, OptionsPanel, type PairConfig } from '../components/correlation/Advanced'
import { overTimeOption, rollingOption, scatterOption } from '../components/correlation/charts'
import { PresetMenu, SeriesPicker, useSeriesName } from '../components/correlation/Pickers'
import { DiscoverView, MatrixView } from '../components/correlation/Views'
import {
  HELP,
  PERIOD_WORD,
  PERIODS,
  type PairDef,
  type PairResult,
  type Period,
  type RegimePreset,
  describeSide,
  periodForFreq,
  periodStart,
  plainWarnings,
  rho,
  spanText,
  strength,
} from '../components/correlation/words'

type View = 'pair' | 'matrix' | 'discover'
const isTicker = (id: string) => !!id && !id.includes(':')
const qs = (o: Record<string, string | number | boolean | undefined | null>) =>
  Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== null && v !== '' && v !== false && v !== 0)
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&')

export default function CorrelationPage({ a, b, pair }: { a?: string; b?: string; pair?: string }) {
  const qc = useQueryClient()
  const [view, setView] = useState<View>('pair')
  const [cfg, setCfgState] = useState<PairConfig>({ a: a || 'SPY', b: b || 'fred:DGS10', ...DEFAULT_OPTIONS })
  const [period, setPeriod] = useState<Period>('2Y')
  const [presetId, setPresetId] = useState(pair ?? '')
  const [showOptions, setShowOptions] = useState(false)
  const setCfg = (p: Partial<PairConfig>) => setCfgState((c) => ({ ...c, ...p }))

  const pairs = useQuery({ queryKey: ['corr-pairs'], queryFn: () => api<{ items: PairDef[] }>('/api/correlation/pairs'), staleTime: 300_000 })
  const regimes = useQuery({ queryKey: ['corr-regimes'], queryFn: () => api<RegimePreset[]>('/api/correlation/regimes/presets'), staleTime: 3_600_000 })
  const preset = pairs.data?.items.find((p) => p.id === presetId)

  const applyPreset = (p: PairDef) => {
    setCfgState((c) => ({ ...c, ...DEFAULT_OPTIONS, a: p.a, b: p.b, ta: p.transform_a ?? 'default', tb: p.transform_b ?? 'default', freq: p.freq || 'auto', lagB: p.lag_b ?? 0, coint: p.transform_a === 'level' && p.transform_b === 'level' }))
    setPeriod(periodForFreq(p.freq))
    setPresetId(p.id)
    setView('pair')
  }
  const compare = (x: string, y: string) => {
    setCfgState((c) => ({ ...c, ...DEFAULT_OPTIONS, a: x, b: y }))
    setPresetId('')
    setView('pair')
  }

  // URL → state (links from elsewhere), state → URL (replace, so the back button is not flooded)
  useEffect(() => {
    if ((a && a !== cfg.a) || (b && b !== cfg.b)) setCfgState((c) => ({ ...c, a: a || c.a, b: b || c.b }))
  }, [a, b]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (cfg.a !== a || cfg.b !== b || (presetId || undefined) !== (pair || undefined)) navigate('correlation', { a: cfg.a, b: cfg.b, pair: presetId || undefined }, true)
  }, [cfg.a, cfg.b, presetId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!pair || !pairs.data) return
    const p = pairs.data.items.find((x) => x.id === pair)
    if (p && (!a || p.a === a) && (!b || p.b === b)) applyPreset(p)
  }, [pairs.data]) // eslint-disable-line react-hooks/exhaustive-deps
  // a manual change of either series leaves the preset
  useEffect(() => {
    if (preset && (preset.a !== cfg.a || preset.b !== cfg.b)) setPresetId('')
  }, [cfg.a, cfg.b]) // eslint-disable-line react-hooks/exhaustive-deps

  const methods = ['rolling', 'stationarity', 'beta', ...(cfg.leadlag ? ['leadlag'] : []), ...(cfg.coint ? ['coint'] : []), ...(cfg.granger ? ['granger'] : [])].join(',')
  const query = qs({
    a: cfg.a,
    b: cfg.b,
    transform_a: cfg.ta === 'default' ? undefined : cfg.ta,
    transform_b: cfg.tb === 'default' ? undefined : cfg.tb,
    freq: cfg.freq === 'auto' ? undefined : cfg.freq,
    window: cfg.window || undefined,
    lag_b: cfg.lagB,
    invert_b: cfg.invertB,
    pub_lag: cfg.pubLag,
    regime: cfg.regime || undefined,
    start: periodStart(period),
    methods,
  })
  const res = useQuery({ queryKey: ['corr-pair', query], queryFn: () => api<PairResult>(`/api/correlation/pair?${query}`), enabled: view === 'pair' && !!cfg.a && !!cfg.b, retry: false, staleTime: 120_000 })
  const r = res.data

  const save = useMutation({
    mutationFn: (name: string) =>
      api('/api/correlation/pairs', {
        method: 'POST',
        json: {
          name,
          a: cfg.a,
          b: cfg.b,
          transform_a: cfg.ta === 'default' ? null : cfg.ta,
          transform_b: cfg.tb === 'default' ? null : cfg.tb,
          freq: cfg.freq,
          lag_b: cfg.lagB,
          rationale: '',
          tags: ['user'],
          country: cfg.a.endsWith('.TA') || /^(boi|cbs):/.test(cfg.a) ? 'IL' : (r?.a.country ?? 'US'),
        },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['corr-pairs'] }),
  })
  const del = useMutation({ mutationFn: (id: string) => api(`/api/correlation/pairs/${encodeURIComponent(id)}`, { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['corr-pairs'] }) })

  const nameA = useSeriesName(cfg.a)
  const nameB = useSeriesName(cfg.b)
  const shortA = isTicker(cfg.a) ? cfg.a : nameA
  const shortB = isTicker(cfg.b) ? cfg.b : nameB
  const changed = (Object.keys(DEFAULT_OPTIONS) as (keyof typeof DEFAULT_OPTIONS)[]).filter((k) => cfg[k] !== DEFAULT_OPTIONS[k]).length

  return (
    <Page
      title="Correlations"
      sub="How closely two series move together, and whether that relationship holds up over time."
      actions={<Segmented value={view} options={[['pair', 'Compare two'], ['matrix', 'Correlation matrix'], ['discover', 'Find related series']]} onChange={setView} />}
    >
      {view === 'matrix' && <MatrixView onCompare={compare} />}
      {view === 'discover' && <DiscoverView x={cfg.a} setX={(x) => setCfg({ a: x })} onCompare={compare} />}
      {view === 'pair' && (
        <div className="stack">
          <Card>
            <div className="row" style={{ gap: 10, fontSize: 15 }}>
              <span>Compare</span>
              <SeriesPicker value={cfg.a} onChange={(x) => setCfg({ a: x })} placeholder="Search a series or type a ticker" ariaLabel="Series A" />
              <span>with</span>
              <SeriesPicker value={cfg.b} onChange={(x) => setCfg({ b: x })} placeholder="Search a series or type a ticker" ariaLabel="Series B" />
              <button className="ghost icon" title="Swap the two series" onClick={() => setCfg({ a: cfg.b, b: cfg.a, ta: cfg.tb, tb: cfg.ta, lagB: -cfg.lagB })}>
                ⇄
              </button>
            </div>
            <div className="row" style={{ marginTop: 12 }}>
              <span className="small muted">Period</span>
              <Segmented value={period} options={PERIODS.map((p) => [p, p === 'All' ? 'All history' : p.replace('Y', ' years').replace(/^1 years$/, '1 year')] as [Period, string])} onChange={setPeriod} />
              <PresetMenu pairs={pairs.data?.items ?? []} current={presetId} onPick={applyPreset} onDelete={(p) => del.mutate(p.id)} />
              <button className={showOptions ? 'active' : ''} onClick={() => setShowOptions((s) => !s)} aria-expanded={showOptions}>
                Options{changed ? ` (${changed} changed)` : ''} {showOptions ? '▴' : '▾'}
              </button>
              {res.isFetching && <span className="small faint">Calculating…</span>}
            </div>
            {preset && (
              <p className="small" style={{ marginTop: 10, color: 'var(--text-2)' }}>
                <span className="strong">{preset.name}.</span> <span className="muted">{preset.rationale}</span>
              </p>
            )}
            {showOptions && <OptionsPanel cfg={cfg} setCfg={setCfg} r={r} nameA={shortA} nameB={shortB} regimes={regimes.data ?? []} onSave={async (n) => void (await save.mutateAsync(n))} />}
          </Card>

          {res.isLoading && <Loading lines={6} />}
          {res.error && <PairError error={res.error} a={cfg.a} b={cfg.b} period={period} onAllHistory={() => setPeriod('All')} />}
          {r && (
            <>
              <Headline r={r} nameA={nameA} nameB={nameB} />
              <Notices r={r} nameA={shortA} nameB={shortB} onUseChanges={() => setCfg({ ta: 'default', tb: 'default' })} />
              <OverTime r={r} nameA={shortA} nameB={shortB} />
              <div className="grid-2">
                <Rolling r={r} />
                <Scatter r={r} shortA={shortA} shortB={shortB} />
              </div>
              {showOptions && <AdvancedResults r={r} cfg={cfg} nameA={shortA} nameB={shortB} />}
            </>
          )}
        </div>
      )}
      <div className="attrib">Correlation describes how two series have moved together; it is not evidence that one causes the other. Data: FRED, Bank of Israel, exchange closing prices.</div>
    </Page>
  )
}

function PairError({ error, a, b, period, onAllHistory }: { error: unknown; a: string; b: string; period: Period; onAllHistory: () => void }) {
  const msg = String((error as Error)?.message ?? error)
  if (/overlapping observations|usable observations/.test(msg))
    return (
      <Empty title="Not enough overlapping data in this period" actions={period !== 'All' ? <button onClick={onAllHistory}>Use all available history</button> : undefined}>
        The two series share too few dates to compare reliably ({msg.replace(/^\d+:\s*/, '')}). Slower data such as monthly releases needs a longer period.
      </Empty>
    )
  const missing = msg.match(/no stored observations for \[(.*?)\]/)
  if (missing) {
    const ids = missing[1].split(',').map((s) => s.trim().replace(/^'|'$/g, ''))
    return (
      <Empty
        title="No stored data yet"
        actions={ids.map((id) => (
          <a key={id} className="chip" href={isTicker(id) ? href(`company/${id}`) : href(`series/${id}`)}>
            Open {id} to load it
          </a>
        ))}
      >
        There are no stored observations for {ids.join(' and ')}. Open the series to fetch its history, then come back.
      </Empty>
    )
  }
  if (msg.startsWith('404'))
    return (
      <Empty title="Series not found">
        We couldn't find “{[a, b].join('” or “')}”. Search by name in the pickers above, or check the ticker.
      </Empty>
    )
  return <ErrorBox error={error} what="the comparison" />
}

function Headline({ r, nameA, nameB }: { r: PairResult; nameA: string; nameB: string }) {
  const s = r.stats
  const [lo, hi] = s.ci
  const ciZero = lo !== null && hi !== null && lo <= 0 && hi >= 0
  const pw = PERIOD_WORD[r.freq] ?? 'periods'
  const notes: string[] = []
  if (lo !== null && hi !== null)
    notes.push(ciZero ? `The 95% confidence range (${rho(lo)} to ${rho(hi)}) includes zero, so there may be no real relationship at all.` : `The 95% confidence range is ${rho(lo)} to ${rho(hi)}.`)
  if (s.spearman !== null && s.pearson !== null && Math.abs(s.spearman - s.pearson) > 0.1)
    notes.push(`The rank correlation (${rho(s.spearman)}) is noticeably different, so a handful of large moves shape the headline figure.`)
  if (r.stability && r.rolling)
    notes.push(
      r.stability.std > 0.25
        ? `The relationship is unstable: measured over ${r.window}-${pw.replace(/s$/, '')} windows it has swung widely (typically ±${r.stability.std.toFixed(2)} around ${rho(r.stability.mean)}).`
        : `It has been fairly steady over time (the ${r.window}-${pw.replace(/s$/, '')} rolling correlation averaged ${rho(r.stability.mean)}).`,
    )
  if (r.lag_b) notes.push(`${nameB} is shifted by ${r.lag_b} ${pw} (${r.lag_b > 0 ? 'earlier values of it are paired with today’s' : 'later values of it are paired with today’s'} ${nameA}).`)
  const nEff = s.n_eff !== null && s.n_eff < s.n * 0.95 ? Math.round(s.n_eff) : null
  return (
    <Card foot={`Data from ${dateLabel(r.start)} to ${dateLabel(r.end)}.`}>
      <div style={{ fontSize: 21, fontWeight: 600, lineHeight: 1.3 }}>
        {strength(s.pearson)} <span className="num accent">(ρ = {rho(s.pearson)})</span>
      </div>
      <p style={{ marginTop: 6, color: 'var(--text-2)', maxWidth: 900 }}>
        between the {describeSide(r.a, r.freq, nameA)} and the {describeSide(r.b, r.freq, nameB)}, based on {spanText(r.start, r.end)} of data (n = {s.n.toLocaleString('en-US')}).
      </p>
      {notes.length > 0 && (
        <p className="small muted" style={{ marginTop: 6, maxWidth: 900 }}>
          {notes.join(' ')}
        </p>
      )}
      <div className="stats" style={{ marginTop: 14 }}>
        <div className="stat">
          <div className="k">
            Pearson ρ <Help text={HELP.pearson} />
          </div>
          <div className="v num">{rho(s.pearson)}</div>
        </div>
        <div className="stat">
          <div className="k">
            Spearman ρ <Help text={HELP.spearman} />
          </div>
          <div className="v num">{rho(s.spearman)}</div>
        </div>
        <div className="stat">
          <div className="k">
            95% confidence range <Help text={HELP.ci} />
          </div>
          <div className="v num">
            {rho(lo)} to {rho(hi)}
          </div>
        </div>
        <div className="stat">
          <div className="k">
            Observations <Help text={HELP.n} />
          </div>
          <div className="v num">{s.n.toLocaleString('en-US')}</div>
          {nEff !== null && <div className="s">≈ {nEff.toLocaleString('en-US')} effectively independent</div>}
        </div>
      </div>
    </Card>
  )
}

function Notices({ r, nameA, nameB, onUseChanges }: { r: PairResult; nameA: string; nameB: string; onUseChanges: () => void }) {
  const st = r.stationarity
  const trendA = st?.a_level.verdict === 'I(1)-like'
  const trendB = st?.b_level.verdict === 'I(1)-like'
  const levelA = r.a.transform === 'level'
  const levelB = r.b.transform === 'level'
  const who = trendA && trendB ? 'Both series trend' : trendA ? `${nameA} trends` : `${nameB} trends`
  const extra = plainWarnings(r.warnings)
  return (
    <>
      {(trendA || trendB) && !levelA && !levelB && (
        <div className="notice info">
          <span>ℹ</span>
          <div className="grow">{who} over time; correlating their levels can be misleading — so we compare changes instead.</div>
        </div>
      )}
      {((trendA && levelA) || (trendB && levelB)) && (
        <div className="notice warn">
          <span>⚠</span>
          <div className="grow">
            You are comparing raw levels, and {who.toLowerCase()} over time. Two trending series often look correlated even when they are unrelated, so this figure is probably misleading. Compare changes
            instead, or turn on the long-run link test (cointegration) in Options.
          </div>
          <button onClick={onUseChanges}>Compare changes instead</button>
        </div>
      )}
      {extra.length > 0 && (
        <div className="notice warn">
          <span>⚠</span>
          <div className="grow">{extra.join(' ')}</div>
        </div>
      )}
    </>
  )
}

function OverTime({ r, nameA, nameB }: { r: PairResult; nameA: string; nameB: string }) {
  const [mode, setMode] = useState<'separate' | 'rebased'>('separate')
  const rebased = useMemo(() => (mode === 'rebased' ? overTimeOption(r, nameA, nameB, true) : null), [r, nameA, nameB, mode])
  const separate = useMemo(() => overTimeOption(r, nameA, nameB, false), [r, nameA, nameB])
  const opt = rebased ?? separate
  return (
    <Card
      title="Both series over time"
      hint={mode === 'rebased' && !rebased ? 'Rebasing needs positive values — showing separate panels' : 'Raw values, before any transformation'}
      actions={<Segmented value={mode} options={[['separate', 'Separate panels'], ['rebased', 'Rebased to 100']]} onChange={setMode} />}
    >
      {opt && <EChart option={opt} height={330} />}
    </Card>
  )
}

function Rolling({ r }: { r: PairResult }) {
  const opt = useMemo(() => rollingOption(r), [r])
  const unit = (PERIOD_WORD[r.freq] ?? 'periods').replace(/s$/, '')
  return (
    <Card
      title="How the relationship changed"
      hint={`Rolling ${r.window}-${unit} correlation`}
      foot={`Each point is the correlation over the previous ${r.window} ${PERIOD_WORD[r.freq] ?? 'periods'}${r.freq === '1d' ? ' (trading days)' : ''}. The shaded band is its 95% range; the dashed line is zero.`}
    >
      {opt ? <EChart option={opt} height={260} /> : <div className="faint small">Not enough data for a rolling window.</div>}
    </Card>
  )
}

function Scatter({ r, shortA, shortB }: { r: PairResult; shortA: string; shortB: string }) {
  const built = useMemo(() => scatterOption(r, shortA, shortB), [r, shortA, shortB])
  const unit = (PERIOD_WORD[r.freq] ?? 'periods').replace(/s$/, '')
  const fit = built?.fit
  const num = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: Math.abs(v) < 0.1 ? 3 : 2 })
  const perUnit = (u: string) => (u === '%' ? '1%' : u ? `1 ${u}` : '1 unit')
  return (
    <Card
      title={`Each ${unit} as a dot`}
      hint={`${shortA} (up) against ${shortB} (across)`}
      foot={
        fit
          ? `On average, when ${shortB} rose by ${perUnit(built!.unitB)}, ${shortA} ${fit.slope >= 0 ? 'rose' : 'fell'} by ${num(Math.abs(fit.slope))}${built!.unitA === '%' ? '%' : ` ${built!.unitA}`}. The line explains ${(fit.r2 * 100).toFixed(1)}% of the ups and downs.`
          : undefined
      }
    >
      {built ? <EChart option={built.option} height={260} /> : <div className="faint small">Not enough data points.</div>}
    </Card>
  )
}
