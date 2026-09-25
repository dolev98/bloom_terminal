import type { Route } from '../lib/router'
import { useTitle } from '../lib/router'
import { Page } from '../ui'
import { Suspense, lazy } from 'react'
import Dashboard from '../pages/Dashboard'
import { Loading } from '../ui'

// Pages other than the dashboard load on first visit, so the app starts faster.
const Watchlist = lazy(() => import('../pages/Watchlist'))
const CompanyPage = lazy(() => import('../pages/company/CompanyPage'))
const NewsPage = lazy(() => import('../pages/News'))
const NotesPage = lazy(() => import('../pages/Notes'))
const CalendarPage = lazy(() => import('../pages/Calendar'))
const MacroPage = lazy(() => import('../pages/Macro'))
const CorrelationPage = lazy(() => import('../pages/Correlation'))
const AlertsPage = lazy(() => import('../pages/Alerts'))
const DataCatalogPage = lazy(() => import('../pages/DataCatalog'))
const SeriesPage = lazy(() => import('../pages/Series'))
const StatusPage = lazy(() => import('../pages/Status'))
const SettingsPage = lazy(() => import('../pages/Settings'))

const TITLES: Record<string, string> = {
  watchlist: 'Watchlist',
  news: 'News',
  notes: 'Notes',
  calendar: 'Calendar',
  macro: 'Macro',
  correlation: 'Correlations',
  alerts: 'Alerts',
  data: 'Data catalog',
  series: 'Data series',
  status: 'Status',
  settings: 'Settings',
}

/** Each page renders its own <Page> header. Company pages set their own title (the ticker). */
export function Outlet({ route }: { route: Route }) {
  const [a] = route.path
  useTitle(a === 'company' ? (route.path[1] ?? '').toUpperCase() : a ? (TITLES[a] ?? '') : 'Dashboard')
  return (
    <Suspense fallback={<div className="page"><Loading lines={6} /></div>}>
      <Pages route={route} />
    </Suspense>
  )
}

function Pages({ route }: { route: Route }) {
  const [a, b, c] = route.path
  switch (a) {
    case undefined:
      return <Dashboard />
    case 'watchlist':
      return <Watchlist />
    case 'company':
      return b ? <CompanyPage key={b.toUpperCase()} ticker={b.toUpperCase()} tab={c ?? 'overview'} /> : <NotFound />
    case 'news':
      return <NewsPage />
    case 'notes':
      return <NotesPage />
    case 'calendar':
      return <CalendarPage country={route.query.country} />
    case 'macro':
      return <MacroPage country={(b ?? 'US').toUpperCase()} />
    case 'correlation':
      return <CorrelationPage a={route.query.a} b={route.query.b} pair={route.query.pair} />
    case 'alerts':
      return <AlertsPage />
    case 'data':
      return <DataCatalogPage />
    case 'series':
      return <SeriesPage key={route.path.slice(1).join('/')} seriesId={route.path.slice(1).join('/')} />
    case 'status':
      return <StatusPage />
    case 'settings':
      return <SettingsPage />
    default:
      return <NotFound />
  }
}

function NotFound() {
  return (
    <Page title="Page not found">
      <a href="#/">Back to the dashboard</a>
    </Page>
  )
}
