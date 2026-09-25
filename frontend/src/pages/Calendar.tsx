import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { Card, Empty, ErrorBox, Loading, Page, Segmented } from '../ui'
import { AddEventDrawer, EventDrawer } from '../components/calendar/EventDrawer'
import { Agenda, CentralBanksTable, MonthGrid } from '../components/calendar/Views'
import {
  COUNTRY_CHIPS,
  KIND_GROUPS,
  type CalEvent,
  type CentralBank,
  type KindGroup,
  addDaysKey,
  dayHeading,
  dayKeyIL,
  keyLabel,
  kindGroup,
  mondayKey,
  todayKeyIL,
} from '../components/calendar/model'

type View = 'week' | 'month' | 'cb'
type Imp = '1' | '2' | '3'

const firstOfMonth = (key: string) => `${key.slice(0, 7)}-01`
const shiftMonth = (key: string, n: number) => {
  const d = new Date(`${key.slice(0, 7)}-01T12:00:00Z`)
  d.setUTCMonth(d.getUTCMonth() + n)
  return d.toISOString().slice(0, 10)
}

export default function CalendarPage({ country }: { country?: string }) {
  const qc = useQueryClient()
  const today = todayKeyIL()
  const [view, setView] = useState<View>('week')
  const [week, setWeek] = useState(mondayKey(today))
  const [month, setMonth] = useState(firstOfMonth(today))
  const initialChip = country ? COUNTRY_CHIPS.find((c) => c.codes.includes(country.toUpperCase()))?.id : undefined
  const [chips, setChips] = useState<Set<string>>(new Set(initialChip ? [initialChip] : []))
  const [kinds, setKinds] = useState<Set<KindGroup>>(new Set())
  const [imp, setImp] = useState<Imp>('2')
  const [showEarlier, setShowEarlier] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  const [pickedDay, setPickedDay] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const codes = useMemo(() => COUNTRY_CHIPS.filter((c) => chips.has(c.id)).flatMap((c) => c.codes), [chips])
  // Query one extra day on each side: the API filters by UTC date, the page groups by Israel date.
  const range = useMemo(() => {
    if (view === 'month') {
      const start = mondayKey(month)
      return { from: addDaysKey(start, -1), to: addDaysKey(start, 42) }
    }
    return { from: addDaysKey(week, -1), to: addDaysKey(week, 7) }
  }, [view, week, month])
  const base = { from: range.from, to: range.to, ...(codes.length ? { countries: codes.join(',') } : {}) }
  const qsEvents = new URLSearchParams({ ...base, min_importance: view === 'month' ? '1' : imp }).toString()
  const qsHol = new URLSearchParams({ ...base, kinds: 'holiday' }).toString()

  const events = useQuery({ queryKey: ['cal-events', qsEvents], queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?${qsEvents}`), enabled: view !== 'cb', refetchInterval: 120_000 })
  const hols = useQuery({ queryKey: ['cal-events', qsHol], queryFn: () => api<{ items: CalEvent[] }>(`/api/calendar/events?${qsHol}`), enabled: view !== 'cb', staleTime: 600_000 })
  const cbs = useQuery({ queryKey: ['cal-cb'], queryFn: () => api<CentralBank[]>('/api/calendar/central-banks'), enabled: view === 'cb', staleTime: 300_000 })

  const kindOk = (e: CalEvent) => !kinds.size || kinds.has(kindGroup(e))
  const items = useMemo(() => (events.data?.items ?? []).filter((e) => e.kind !== 'holiday' && kindOk(e)), [events.data, kinds]) // eslint-disable-line react-hooks/exhaustive-deps
  const holidays = useMemo(() => (!kinds.size || kinds.has('holiday') ? (hols.data?.items ?? []) : []), [hols.data, kinds])

  const weekDays = Array.from({ length: 7 }, (_, i) => addDaysKey(week, i))
  const isThisWeek = week === mondayKey(today)
  const earlier = isThisWeek && !showEarlier ? weekDays.filter((d) => d < today) : []
  const shownDays = weekDays.filter((d) => !earlier.includes(d))

  const toggle = <T,>(set: Set<T>, v: T, setter: (s: Set<T>) => void) => {
    const n = new Set(set)
    if (n.has(v)) n.delete(v)
    else n.add(v)
    setter(n)
  }
  const refresh = async () => {
    setRefreshing(true)
    try {
      await api('/api/calendar/refresh?scope=all', { method: 'POST' })
      await qc.invalidateQueries({ queryKey: ['cal-events'] })
    } finally {
      setRefreshing(false)
    }
  }

  const icsQs = new URLSearchParams({
    ...base,
    min_importance: imp,
    ...(kinds.size ? { kinds: KIND_GROUPS.filter((k) => kinds.has(k.id)).flatMap((k) => k.kinds).join(',') } : {}),
  })
  const weekLabel = `${keyLabel(week, { day: 'numeric', month: 'short' })} – ${keyLabel(addDaysKey(week, 6), { day: 'numeric', month: 'short', year: 'numeric' })}`
  const monthLabel = keyLabel(month, { month: 'long', year: 'numeric' })
  const nothing = !events.isLoading && !events.error && items.length === 0 && holidays.length === 0

  const filters = (
    <div className="stack" style={{ gap: 8, marginBottom: 14 }}>
      <div className="row">
        <span className={`chip ${chips.size === 0 ? 'on' : ''}`} onClick={() => setChips(new Set())}>
          All countries
        </span>
        {COUNTRY_CHIPS.map((c) => (
          <span key={c.id} className={`chip ${chips.has(c.id) ? 'on' : ''}`} onClick={() => toggle(chips, c.id, setChips)}>
            {c.label}
          </span>
        ))}
      </div>
      <div className="row">
        <span className="small muted">Importance</span>
        <Segmented value={imp} options={[['1', 'All'], ['2', 'Medium+'], ['3', 'High only']]} onChange={setImp} />
        <span style={{ width: 8 }} />
        <span className={`chip ${kinds.size === 0 ? 'on' : ''}`} onClick={() => setKinds(new Set())}>
          All types
        </span>
        {KIND_GROUPS.map((k) => (
          <span key={k.id} className={`chip ${kinds.has(k.id) ? 'on' : ''}`} onClick={() => toggle(kinds, k.id, setKinds)}>
            {k.label}
          </span>
        ))}
      </div>
    </div>
  )

  return (
    <Page
      title="Calendar"
      sub="Economic releases, central-bank decisions and market holidays. Times are Israel time, with New York time underneath."
      actions={
        <>
          <Segmented value={view} options={[['week', 'Week'], ['month', 'Month'], ['cb', 'Central banks']]} onChange={setView} />
          <button onClick={() => setAdding(true)}>Add event / risk window</button>
        </>
      }
    >
      {view !== 'cb' && filters}

      {view === 'week' && (
        <Card
          title={isThisWeek ? 'This week' : `Week of ${weekLabel}`}
          hint={isThisWeek ? weekLabel : undefined}
          actions={
            <>
              <button className="icon" onClick={() => setWeek(addDaysKey(week, -7))} aria-label="Previous week">
                ‹
              </button>
              {!isThisWeek && <button onClick={() => setWeek(mondayKey(today))}>This week</button>}
              <button className="icon" onClick={() => setWeek(addDaysKey(week, 7))} aria-label="Next week">
                ›
              </button>
            </>
          }
          foot={
            <>
              “Released · n/a” means the release has happened but our sources do not carry the actual figure. Schedules from official release calendars and central banks; forecasts from ForexFactory / FMP; holidays © Hebcal.com (CC BY 4.0) and Nager.Date. This product uses the FRED® API but is not
              endorsed or certified by the Federal Reserve Bank of St. Louis. ·{' '}
              <a href={`/api/calendar/export.ics?${icsQs}`} download>
                Export these events (.ics)
              </a>
            </>
          }
        >
          {earlier.length > 0 && (
            <button className="ghost small" style={{ marginBottom: 8 }} onClick={() => setShowEarlier(true)}>
              Show earlier this week ({keyLabel(earlier[0], { weekday: 'short' })}–{keyLabel(earlier[earlier.length - 1], { weekday: 'short' })})
            </button>
          )}
          {events.isLoading && <Loading lines={6} />}
          {events.error && <ErrorBox error={events.error} what="the calendar" />}
          {nothing ? (
            <Empty
              title="No events match your filters this week"
              actions={
                imp !== '1' || kinds.size || chips.size ? (
                  <button
                    onClick={() => {
                      setImp('1')
                      setKinds(new Set())
                      setChips(new Set())
                    }}
                  >
                    Show everything
                  </button>
                ) : (
                  <button disabled={refreshing} onClick={refresh}>
                    {refreshing ? 'Refreshing…' : 'Refresh the calendar'}
                  </button>
                )
              }
            >
              {imp !== '1' || kinds.size || chips.size ? 'Try widening the filters.' : 'The calendar has no events stored for these dates yet.'}
            </Empty>
          ) : (
            !events.isLoading && !events.error && <Agenda days={shownDays} events={items} holidays={holidays} onSelect={setSelected} selectedId={selected} />
          )}
        </Card>
      )}

      {view === 'month' && (
        <MonthView
          month={month}
          monthLabel={monthLabel}
          setMonth={setMonth}
          today={today}
          loading={events.isLoading}
          error={events.error}
          events={items}
          holidays={holidays}
          imp={imp}
          pickedDay={pickedDay}
          setPickedDay={setPickedDay}
          onSelect={setSelected}
          selected={selected}
        />
      )}

      {view === 'cb' && (
        <Card title="Central banks" hint="Current policy rate and the next scheduled decision" foot="Decision dates from each central bank's official calendar. Rates: FRED, Bank of Israel, and BIS (monthly, end of month) for the others.">
          {cbs.isLoading && <Loading lines={8} />}
          {cbs.error && <ErrorBox error={cbs.error} what="central banks" />}
          {cbs.data && <CentralBanksTable banks={cbs.data} />}
        </Card>
      )}

      {selected !== null && (
        <EventDrawer
          key={selected}
          id={selected}
          onClose={() => setSelected(null)}
          onDeleted={() => {
            setSelected(null)
            qc.invalidateQueries({ queryKey: ['cal-events'] })
          }}
        />
      )}
      {adding && (
        <AddEventDrawer
          onClose={() => setAdding(false)}
          onSaved={() => {
            setAdding(false)
            qc.invalidateQueries({ queryKey: ['cal-events'] })
          }}
        />
      )}
    </Page>
  )
}

function MonthView({
  month,
  monthLabel,
  setMonth,
  today,
  loading,
  error,
  events,
  holidays,
  imp,
  pickedDay,
  setPickedDay,
  onSelect,
  selected,
}: {
  month: string
  monthLabel: string
  setMonth: (m: string) => void
  today: string
  loading: boolean
  error: unknown
  events: CalEvent[]
  holidays: CalEvent[]
  imp: Imp
  pickedDay: string | null
  setPickedDay: (d: string | null) => void
  onSelect: (id: number) => void
  selected: number | null
}) {
  const dayEvents = pickedDay ? events.filter((e) => dayKeyIL(e.release_ts) === pickedDay && e.importance >= Number(imp)) : []
  const dayHols = pickedDay ? holidays.filter((h) => dayKeyIL(h.release_ts) === pickedDay) : []
  return (
    <div className="stack">
      <Card
        title={monthLabel}
        hint="Market-moving events and holidays per day — click a day for its list"
        actions={
          <>
            <button className="icon" onClick={() => setMonth(shiftMonth(month, -1))} aria-label="Previous month">
              ‹
            </button>
            {month !== firstOfMonth(today) && <button onClick={() => setMonth(firstOfMonth(today))}>This month</button>}
            <button className="icon" onClick={() => setMonth(shiftMonth(month, 1))} aria-label="Next month">
              ›
            </button>
          </>
        }
      >
        {loading && <Loading lines={5} />}
        {error ? <ErrorBox error={error} what="the calendar" /> : null}
        {!loading && !error && <MonthGrid month={month} events={events} holidays={holidays} selected={pickedDay} onPick={(d) => setPickedDay(d === pickedDay ? null : d)} />}
      </Card>
      {pickedDay && (
        <Card title={dayHeading(pickedDay, today)} actions={<button className="ghost" onClick={() => setPickedDay(null)}>Close</button>}>
          {dayEvents.length === 0 && dayHols.length === 0 ? (
            <div className="faint small">Nothing at this importance level on this day.</div>
          ) : (
            <Agenda days={[pickedDay]} events={dayEvents} holidays={dayHols} onSelect={onSelect} selectedId={selected} />
          )}
        </Card>
      )}
    </div>
  )
}
