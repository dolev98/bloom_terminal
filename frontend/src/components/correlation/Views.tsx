import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { ago } from '../../lib/format'
import { Card, Empty, ErrorBox, Help, Loading } from '../../ui'
import EChart from '../charts/EChart'
import { matrixOption } from './charts'
import { SeriesPicker, useSeriesName } from './Pickers'
import { FREQ_WORD, HELP, type DiscoverResult, type MatrixResult, lagWords, rho, strength } from './words'

const qs = (o: Record<string, string | number | undefined>) =>
  Object.entries(o)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
    .join('&')

const UNIVERSES: [string, string][] = [
  ['catalog', 'All daily series in the data catalog'],
  ['watchlist', 'Your watchlist (closing prices)'],
  ['cat:rates', 'Interest rates'],
  ['cat:fx', 'Currencies'],
  ['cat:equity', 'Stock indices'],
  ['cat:credit', 'Credit spreads'],
  ['cat:commodity', 'Commodities'],
  ['cty:US', 'United States only'],
  ['cty:IL', 'Israel only'],
]
const WINDOWS: [string, string][] = [
  ['365', 'Past year'],
  ['730', 'Past 2 years'],
  ['1825', 'Past 5 years'],
]

export function MatrixView({ onCompare }: { onCompare: (a: string, b: string) => void }) {
  const [universe, setUniverse] = useState('catalog')
  const [win, setWin] = useState('730')
  const params = useMemo(() => {
    const [kind, val] = universe.split(':')
    return { universe: kind === 'watchlist' ? 'watchlist' : 'catalog', category: kind === 'cat' ? val : undefined, country: kind === 'cty' ? val : undefined, window: win }
  }, [universe, win])
  const q = useQuery({ queryKey: ['corr-matrix', params], queryFn: () => api<MatrixResult>(`/api/correlation/matrix?${qs(params)}`), retry: false, staleTime: 300_000 })
  const m = q.data
  const name = (id: string) => m?.names?.[id] ?? id
  const built = useMemo(() => (m ? matrixOption(m, (id) => m.names?.[id] ?? id) : null), [m])
  const clusters = useMemo(() => {
    if (!m) return []
    const g = new Map<number, string[]>()
    m.labels.forEach((l, i) => g.set(m.clusters[i], [...(g.get(m.clusters[i]) ?? []), l]))
    return [...g.values()].filter((v) => v.length > 1)
  }, [m])
  return (
    <Card
      title="Correlation matrix"
      hint="How the daily changes of many series move together — click any square to compare that pair"
      actions={
        <>
          <select value={universe} onChange={(e) => setUniverse(e.target.value)} aria-label="Which series">
            {UNIVERSES.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
          <select value={win} onChange={(e) => setWin(e.target.value)} aria-label="Period">
            {WINDOWS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </>
      }
      foot={m ? `Changes are % changes for prices and basis-point changes for yields. Calculated ${ago(m.computed_at)} from stored data.` : undefined}
    >
      {q.isLoading && <Loading lines={8} />}
      {q.error && (String(q.error).includes('422') ? <Empty title="Not enough series with data">This selection has fewer than two series with enough stored history. Pick a broader set.</Empty> : <ErrorBox error={q.error} what="the matrix" />)}
      {m && built && (
        <>
          <div className="row small muted" style={{ marginBottom: 8 }}>
            <span>{m.n_series} series</span>
            {m.pc1_share !== null && (
              <span>
                · one common factor explains {Math.round(m.pc1_share * 100)}% of all their movement <Help text={HELP.pc1} />
              </span>
            )}
            {m.dropped && m.dropped.length > 0 && <span title={m.dropped.join(', ')}>· {m.dropped.length} left out for lack of overlapping data</span>}
          </div>
          <EChart
            option={built.option}
            height={Math.max(440, 20 * m.labels.length + 180)}
            onClick={(p) => {
              const d = p.data as [number, number, unknown]
              if (!Array.isArray(d)) return
              const a = m.labels[built.order[d[1]]]
              const b = m.labels[built.order[d[0]]]
              if (a && b && a !== b) onCompare(a, b)
            }}
          />
          {clusters.length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div className="section-title">Groups that tend to move together</div>
              <ul className="small" style={{ margin: 0, paddingLeft: 18, color: 'var(--text-2)' }}>
                {clusters.map((ids, i) => (
                  <li key={i}>{ids.map(name).join(' · ')}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </Card>
  )
}

function Spark({ v }: { v: number[] }) {
  if (v.length < 2) return null
  const w = 90
  const h = 22
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h / 2 - (y * h) / 2).toFixed(1)}`).join(' ')
  return (
    <svg width={w} height={h} style={{ display: 'block' }} aria-label="Rolling correlation">
      <line x1={0} x2={w} y1={h / 2} y2={h / 2} stroke="#343c4a" strokeWidth={1} />
      <polyline points={pts} fill="none" stroke="#f5a524" strokeWidth={1.5} />
    </svg>
  )
}

export function DiscoverView({ x, setX, onCompare }: { x: string; setX: (v: string) => void; onCompare: (a: string, b: string) => void }) {
  const [universe, setUniverse] = useState('catalog')
  const [win, setWin] = useState('730')
  const xName = useSeriesName(x)
  const q = useQuery({ queryKey: ['corr-discover', x, universe, win], queryFn: () => api<DiscoverResult>(`/api/correlation/discover?${qs({ x, universe, window: win })}`), enabled: !!x, retry: false, staleTime: 300_000 })
  const d = q.data
  const freq = d?.freq ?? '1d'
  return (
    <Card
      title="Find related series"
      hint="Which stored series move most closely with the one you pick"
      foot={d ? `Based on ${FREQ_WORD[freq] ?? ''} changes over the chosen period; ${d.n_candidates} series checked. Calculated ${ago(d.computed_at)}.` : undefined}
    >
      <div className="row" style={{ marginBottom: 12 }}>
        <span className="muted">Most related to</span>
        <SeriesPicker value={x} onChange={setX} placeholder="Search a series or type a ticker" ariaLabel="Series to explore" />
        <select value={universe} onChange={(e) => setUniverse(e.target.value)} aria-label="Search in">
          <option value="catalog">Search the data catalog</option>
          <option value="watchlist">Search your watchlist</option>
        </select>
        <select value={win} onChange={(e) => setWin(e.target.value)} aria-label="Period">
          {WINDOWS.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {!x && <Empty title="Pick a series to start">Search by name (e.g. “shekel”, “10-year”) or type a ticker such as TEVA.TA.</Empty>}
      {q.isLoading && x && <Loading lines={8} />}
      {q.error && <ErrorBox error={q.error} what="related series" />}
      {d && d.rows.length === 0 && <Empty title="Nothing overlaps enough">None of the other series has enough history overlapping with {xName}.</Empty>}
      {d && d.rows.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Series</th>
                <th>Relationship with {xName}</th>
                <th className="r">
                  Correlation <Help text={HELP.pearson} />
                </th>
                <th className="r">
                  Rank <Help text={HELP.spearman} />
                </th>
                <th>
                  Timing <Help text="Whether the series tends to move before or after the one you picked (strongest correlation across shifts of up to a few periods)." />
                </th>
                <th>
                  Recent stability <Help text={HELP.rolling} />
                </th>
                <th className="r">Observations</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((row) => (
                <tr key={row.y} className="click" onClick={() => onCompare(x, row.y)} title="Compare these two">
                  <td>
                    <div>{row.name ?? row.y}</div>
                    <div className="xs faint mono">{row.y}</div>
                  </td>
                  <td className="small muted">{strength(row.pearson)}</td>
                  <td className="r num strong">{rho(row.pearson)}</td>
                  <td className="r num muted">{rho(row.spearman)}</td>
                  <td className="small">{lagWords(row.best_lag, freq)}</td>
                  <td>
                    <Spark v={row.spark} />
                  </td>
                  <td className="r num muted">{row.n}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
