import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { href, navigate } from '../../lib/router'
import { dayLabel, timeIL } from '../../lib/format'
import { Card, Empty, ErrorBox, Flag, Help, Importance, Loading } from '../../ui'
import Sparkline from '../Sparkline'
import EChart, { AXIS_STYLE, baseOption, type EChartsOption } from '../charts/EChart'
import { plainTitle, type CalEvent } from '../calendar/model'
import { COMPARE_COLUMNS, REGIME_TEXT, SECTIONS, type Country, type Dashboard, type Tile, changeText, indicatorHelp, indicatorLabel, indicatorShort, periodLabel, valueText } from './indicators'

const Z_HELP = 'z-score: how far the latest reading is from its average of the past 3 years, measured in standard deviations. 0 = normal; beyond ±1 = unusually high or low.'
const monthYear = (s: string) => new Date(`${s.slice(0, 10)}T12:00:00Z`).toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' })
const zText = (z: number) => `${z > 0 ? '+' : z < 0 ? '−' : ''}${Math.abs(z).toFixed(2)}`

// ------------------------------------------------------------------ economic regime
// mirrors GROWTH_KEYS / INFLATION_KEYS in backend/app/macro/service.py
const GROWTH_PARTS = ['gdp_yoy', 'ip', 'retail', 'pmi', 'payrolls']
const INFLATION_PARTS = ['cpi', 'core_cpi', 'ppi', 'wages']

/** "GDP growth −0.97 · retail sales +1.53" for the parts that have data, plus the ones left out. */
function partsText(d: Dashboard, keys: string[]): { used: string; missing: string[] } {
  const tiles = new Map(d.sections.flatMap((s) => s.tiles).map((t) => [t.indicator, t]))
  const used: string[] = []
  const missing: string[] = []
  for (const k of keys) {
    const t = tiles.get(k)
    if (!t) continue
    if (t.zscore_3y === null) missing.push(indicatorShort(k))
    else used.push(`${indicatorShort(k)} ${zText(t.zscore_3y)}`)
  }
  return { used: used.join(' · '), missing }
}

export function RegimeCard({ d }: { d: Dashboard }) {
  const { growth_z: g, inflation_z: i, label } = d.regime
  const gp = partsText(d, GROWTH_PARTS)
  const ip = partsText(d, INFLATION_PARTS)
  const left = [...gp.missing, ...ip.missing]
  const option = useMemo<EChartsOption | null>(() => {
    if (g === null || i === null) return null
    const trail = d.regime.trail.filter((p) => p.growth_z !== null && p.inflation_z !== null)
    const all = [...trail.flatMap((p) => [p.growth_z as number, p.inflation_z as number]), g, i]
    const m = Math.max(2, Math.ceil(Math.max(...all.map(Math.abs)) + 0.3))
    const corner = (x: number, y: number, text: string, align: 'left' | 'right') => ({ value: [x, y], name: text, label: { align, verticalAlign: (y > 0 ? 'top' : 'bottom') as 'top' | 'bottom' } })
    return {
      grid: { left: 58, right: 16, top: 12, bottom: 50 },
      legend: { show: false },
      tooltip: {
        ...(baseOption.tooltip as object),
        trigger: 'item',
        formatter: (p: any) => (p.seriesName === 'q' ? '' : `${p.data[2]}<br/>Growth ${zText(p.data[0])} · Inflation ${zText(p.data[1])}`),
      },
      xAxis: { type: 'value', min: -m, max: m, name: 'Growth vs its 3-year norm (z) →', nameLocation: 'middle', nameGap: 32, ...AXIS_STYLE, splitLine: { show: false } },
      yAxis: { type: 'value', min: -m, max: m, name: 'Inflation vs norm (z) →', nameLocation: 'middle', nameGap: 36, ...AXIS_STYLE, splitLine: { show: false } },
      series: [
        {
          name: 'q',
          type: 'scatter',
          silent: true,
          symbolSize: 0,
          label: { show: true, formatter: '{b}', color: '#626b7a', fontSize: 11 },
          data: [corner(m - 0.1, m - 0.1, 'Running hot', 'right'), corner(m - 0.1, -m + 0.1, 'Goldilocks', 'right'), corner(-m + 0.1, m - 0.1, 'Stagflation', 'left'), corner(-m + 0.1, -m + 0.1, 'Slowdown', 'left')],
          markLine: { silent: true, symbol: 'none', label: { show: false }, lineStyle: { color: '#343c4a', type: 'solid' }, data: [{ xAxis: 0 }, { yAxis: 0 }] },
        },
        {
          name: 'trail',
          type: 'line',
          data: trail.map((p) => [p.growth_z, p.inflation_z, monthYear(p.ts)]),
          symbol: 'circle',
          symbolSize: 6,
          lineStyle: { color: '#5aa9ff', width: 1.5, opacity: 0.6 },
          itemStyle: { color: '#5aa9ff', opacity: 0.75 },
          label: { show: true, formatter: (p: any) => (p.dataIndex === 0 ? p.data[2] : ''), color: '#8a93a3', fontSize: 10, position: 'bottom' },
        },
        {
          name: 'latest',
          type: 'scatter',
          data: [[g, i, 'Latest reading']],
          symbolSize: 13,
          itemStyle: { color: '#f5a524', borderColor: '#151920', borderWidth: 2 },
          label: { show: true, formatter: 'Latest', position: 'right', color: '#f5a524', fontSize: 11 },
        },
      ],
    }
  }, [g, i, d.regime.trail])

  const missing = g === null ? 'growth' : i === null ? 'inflation' : null
  const rt = label ? REGIME_TEXT[label] : null
  return (
    <Card
      title={
        <>
          Economic regime <Help text={Z_HELP} />
        </>
      }
      hint={rt ? rt.title : undefined}
      foot={`Each score is a simple average of the indicators listed, each measured against its own past 3 years.${left.length ? ` Left out (no data): ${left.join(', ')}.` : ''} Blue dots: the past 12 months.`}
    >
      {missing || !option ? (
        <Empty title={`Can't place ${d.name} yet`}>No {missing ?? 'growth or inflation'} indicator has data for this country, so the regime cannot be computed.</Empty>
      ) : (
        <>
          <p className="small" style={{ color: 'var(--text-2)', marginBottom: 6 }}>
            {rt?.text}{' '}
            <span className="muted">
              (growth {zText(g as number)}, inflation {zText(i as number)}
              {Math.max(Math.abs(g as number), Math.abs(i as number)) < 0.5 ? ' — both close to normal, so the label is tentative' : ''})
            </span>
          </p>
          <EChart option={option} height={270} />
          <div className="kv small" style={{ marginTop: 8 }}>
            <span className="muted">Growth {zText(g as number)}, from</span>
            <span>{gp.used}</span>
            <span className="muted">Inflation {zText(i as number)}, from</span>
            <span>{ip.used}</span>
          </div>
        </>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ next releases
export function NextReleases({ cc, name }: { cc: string; name: string }) {
  const from = new Date().toISOString().slice(0, 10)
  const to = new Date(Date.now() + 45 * 86400000).toISOString().slice(0, 10)
  const codes = cc === 'EA' ? 'EA,DE' : cc
  const q = useQuery({
    queryKey: ['cal-events', 'macro-next', codes, from],
    queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?from=${from}&to=${to}&countries=${codes}&min_importance=2`),
    staleTime: 300_000,
  })
  const next = (q.data?.items ?? []).filter((e) => e.kind !== 'holiday' && new Date(`${e.release_ts}Z`).getTime() > Date.now()).slice(0, 5)
  return (
    <Card title="Next releases" hint={`${name}, medium and high importance`} actions={<a className="small" href={href('calendar', { country: cc })}>Full calendar →</a>}>
      {q.isLoading && <Loading lines={4} />}
      {q.error && <ErrorBox error={q.error} what="upcoming releases" />}
      {q.data && next.length === 0 && <div className="faint small">Nothing of medium or high importance is scheduled for {name} in the next 45 days.</div>}
      <div className="list">
        {next.map((e) => (
          <div key={e.id} className="list-item click" onClick={() => navigate('calendar', { country: cc })}>
            <div className="li-time" style={{ width: 92 }}>
              <div>{dayLabel(e.release_ts)}</div>
              <div className="faint">{timeIL(e.release_ts)}</div>
            </div>
            <div className="li-body">
              <div className="li-title">{plainTitle(e.title)}</div>
            </div>
            <Importance level={e.importance} />
          </div>
        ))}
      </div>
    </Card>
  )
}

// ------------------------------------------------------------------ indicator sections
export function SectionCard({ cc, countryName, section, tiles }: { cc: string; countryName: string; section: (typeof SECTIONS)[number]; tiles: Tile[] }) {
  const byKey = new Map(tiles.map((t) => [t.indicator, t]))
  const shown = section.indicators.filter((k) => byKey.has(k)).map((k) => byKey.get(k) as Tile)
  const noSource = section.indicators.filter((k) => !byKey.has(k))
  if (!shown.length)
    return (
      <Card title={section.title}>
        <div className="faint small">No data source yet for {countryName}: {noSource.map(indicatorShort).join(', ')}.</div>
      </Card>
    )
  return (
    <Card title={section.title} flush foot={noSource.length ? `No data source yet for ${noSource.map(indicatorShort).join(', ')}.` : undefined}>
      <table className="table">
        <thead>
          <tr>
            <th>Indicator</th>
            <th className="r">Latest</th>
            <th className="r">Change</th>
            <th title="The last 24 observations">Trend</th>
            <th>Period</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((t) => (
            <Row key={t.indicator} cc={cc} t={t} />
          ))}
        </tbody>
      </table>
    </Card>
  )
}

function Row({ cc, t }: { cc: string; t: Tile }) {
  const label = indicatorLabel(cc, t.indicator)
  const help = indicatorHelp(cc, t.indicator)
  const open = () => navigate(`series/${t.series_id}`)
  if (t.n === 0 || t.last === null) {
    const manual = t.series_id.startsWith('manual:')
    return (
      <tr className="click" onClick={open} title={t.series_id}>
        <td className="label-col">
          {label}
          {help && <Help text={help} />}
        </td>
        <td colSpan={4} className="faint small">
          {manual ? 'Entered by hand — no values added yet. Click to add them.' : 'No data yet: the source has not delivered any observations. Try "Refresh data".'}
        </td>
      </tr>
    )
  }
  const ch = changeText(t)
  const prevPeriod = t.sparkline.ts.length > 1 ? periodLabel(t.sparkline.ts[t.sparkline.ts.length - 2], t.freq) : null
  return (
    <tr className="click" onClick={open} title={`Open the full history (${t.series_id})`}>
      <td className="label-col">
        {label}
        {help && <Help text={help} />}
      </td>
      <td className="r num strong">{valueText(t)}</td>
      <td className={`r num ${ch.sign > 0 ? 'up' : ch.sign < 0 ? 'down' : 'muted'}`} title={prevPeriod ? `Change vs ${prevPeriod}` : undefined}>
        {ch.text}
      </td>
      <td style={{ width: 90 }}>
        <Sparkline values={t.sparkline.value} width={80} height={22} color={t.stale ? 'var(--faint)' : 'var(--info)'} />
      </td>
      <td className="nowrap small">{t.stale ? <span className="warn-text">Not updated since {periodLabel(t.last_ts, t.freq)}</span> : <span className="muted">{periodLabel(t.last_ts, t.freq)}</span>}</td>
    </tr>
  )
}

// ------------------------------------------------------------------ compare countries
type Heat = { countries: string[]; indicators: string[]; cells: { country: string; indicator: string; z: number | null; last: number | null; last_ts?: string | null; series_id: string | null; stale?: boolean }[] }

function tint(z: number | null): string | undefined {
  if (z === null) return undefined
  const a = Math.min(1, Math.abs(z) / 2.5) * 0.32
  return z > 0 ? `rgba(245,165,36,${a.toFixed(3)})` : `rgba(90,169,255,${a.toFixed(3)})`
}

export function CompareTable({ countries }: { countries: Country[] }) {
  const q = useQuery({ queryKey: ['macro-heat', 'compare'], queryFn: () => api<Heat>(`/api/macro/heatmap?indicators=${COMPARE_COLUMNS.map((c) => c.key).join(',')}`), staleTime: 600_000 })
  const cell = (cc: string, k: string) => q.data?.cells.find((c) => c.country === cc && c.indicator === k)
  const names = new Map(countries.map((c) => [c.cc, c.name]))
  return (
    <Card
      title="Compare countries"
      hint="Latest reading of each indicator; shading shows how unusual it is for that country"
      foot={
        <>
          Growth and inflation figures are % change vs a year earlier; rates are % per year. Shading: amber = high and blue = low compared with that country's own last 3 years
          <Help text={Z_HELP} />. Hover a cell for its date; click to open the series. “—” = no data yet; * = not updated recently.
        </>
      }
      flush
    >
      {q.isLoading && (
        <div style={{ padding: 16 }}>
          <Loading lines={6} />
        </div>
      )}
      {q.error && <ErrorBox error={q.error} what="the comparison" />}
      {q.data && (
        <div style={{ overflowX: 'auto' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Country</th>
                {COMPARE_COLUMNS.map((c) => (
                  <th key={c.key} className="r" title={c.help}>
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {q.data.countries.map((cc) => (
                <tr key={cc}>
                  <td className="nowrap">
                    <a href={href(`macro/${cc}`)}>
                      <Flag cc={cc} /> {names.get(cc) ?? cc}
                    </a>
                  </td>
                  {COMPARE_COLUMNS.map((c) => {
                    const x = cell(cc, c.key)
                    const has = x && x.last !== null
                    const digits = ['policy', 'y2', 'y10'].includes(c.key) ? 2 : 1
                    const freq = c.key === 'gdp_yoy' ? '1q' : x?.last_ts?.slice(8, 10) === '01' ? '1mo' : '1d'
                    const when = x?.last_ts ? periodLabel(x.last_ts, freq) : ''
                    return (
                      <td
                        key={c.key}
                        className={`r num ${has ? 'click' : 'faint'}`}
                        style={{ background: has ? tint(x!.z) : undefined, cursor: has ? 'pointer' : 'default' }}
                        title={has ? `${c.help}: ${x!.last!.toFixed(digits)}% (${when})${x!.z !== null ? ` · z ${zText(x!.z)} vs its 3-year norm` : ''}${x!.stale ? ' · not recently updated' : ''}` : x?.series_id ? 'Source mapped but no data yet' : 'No data source yet'}
                        onClick={() => has && x!.series_id && navigate(`series/${x!.series_id}`)}
                      >
                        {has ? `${x!.last!.toFixed(digits)}%` : '—'}
                        {has && x!.stale ? <span className="warn-text" title="Not recently updated"> *</span> : null}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
