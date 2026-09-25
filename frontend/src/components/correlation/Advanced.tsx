import { useMemo, useState } from 'react'
import { Card, Help } from '../../ui'
import EChart from '../charts/EChart'
import { betaOption, leadLagOption, spreadOption } from './charts'
import { FREQ_OPTIONS, HELP, PERIOD_WORD, TRANSFORM_OPTIONS, type PairResult, type RegimePreset, lagWords, pval, rho, verdictWord } from './words'

export type PairConfig = {
  a: string
  b: string
  ta: string
  tb: string
  freq: string
  window: string
  lagB: number
  invertB: boolean
  pubLag: boolean
  regime: string
  leadlag: boolean
  coint: boolean
  granger: boolean
}
export const DEFAULT_OPTIONS = { ta: 'default', tb: 'default', freq: 'auto', window: '', lagB: 0, invertB: false, pubLag: false, regime: '', leadlag: false, coint: false, granger: false }

/** The collapsed "Options" panel: transforms, frequency, window, lag, invert, publication lag, regimes, extra tests. */
export function OptionsPanel({
  cfg,
  setCfg,
  r,
  nameA,
  nameB,
  regimes,
  onSave,
}: {
  cfg: PairConfig
  setCfg: (p: Partial<PairConfig>) => void
  r: PairResult | undefined
  nameA: string
  nameB: string
  regimes: RegimePreset[]
  onSave: (name: string) => Promise<void>
}) {
  const [saveName, setSaveName] = useState('')
  const [saved, setSaved] = useState<string | null>(null)
  const tfLabel = (t: string | undefined) => TRANSFORM_OPTIONS.find(([v]) => v === t)?.[1] ?? t
  return (
    <div style={{ borderTop: '1px solid var(--border)', marginTop: 14, paddingTop: 14 }}>
      <div className="form">
        <label>How to compare {nameA}</label>
        <select value={cfg.ta} onChange={(e) => setCfg({ ta: e.target.value })}>
          {TRANSFORM_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {v === 'default' && r ? `${l}: ${tfLabel(r.a.transform)?.toLowerCase()}` : l}
            </option>
          ))}
        </select>
        <label>How to compare {nameB}</label>
        <select value={cfg.tb} onChange={(e) => setCfg({ tb: e.target.value })}>
          {TRANSFORM_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {v === 'default' && r ? `${l}: ${tfLabel(r.b.transform)?.toLowerCase()}` : l}
            </option>
          ))}
        </select>
        <label>Data frequency</label>
        <select value={cfg.freq} onChange={(e) => setCfg({ freq: e.target.value })}>
          {FREQ_OPTIONS.map(([v, l]) => (
            <option key={v} value={v}>
              {v === 'auto' && r ? `${l} (${r.freq === '1d' ? 'daily' : r.freq === '1w' ? 'weekly' : r.freq === '1mo' ? 'monthly' : r.freq})` : l}
            </option>
          ))}
        </select>
        <label>
          Rolling window <Help text={HELP.rolling} />
        </label>
        <div className="row">
          <input className="num" type="number" min={5} max={2000} value={cfg.window} placeholder={r ? String(r.window) : 'auto'} onChange={(e) => setCfg({ window: e.target.value })} style={{ width: 90 }} />
          <span className="small muted">{PERIOD_WORD[r?.freq ?? '1d'] ?? 'periods'}</span>
        </div>
        <label>
          Shift {nameB} in time <Help text={HELP.lag} />
        </label>
        <div className="row">
          <input className="num" type="number" min={-250} max={250} value={cfg.lagB} onChange={(e) => setCfg({ lagB: Number(e.target.value) || 0 })} style={{ width: 90 }} />
          <span className="small muted">{cfg.lagB ? lagWords(cfg.lagB, r?.freq ?? '1d').replace('Moves first', `${nameB} moves first`).replace('Follows', `${nameB} follows`) : 'No shift'}</span>
        </div>
        <label>Flip {nameB}</label>
        <label className="check">
          <input type="checkbox" checked={cfg.invertB} onChange={(e) => setCfg({ invertB: e.target.checked })} /> Multiply by −1 (useful when a rise in B means the opposite, e.g. yields vs bond prices)
        </label>
        <label>Publication delay</label>
        <label className="check">
          <input type="checkbox" checked={cfg.pubLag} onChange={(e) => setCfg({ pubLag: e.target.checked })} /> Use economic data only from the date it was actually published
        </label>
        <label>Split by market regime</label>
        <select value={cfg.regime} onChange={(e) => setCfg({ regime: e.target.value })}>
          <option value="">Don't split</option>
          {regimes.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        <label>Extra tests</label>
        <div className="row">
          <label className="check">
            <input type="checkbox" checked={cfg.leadlag} onChange={(e) => setCfg({ leadlag: e.target.checked })} /> Which one moves first
          </label>
          <label className="check">
            <input type="checkbox" checked={cfg.coint} onChange={(e) => setCfg({ coint: e.target.checked })} /> Long-run link (cointegration) <Help text={HELP.coint} />
          </label>
          <label className="check">
            <input type="checkbox" checked={cfg.granger} onChange={(e) => setCfg({ granger: e.target.checked })} /> Predictive power (Granger) <Help text={HELP.granger} />
          </label>
        </div>
        <label>Save as a preset</label>
        <div className="row">
          <input value={saveName} onChange={(e) => setSaveName(e.target.value)} placeholder={`${nameA} vs ${nameB}`} style={{ width: 280 }} />
          <button
            disabled={!cfg.a || !cfg.b}
            onClick={async () => {
              const n = saveName.trim() || `${nameA} vs ${nameB}`
              try {
                await onSave(n)
                setSaved(`Saved “${n}”.`)
                setSaveName('')
              } catch (e) {
                setSaved(`Could not save: ${String((e as Error).message ?? e)}`)
              }
            }}
          >
            Save
          </button>
          <button className="ghost" onClick={() => setCfg(DEFAULT_OPTIONS)}>
            Reset options
          </button>
          {saved && <span className="small muted">{saved}</span>}
        </div>
      </div>
    </div>
  )
}

/** Results of the advanced options: stationarity, regression, rolling beta, lead-lag, cointegration, Granger, regimes. */
export function AdvancedResults({ r, cfg, nameA, nameB }: { r: PairResult; cfg: PairConfig; nameA: string; nameB: string }) {
  const ll = useMemo(() => leadLagOption(r), [r])
  const beta = useMemo(() => betaOption(r), [r])
  const spread = useMemo(() => spreadOption(r), [r])
  const st = r.stationarity
  const o = r.ols
  const co = r.cointegration
  const gr = r.granger
  const pw = PERIOD_WORD[r.freq] ?? 'periods'
  return (
    <Card title="Detailed statistics" hint="For checking the result — the summary above is what matters for most decisions">
      <div className="grid-2">
        {st && (
          <div>
            <div className="section-title" style={{ marginTop: 0 }}>
              Trend check <Help text={HELP.stationarity} />
            </div>
            <table className="table compact">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Behaviour</th>
                  <th className="r">ADF p</th>
                  <th className="r">KPSS p</th>
                </tr>
              </thead>
              <tbody>
                {(
                  [
                    [`${nameA}, as compared`, st.a],
                    [`${nameB}, as compared`, st.b],
                    [`${nameA}, raw level`, st.a_level],
                    [`${nameB}, raw level`, st.b_level],
                  ] as const
                ).map(([label, s]) => (
                  <tr key={label}>
                    <td className="small">{label}</td>
                    <td className={`small ${s.verdict === 'I(1)-like' ? 'warn-text' : ''}`}>{verdictWord(s.verdict)}</td>
                    <td className="r num muted">{pval(s.adf_p)}</td>
                    <td className="r num muted">{pval(s.kpss_p)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {o && (
          <div>
            <div className="section-title" style={{ marginTop: 0 }}>
              Regression of {nameA} on {nameB}
            </div>
            <dl className="kv">
              <dt>Slope (β)</dt>
              <dd className="num">{o.beta === null ? '—' : o.beta.toPrecision(3)}</dd>
              <dt>
                t-statistic <Help text="Robust (HAC) t-statistic of the slope. Beyond about ±2 the slope is statistically meaningful." />
              </dt>
              <dd className="num">
                {o.t_beta === null ? '—' : o.t_beta.toFixed(1)} <span className="faint">(p {pval(o.p_beta)})</span>
              </dd>
              <dt>R²</dt>
              <dd className="num">{o.r2 === null ? '—' : `${(o.r2 * 100).toFixed(1)}% of the variation explained`}</dd>
              <dt>
                Durbin–Watson <Help text="Checks leftover autocorrelation; values near 2 are fine, far below 2 means the errors are persistent." />
              </dt>
              <dd className="num">{o.dw === null ? '—' : o.dw.toFixed(2)}</dd>
            </dl>
          </div>
        )}
      </div>

      {beta && (
        <>
          <div className="section-title">Rolling slope (β) over time</div>
          <EChart option={beta} height={180} />
        </>
      )}

      {cfg.leadlag && ll && r.lead_lag && (
        <>
          <div className="section-title">Which one moves first</div>
          <p className="small" style={{ color: 'var(--text-2)' }}>
            {r.lead_lag.best_lag === 0 || r.lead_lag.best_lag === null
              ? `The two move together in the same ${pw.replace(/s$/, '')}; shifting either one does not strengthen the link.`
              : `The link is strongest when ${nameB} is shifted by ${r.lead_lag.best_lag} ${pw} (${lagWords(r.lead_lag.best_lag, r.freq).toLowerCase()}), ρ = ${rho(r.lead_lag.best_corr)}.`}{' '}
            <span className="muted">Bars outside the dashed lines are unlikely to be chance.</span>
          </p>
          <EChart option={ll} height={200} />
        </>
      )}

      {cfg.coint && co && (
        <>
          <div className="section-title">
            Long-run link (cointegration) <Help text={HELP.coint} />
          </div>
          <p className="small" style={{ color: 'var(--text-2)' }}>
            {co.verdict === 'cointegrated'
              ? `The levels of ${nameA} and ${nameB} appear tied together: the gap between them keeps returning to normal`
              : co.verdict === 'weak'
                ? 'There is weak evidence of a long-run link between the levels'
                : 'No long-run link between the levels was found'}
            {` (Engle–Granger p ${pval(co.eg_p)}`}
            {co.half_life !== null && co.verdict !== 'none' ? `; a gap typically halves in ${co.half_life.toFixed(0)} ${pw}` : ''}
            {`). The gap is now ${co.z_last === null ? '—' : co.z_last.toFixed(1)} standard deviations from its norm.`}
          </p>
          {spread && <EChart option={spread} height={180} />}
        </>
      )}

      {cfg.granger && gr && (
        <>
          <div className="section-title">
            Predictive power (Granger) <Help text={HELP.granger} />
          </div>
          <ul className="small" style={{ margin: 0, paddingLeft: 18, color: 'var(--text-2)' }}>
            <li>
              Past {nameB} {gr.b_to_a && (gr.b_to_a.p ?? 1) < 0.05 ? 'helps' : 'does not help'} predict {nameA} (p {pval(gr.b_to_a?.p)}, {gr.b_to_a?.lag ?? '—'} {pw} of history).
            </li>
            <li>
              Past {nameA} {gr.a_to_b && (gr.a_to_b.p ?? 1) < 0.05 ? 'helps' : 'does not help'} predict {nameB} (p {pval(gr.a_to_b?.p)}, {gr.a_to_b?.lag ?? '—'} {pw} of history).
            </li>
          </ul>
        </>
      )}

      {r.regimes && (
        <>
          <div className="section-title">Correlation by regime: {r.regimes.name}</div>
          <table className="table compact">
            <thead>
              <tr>
                <th>Regime</th>
                <th className="r">Observations</th>
                <th className="r">Correlation</th>
                <th className="r">Rank corr.</th>
                <th className="r">95% range</th>
              </tr>
            </thead>
            <tbody>
              {r.regimes.rows.map((row) => (
                <tr key={row.regime}>
                  <td>{row.regime}</td>
                  <td className="r num">{row.n}</td>
                  <td className="r num strong">{rho(row.pearson)}</td>
                  <td className="r num muted">{rho(row.spearman)}</td>
                  <td className="r num muted">
                    {rho(row.ci[0])} to {rho(row.ci[1])}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  )
}
