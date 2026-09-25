/** News types (fields as returned by /api/news/*) and plain-English labels shared by the news page and tab. */

export type TickerRef = { ticker: string; relevance: number; method: string }
export type ClusterFiling = { accession: string; form: string; items: string[]; ticker: string; url: string; filed_at: string | null }
export type Story = {
  id: number
  kind: string
  lang: string
  first_seen: string
  last_seen: string
  n_items: number
  n_publishers: number
  importance: number
  summary: string | null
  why_it_matters: string | null
  sentiment: number | null
  facts: string[]
  title: string
  url: string
  publisher: string | null
  sources: string[]
  publishers: string[]
  tickers: TickerRef[]
  filing: ClusterFiling | null
}
export type StoryItem = { id: number; source_id: string; url: string; title: string; snippet: string | null; lang: string; published_at: string; publisher: string | null }
export type StoryDetail = Story & { items: StoryItem[] }
export type FilingRow = { accession: string; ticker: string; form: string; items: string[]; filed_at: string | null; accepted_at: string | null; url: string; cluster_id: number | null }
export type NewsStats = { ticker: string; days: number; ts: string[]; count: number[]; sentiment: (number | null)[] }

/** "Important" = importance score >= 30 (scores on live data run ~15–40; >= 30 keeps ~25 stories a day
 *  across a 12-ticker watchlist). Dots: 3 for >= 40, 2 for >= 30, 1 otherwise. */
export const IMPORTANT = 30
export const importanceLevel = (v: number) => (v >= 40 ? 3 : v >= IMPORTANT ? 2 : 1)
export const IMPORTANCE_HELP =
  'How important we think this is: coverage by several outlets, source type, filing type, price reaction. "Important only" shows stories with 2 or 3 dots.'

export type Period = '24h' | '3d' | '7d'
export const PERIODS: [Period, string][] = [
  ['24h', '24 hours'],
  ['3d', '3 days'],
  ['7d', 'Week'],
]

export const PERIOD_PHRASE: Record<Period, string> = { '24h': 'the last 24 hours', '3d': 'the last 3 days', '7d': 'the last week' }

export type StoryType = 'news' | 'filing' | 'wire'
export const TYPES: [StoryType, string][] = [
  ['news', 'News'],
  ['filing', 'Company filings'],
  ['wire', 'Press releases'],
]
export const storyType = (kind: string): StoryType => (kind === 'filing' ? 'filing' : kind === 'wire' ? 'wire' : 'news')

/** Tone of the wording, -1..+1. Only shown when it clearly leans one way. */
export function sentimentLabel(s: number | null | undefined): { text: string; cls: string } | null {
  if (s === null || s === undefined || !Number.isFinite(s)) return null
  if (s >= 0.3) return { text: 'Positive', cls: 'up' }
  if (s <= -0.3) return { text: 'Negative', cls: 'down' }
  return { text: 'Neutral', cls: 'muted' }
}

const FORMS: Record<string, string> = {
  '10-K': 'Annual report',
  '10-Q': 'Quarterly report',
  '8-K': 'Current report',
  '6-K': 'Foreign issuer update',
  '20-F': 'Annual report (foreign issuer)',
  '40-F': 'Annual report (Canadian issuer)',
  '3': 'Insider holdings, first report',
  '4': 'Insider trade',
  '5': 'Insider trades, annual summary',
  '144': 'Planned insider sale',
  'S-1': 'Share offering registration',
  'S-3': 'Shelf registration',
  'S-8': 'Employee share plan registration',
  'DEF 14A': 'Proxy statement',
  'SC 13D': 'Holder above 5% (active)',
  'SC 13G': 'Holder above 5% (passive)',
  '13F-HR': 'Institutional holdings',
}
const ITEMS_8K: Record<string, string> = {
  '1.01': 'Material agreement',
  '1.02': 'Agreement ended',
  '1.03': 'Bankruptcy',
  '1.05': 'Cybersecurity incident',
  '2.01': 'Acquisition or sale completed',
  '2.02': 'Results of operations',
  '2.03': 'New debt',
  '2.04': 'Debt accelerated',
  '2.05': 'Restructuring costs',
  '2.06': 'Impairment',
  '3.01': 'Listing standards notice',
  '3.02': 'Unregistered share sale',
  '3.03': 'Shareholder rights changed',
  '4.01': 'Auditor change',
  '4.02': 'Past results no longer reliable',
  '5.01': 'Change of control',
  '5.02': 'Director or officer change',
  '5.03': 'Charter or bylaw change',
  '5.07': 'Shareholder vote results',
  '7.01': 'Regulation FD disclosure',
  '8.01': 'Other events',
}

/** "Quarterly report (10-Q)", "Current report — Results of operations (8-K 2.02)", "Insider trade (Form 4)". */
export function filingLabel(form: string, items: string[] = []): string {
  const amended = form.endsWith('/A')
  const base = amended ? form.slice(0, -2) : form
  const name = FORMS[base] ?? 'SEC filing'
  const code = /^\d+$/.test(base) ? `Form ${form}` : form
  if (base === '8-K') {
    const main = items.filter((i) => i !== '9.01')
    const first = main[0]
    const what = first ? ITEMS_8K[first] ?? `Item ${first}` : null
    const more = main.length > 1 ? ` +${main.length - 1} more` : ''
    return `${name}${amended ? ', amended' : ''}${what ? ` — ${what}${more}` : ''} (${code}${first ? ` ${first}` : ''})`
  }
  return `${name}${amended ? ', amended' : ''} (${code})`
}

const SOURCE_NAMES: Record<string, string> = {
  sec_filings: 'SEC EDGAR',
  wires: 'press-release wires',
  finnhub_news: 'Finnhub',
  google_news: 'Google News',
  israel_rss: 'Israeli press',
  gdelt: 'GDELT',
  massive: 'Massive',
  alphavantage: 'Alpha Vantage',
}
export function sourcesSentence(ids: string[]): string {
  const names = [...new Set(ids.map((s) => SOURCE_NAMES[s] ?? s))].sort()
  if (!names.length) return ''
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}

export const isHebrew = (s: string | null | undefined) => /[֐-׿]/.test(s ?? '')
