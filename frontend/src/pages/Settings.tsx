import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { num } from '../lib/format'
import { href } from '../lib/router'
import { toast } from '../lib/toast'
import { Card, ErrorBox, Loading, Page } from '../ui'
import { useProviders } from '../components/catalog/labels'

type SecretState = { set: boolean; masked: string; from_keyring: boolean }
type SettingsView = { data_dir: string; timezone: string; grey_sources_enabled: boolean; llm_monthly_budget_usd: number; secrets: Record<string, SecretState> }
type Llm = { month_spent_usd: number; budget_usd: number; configured: boolean }
type Chat = { chat_id: number | string; title: string | null; text: string | null }
type RuleTypes = { defaults: { quiet_hours?: { start: string; end: string } | null } }

type KeyDef = { name: string; label: string; need: 'required' | 'optional' | 'unused'; benefit: string; link?: [string, string]; secret?: boolean; placeholder?: string; missing?: string }

const MARKET_KEYS: KeyDef[] = [
  {
    name: 'sec_user_agent',
    label: 'SEC contact',
    need: 'required',
    benefit: 'Your name and email, e.g. “Dana Levi dana@example.com”. The SEC asks every app to identify itself. Needed for US company financials and filings.',
    link: ['https://www.sec.gov/os/accessing-edgar-data', 'SEC fair-access policy'],
    secret: false,
    placeholder: 'Your name and email',
    missing: 'US company financials and filings will not load',
  },
  {
    name: 'fred_api_key',
    label: 'FRED API key',
    need: 'required',
    benefit: 'US and global macro data — interest rates, inflation, jobs. Most of the data catalog comes from FRED.',
    link: ['https://fredaccount.stlouisfed.org/apikeys', 'Get a free key'],
    missing: 'most macro data will not load',
  },
  {
    name: 'finnhub_api_key',
    label: 'Finnhub API key',
    need: 'required',
    benefit: 'Live US stock quotes, company news and the earnings calendar. Price alerts use these quotes.',
    link: ['https://finnhub.io/register', 'Get a free key'],
    missing: 'live US quotes and price alerts will not work',
  },
  { name: 'fmp_api_key', label: 'Financial Modeling Prep (FMP)', need: 'optional', benefit: 'Adds consensus forecasts to the economic calendar, plus earnings, dividend and IPO dates. Paid plan.', link: ['https://site.financialmodelingprep.com/developer/docs/pricing', 'Plans and pricing'] },
  { name: 'massive_api_key', label: 'Massive (formerly Polygon.io)', need: 'optional', benefit: 'An extra news source with sentiment scores. Free tier available.', link: ['https://massive.com', 'massive.com'] },
  { name: 'alphavantage_api_key', label: 'Alpha Vantage', need: 'optional', benefit: 'News sentiment, used to cross-check other news sources. Free tier: 25 requests a day.', link: ['https://www.alphavantage.co/support/#api-key', 'Get a free key'] },
  { name: 'alpaca_key_id', label: 'Alpaca key id', need: 'unused', benefit: 'Not used by any feature yet — you can leave it empty.', link: ['https://alpaca.markets', 'alpaca.markets'] },
  { name: 'alpaca_secret', label: 'Alpaca secret', need: 'unused', benefit: 'Goes with the Alpaca key id. Not used by any feature yet.' },
]
const AI_KEY: KeyDef = {
  name: 'anthropic_api_key',
  label: 'Claude API key',
  need: 'optional',
  benefit: 'Lets the terminal read Israeli (Maya) PDF financial statements, summarise important news and write the morning brief.',
  link: ['https://console.anthropic.com/settings/keys', 'Get a key from Anthropic'],
}
const TELEGRAM_KEYS: KeyDef[] = [
  { name: 'telegram_bot_token', label: 'Bot token', need: 'required', benefit: 'Create a bot by messaging @BotFather in Telegram, then paste the token it gives you.', link: ['https://t.me/BotFather', 'Open @BotFather'], missing: 'no alerts can be sent' },
  { name: 'telegram_chat_id', label: 'Alerts chat id', need: 'required', benefit: 'The chat that receives your alerts. Send /start to your bot, then use “Find my chat id” below.', secret: false, placeholder: 'e.g. 123456789', missing: 'no alerts can be sent' },
  { name: 'telegram_ops_chat_id', label: 'System messages chat id', need: 'optional', benefit: 'A separate chat for system messages (errors, data problems). Leave empty to use the alerts chat.', secret: false, placeholder: 'e.g. 123456789' },
]

function useSettingsView() {
  return useQuery({ queryKey: ['settings'], queryFn: () => api<SettingsView>('/api/sys/settings') })
}

export default function SettingsPage() {
  const settings = useSettingsView()
  const secrets = settings.data?.secrets ?? {}
  return (
    <Page title="Settings" sub="API keys are stored in the macOS Keychain on this Mac and are only ever sent to the service they belong to.">
      {settings.error && <ErrorBox error={settings.error} what="settings" />}
      {settings.isLoading && <Loading lines={6} />}
      {settings.data && (
        <div className="stack">
          <Card title="Market data" hint="Three keys are needed; the rest add extra sources">
            <KeyList defs={MARKET_KEYS.filter((k) => k.need === 'required')} secrets={secrets} />
            <div className="section-title">Optional</div>
            <KeyList defs={MARKET_KEYS.filter((k) => k.need !== 'required')} secrets={secrets} />
          </Card>
          <AiCard secrets={secrets} />
          <TelegramCard secrets={secrets} />
          <PreferencesCard settings={settings.data} />
        </div>
      )}
    </Page>
  )
}

// ------------------------------------------------------------------ keys
function KeyList({ defs, secrets }: { defs: KeyDef[]; secrets: Record<string, SecretState> }) {
  return (
    <div className="list">
      {defs.map((d) => (
        <KeyRow key={d.name} def={d} state={secrets[d.name]} />
      ))}
    </div>
  )
}

function KeyRow({ def, state }: { def: KeyDef; state?: SecretState }) {
  const qc = useQueryClient()
  const [value, setValue] = useState('')
  const save = useMutation({
    mutationFn: () => api<{ saved: boolean; keyring: boolean }>('/api/sys/settings/secret', { method: 'PUT', json: { name: def.name, value: value.trim() } }),
    onSuccess: (r) => {
      setValue('')
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['providers'] })
      qc.invalidateQueries({ queryKey: ['sys'] })
      toast(r.keyring ? `${def.label} saved in the macOS Keychain` : `${def.label} saved for this session only — the Keychain was not available`, r.keyring ? 'ok' : 'info')
    },
    onError: (e) => toast(`Could not save ${def.label}: ${String((e as Error).message)}`, 'err'),
  })
  const isSet = !!state?.set
  return (
    <div className="list-item" style={{ gap: 18 }}>
      <div style={{ width: 210, flexShrink: 0 }}>
        <div className="strong">{def.label}</div>
        <div className="xs faint">{def.need === 'required' ? 'Required' : def.need === 'optional' ? 'Optional' : 'Not used yet'}</div>
      </div>
      <div className="li-body">
        <div className="small muted">
          {def.benefit}{' '}
          {def.link && (
            <a href={def.link[0]} target="_blank" rel="noreferrer">
              {def.link[1]} ↗
            </a>
          )}
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <input
            type={def.secret === false ? 'text' : 'password'}
            autoComplete="off"
            spellCheck={false}
            style={{ width: 300 }}
            placeholder={isSet ? 'Paste a new value to replace it' : (def.placeholder ?? 'Paste the key')}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && value.trim() && save.mutate()}
          />
          <button disabled={!value.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
          <KeyStatus def={def} state={state} />
        </div>
      </div>
    </div>
  )
}

function KeyStatus({ def, state }: { def: KeyDef; state?: SecretState }) {
  if (state?.set)
    return (
      <span className="small">
        <span className="up">✓</span> {state.from_keyring ? 'Saved in macOS Keychain' : 'Set in the .env file (takes priority over the Keychain)'}
        {state.masked && <span className="mono xs faint"> · {state.masked}</span>}
      </span>
    )
  if (def.need === 'required') return <span className="small warn-text">Not set — {def.missing ?? 'this feature will not work'}</span>
  return <span className="small faint">Not set</span>
}

// ------------------------------------------------------------------ AI
function AiCard({ secrets }: { secrets: Record<string, SecretState> }) {
  const llm = useQuery({ queryKey: ['sys', 'llm'], queryFn: () => api<Llm>('/api/sys/llm') })
  return (
    <Card
      title="AI (Claude)"
      hint="Optional"
      foot={
        llm.data ? (
          <>
            Monthly budget: <span className="num">${num(llm.data.budget_usd, 2)}</span> · spent this month: <span className="num">${num(llm.data.month_spent_usd, 2)}</span>. AI features pause when the budget is used up. To change it, set <span className="mono">TERMINAL_LLM_MONTHLY_BUDGET_USD</span> in the .env file and restart. <a href={href('status')}>Spending details</a>
          </>
        ) : null
      }
    >
      <KeyList defs={[AI_KEY]} secrets={secrets} />
    </Card>
  )
}

// ------------------------------------------------------------------ Telegram
function TelegramCard({ secrets }: { secrets: Record<string, SecretState> }) {
  const qc = useQueryClient()
  const [chats, setChats] = useState<Chat[] | null>(null)
  const hasToken = !!secrets.telegram_bot_token?.set
  const hasChat = !!secrets.telegram_chat_id?.set
  const test = useMutation({
    mutationFn: () => api<{ sent: boolean }>('/api/sys/alerts/test', { method: 'POST', json: { text: 'Test message from your terminal ✅', ops: false } }),
    onSuccess: (d) => toast(d.sent ? 'Test message sent — check Telegram' : 'Could not send. Check the bot token and the alerts chat id.', d.sent ? 'ok' : 'err'),
    onError: (e) => toast(`Could not send: ${String((e as Error).message)}`, 'err'),
  })
  const find = useMutation({
    mutationFn: () => api<Chat[]>('/api/sys/telegram/chats'),
    onSuccess: (d) => {
      setChats(d)
      if (!d.length) toast('No messages found. Send /start to your bot in Telegram, then try again.', 'info')
    },
    onError: (e) => toast(`Could not ask Telegram: ${String((e as Error).message)}`, 'err'),
  })
  const use = useMutation({
    mutationFn: (id: string) => api('/api/sys/settings/secret', { method: 'PUT', json: { name: 'telegram_chat_id', value: id } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      toast('Alerts chat saved', 'ok')
    },
    onError: (e) => toast(`Could not save: ${String((e as Error).message)}`, 'err'),
  })
  const unique = chats ? Array.from(new Map(chats.map((c) => [String(c.chat_id), c])).values()) : []
  return (
    <Card
      title="Telegram alerts"
      hint="How alerts reach your phone"
      actions={
        <>
          <button disabled={!hasToken || find.isPending} onClick={() => find.mutate()} title={hasToken ? 'Look for recent messages sent to your bot' : 'Save the bot token first'}>
            {find.isPending ? 'Looking…' : 'Find my chat id'}
          </button>
          <button className="primary" disabled={!hasToken || !hasChat || test.isPending} onClick={() => test.mutate()} title={hasToken && hasChat ? 'Send a test message to the alerts chat' : 'Save the bot token and chat id first'}>
            {test.isPending ? 'Sending…' : 'Send test message'}
          </button>
        </>
      }
    >
      <KeyList defs={TELEGRAM_KEYS} secrets={secrets} />
      {chats && unique.length > 0 && (
        <div className="notice info" style={{ marginTop: 10 }}>
          <div className="grow stack" style={{ gap: 6 }}>
            <b>Chats that messaged your bot</b>
            {unique.map((c) => (
              <div key={String(c.chat_id)} className="row">
                <span>{c.title ?? 'Unnamed chat'}</span>
                <span className="mono small muted">{String(c.chat_id)}</span>
                {c.text && <span className="small faint">last message “{c.text.slice(0, 40)}”</span>}
                <span className="spacer" />
                <button disabled={use.isPending} onClick={() => use.mutate(String(c.chat_id))}>
                  Use for alerts
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  )
}

// ------------------------------------------------------------------ Preferences
function PreferencesCard({ settings }: { settings: SettingsView }) {
  const qc = useQueryClient()
  const prefs = useQuery({ queryKey: ['prefs'], queryFn: () => api<Record<string, unknown>>('/api/sys/prefs') })
  const providers = useProviders()
  const llm = useQuery({ queryKey: ['sys', 'llm'], queryFn: () => api<Llm>('/api/sys/llm') })
  const rules = useQuery({ queryKey: ['alerts', 'rule-types'], queryFn: () => api<RuleTypes>('/api/alerts/rule-types'), staleTime: 300_000 })
  const [threshold, setThreshold] = useState<string | null>(null)

  const savePref = useMutation({
    mutationFn: (kv: { key: string; value: unknown; label: string }) => api('/api/sys/prefs', { method: 'PUT', json: { key: kv.key, value: kv.value } }),
    onSuccess: (_d, kv) => {
      qc.invalidateQueries({ queryKey: ['prefs'] })
      qc.invalidateQueries({ queryKey: ['settings'] })
      qc.invalidateQueries({ queryKey: ['providers'] })
      qc.invalidateQueries({ queryKey: ['health'] })
      toast(`${kv.label} saved`, 'ok')
    },
    onError: (e) => toast(`Could not save: ${String((e as Error).message)}`, 'err'),
  })

  const grey = Boolean(prefs.data?.grey_sources_enabled ?? settings.grey_sources_enabled)
  const greyNames = (providers.data ?? []).filter((p) => p.grey).map((p) => p.name.replace(/\s*\(.*\)$/, ''))
  const currentThreshold = Number(prefs.data?.news_llm_threshold ?? 60)
  const thresholdValue = threshold ?? String(currentThreshold)
  const thresholdNum = Number(thresholdValue)
  const thresholdValid = thresholdValue.trim() !== '' && Number.isFinite(thresholdNum) && thresholdNum >= 0 && thresholdNum <= 100
  const qh = rules.data?.defaults?.quiet_hours

  return (
    <Card title="Preferences" foot={`Data folder: ${settings.data_dir} · Time zone: ${settings.timezone}`}>
      <div className="list">
        <div className="list-item" style={{ gap: 18 }}>
          <div style={{ width: 210, flexShrink: 0 }} className="strong">
            Unofficial sources
          </div>
          <div className="li-body">
            <label className="check" style={{ color: 'var(--text)' }}>
              <input type="checkbox" checked={grey} disabled={savePref.isPending || prefs.isLoading} onChange={(e) => savePref.mutate({ key: 'grey_sources_enabled', value: e.target.checked, label: e.target.checked ? 'Unofficial sources switched on' : 'Unofficial sources switched off' })} />
              Use unofficial free sources (Yahoo Finance, Google News) — never used alone for alerts
            </label>
            <div className="small muted" style={{ marginTop: 4 }}>
              They fill gaps such as Israeli and index prices and extra headlines. Turning this off stops using them{greyNames.length ? ` (currently: ${greyNames.join(', ')})` : ''}.
            </div>
          </div>
        </div>

        <div className="list-item" style={{ gap: 18 }}>
          <div style={{ width: 210, flexShrink: 0 }} className="strong">
            News summaries
          </div>
          <div className="li-body">
            <div className="small muted">
              Claude writes a short summary for news stories whose importance score (0–100) is at least this number. Lower means more summaries and more AI spend.
              {llm.data && !llm.data.configured ? ' Takes effect once Claude is set up above.' : ''}
            </div>
            <div className="row" style={{ marginTop: 8 }}>
              <input type="number" min={0} max={100} style={{ width: 90 }} value={thresholdValue} onChange={(e) => setThreshold(e.target.value)} />
              <button
                disabled={!thresholdValid || thresholdNum === currentThreshold || savePref.isPending}
                onClick={() => savePref.mutate({ key: 'news_llm_threshold', value: Math.round(thresholdNum), label: 'News summary threshold' }, { onSuccess: () => setThreshold(null) })}
              >
                Save
              </button>
              {!thresholdValid && <span className="small warn-text">Enter a number from 0 to 100</span>}
            </div>
          </div>
        </div>

        <div className="list-item" style={{ gap: 18 }}>
          <div style={{ width: 210, flexShrink: 0 }} className="strong">
            Alert quiet hours
          </div>
          <div className="li-body small muted">
            {qh ? (
              <>
                Alerts that trigger between <span className="num">{qh.start}</span> and <span className="num">{qh.end}</span> (Israel time) are held and sent together in the morning digest. You can change this for each alert on the <a href={href('alerts')}>Alerts page</a>.
              </>
            ) : rules.isLoading ? (
              'Loading…'
            ) : (
              <>
                Quiet hours are set for each alert on the <a href={href('alerts')}>Alerts page</a>.
              </>
            )}
          </div>
        </div>
      </div>
    </Card>
  )
}
