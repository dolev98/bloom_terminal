import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { navigate } from '../lib/router'
import { ErrorBox, Flag, Loading, Page, Segmented } from '../ui'
import { CompareTable, NextReleases, RegimeCard, SectionCard } from '../components/macro/MacroParts'
import { SECTIONS, type Country, type Dashboard } from '../components/macro/indicators'

type View = 'country' | 'compare'

export default function MacroPage({ country }: { country: string }) {
  const cc = country.toUpperCase()
  const qc = useQueryClient()
  const [view, setView] = useState<View>('country')
  const [busy, setBusy] = useState(false)
  const [refreshNote, setRefreshNote] = useState<string | null>(null)
  const countries = useQuery({ queryKey: ['macro-countries'], queryFn: () => api<Country[]>('/api/macro/countries'), staleTime: 3_600_000 })
  const dash = useQuery({ queryKey: ['macro-dash', cc], queryFn: () => api<Dashboard>(`/api/macro/${cc}`), enabled: view === 'country', refetchInterval: 600_000 })
  const d = dash.data
  const name = countries.data?.find((c) => c.cc === cc)?.name ?? d?.name ?? cc

  const refresh = async () => {
    setBusy(true)
    setRefreshNote(null)
    try {
      const r = await api<{ ok: number; errors: number }>(`/api/macro/${cc}/refresh`, { method: 'POST' })
      setRefreshNote(r.errors ? `Updated ${r.ok} series; ${r.errors} could not be fetched.` : `Updated ${r.ok} series.`)
      await qc.invalidateQueries({ queryKey: ['macro-dash', cc] })
      await qc.invalidateQueries({ queryKey: ['macro-heat'] })
    } catch (e) {
      setRefreshNote(`Refresh failed: ${String((e as Error).message ?? e)}`)
    } finally {
      setBusy(false)
    }
  }

  const tilesFor = (id: string) => d?.sections.find((s) => s.name === id)?.tiles ?? []

  return (
    <Page
      title={
        view === 'compare' ? (
          'Compare economies'
        ) : (
          <>
            <Flag cc={cc} /> {name} economy
          </>
        )
      }
      sub={view === 'compare' ? 'The same key indicators side by side for every covered country.' : 'Latest official figures by theme. Click any row to see its full history.'}
      actions={
        <>
          <Segmented value={view} options={[['country', 'Country overview'], ['compare', 'Compare countries']]} onChange={setView} />
          {view === 'country' && (
            <button disabled={busy} onClick={refresh} title={`Fetch the latest values for every ${name} series`}>
              {busy ? 'Refreshing…' : 'Refresh data'}
            </button>
          )}
        </>
      }
    >
      {view === 'country' && (
        <div className="row" style={{ marginBottom: 16 }}>
          {(countries.data ?? []).map((c) => (
            <span key={c.cc} className={`chip ${c.cc === cc ? 'on' : ''}`} onClick={() => navigate(`macro/${c.cc}`)}>
              <Flag cc={c.cc} /> {c.name}
            </span>
          ))}
          {refreshNote && <span className="small muted">{refreshNote}</span>}
        </div>
      )}

      {view === 'compare' && <CompareTable countries={countries.data ?? []} />}

      {view === 'country' && (
        <>
          {dash.isLoading && <Loading lines={8} />}
          {dash.error && <ErrorBox error={dash.error} what={`the ${name} dashboard`} />}
          {d && (
            <div className="stack">
              <div className="grid-2">
                <RegimeCard d={d} />
                <NextReleases cc={cc} name={name} />
              </div>
              <div className="grid-2" style={{ alignItems: 'start' }}>
                {SECTIONS.map((s) => (
                  <SectionCard key={s.id} cc={cc} countryName={name} section={s} tiles={tilesFor(s.id)} />
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <div className="attrib">
        Sources: FRED, Bank of Israel, Central Bureau of Statistics (Israel), Eurostat, ECB, BIS, IMF, OECD and ONS (Open Government Licence v3.0). This product uses the FRED® API but is not endorsed or certified by
        the Federal Reserve Bank of St. Louis.
      </div>
    </Page>
  )
}
