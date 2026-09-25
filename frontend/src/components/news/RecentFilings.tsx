import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { dateLabel } from '../../lib/format'
import { Card, Empty, ErrorBox, Loading } from '../../ui'
import { filingLabel } from './model'
import type { FilingRow } from './model'

const shortDate = (s: string | null) => {
  const d = dateLabel(s)
  return d.endsWith(String(new Date().getFullYear())) ? d.slice(0, -5) : d
}

/** The company's latest SEC filings (as collected by the news module), in plain words. */
export default function RecentFilings({ ticker }: { ticker: string }) {
  const q = useQuery({ queryKey: ['news-filings', ticker], queryFn: () => api<FilingRow[]>(`/api/news/filings?tickers=${encodeURIComponent(ticker)}&limit=10`), staleTime: 300_000 })
  const tase = ticker.endsWith('.TA')
  return (
    <Card title="Recent filings" foot={q.data?.length ? 'Source: SEC EDGAR. Dates are when the SEC accepted the filing.' : undefined}>
      {q.isLoading && <Loading lines={3} />}
      {q.error && <ErrorBox error={q.error} what="filings" />}
      {q.data && !q.data.length && (
        <Empty title="No filings collected yet">
          {tase ? 'SEC filings exist only for US-listed companies; Tel Aviv reports are published on Maya.' : `New SEC filings by ${ticker} will appear here as they are published.`}
        </Empty>
      )}
      {q.data && q.data.length > 0 && (
        <div className="list">
          {q.data.map((f) => (
            <div key={f.accession} className="list-item" style={{ padding: '8px 0' }}>
              <div className="li-time num">{shortDate(f.accepted_at ?? f.filed_at)}</div>
              <div className="li-body">
                <a className="li-title" href={f.url} target="_blank" rel="noreferrer" style={{ color: 'var(--text)' }}>
                  {filingLabel(f.form, f.items)}
                </a>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}
