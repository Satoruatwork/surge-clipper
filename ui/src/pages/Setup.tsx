import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ExternalLink, Copy } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, setState, toast, useStore } from '../lib/store'
import { AiConnectors } from '../components/AiConnectors'
import type { SetupStatus, Settings as S } from '../lib/types'

type Test = { ok: boolean; message: string } | null

function Step({ n, children }: { n: number; children: ReactNode }) {
  return <li><span className="step-n num">{n}</span><div>{children}</div></li>
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer" className="ext">{children}<ExternalLink size={12} /></a>
}

function Block({ title, ok, summary, children }: { title: string; ok: boolean | null; summary: ReactNode; children: ReactNode }) {
  return (
    <section className="setup-block">
      <header>
        <span className={`dot ${ok === null ? '' : ok ? 'on' : 'warn'}`} />
        <h2>{title}</h2>
        <span className="setup-sum">{summary}</span>
      </header>
      <div className="setup-body">{children}</div>
    </section>
  )
}

function TestLine({ t }: { t: Test }) {
  if (!t) return null
  return <p className={`test-line ${t.ok ? 'ok' : 'bad'}`}>{t.message}</p>
}

export function Setup({ go }: { go: (page: 'accounts' | 'settings') => void }) {
  const settings = useStore((s) => s.settings)
  const [st, setSt] = useState<SetupStatus | null>(null)
  const [tests, setTests] = useState<Record<string, Test>>({})
  const [meta, setMeta] = useState({ id: '', secret: '', redirect: '' })
  const [limit, setLimit] = useState(100)
  const file = useRef<HTMLInputElement>(null)

  const refresh = () => api.get<SetupStatus>('/api/setup').then((s) => { setSt(s); setLimit(s.google.upload_limit) }).catch(() => {})
  useEffect(() => {
    void refresh()
  }, [])
  useEffect(() => {
    if (settings) setMeta({ id: String(settings.meta_app_id ?? ''), secret: String(settings.meta_app_secret ?? ''), redirect: String(settings.meta_redirect_uri ?? '') })
  }, [settings])

  const save = async (patch: Record<string, unknown>) => {
    const next = await api.put<S>('/api/settings', patch)
    setState({ settings: next })
  }
  const test = async (what: string) => {
    setTests((t) => ({ ...t, [what]: { ok: true, message: 'Checking…' } }))
    try {
      const r = await api.post<{ ok: boolean; message: string }>(`/api/setup/test/${what}`)
      setTests((t) => ({ ...t, [what]: r }))
    } catch (e) {
      setTests((t) => ({ ...t, [what]: { ok: false, message: errorText(e) } }))
    }
    void refresh()
  }
  const uploadSecret = async (f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    try {
      await api.post('/api/settings/google-secret', fd)
      toast('Google client added', 'good')
      void load.state()
      await test('google')
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const copy = (text: string) => navigator.clipboard.writeText(text).then(() => toast('Copied'))

  if (!st) return <p className="muted">Checking your setup…</p>
  const g = st.google
  const m = st.meta
  const ytReady = !!g.client && g.client.type === 'installed'
  const igReady = m.app_id && m.secret
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Setup</h1>
          <p>Everything Surge needs to post on your accounts, in order. Each part checks itself, so you can see what's done and what's left.</p>
        </div>
      </div>

      <div className="setup">
        <Block title="Engine" ok summary={`${st.engine.encoder.replace('h264_', '').toUpperCase()} encoder, ${st.engine.models.length} speech models, ${st.engine.disk_free_gb} GB free`}>
          <p className="muted">Clipping, captions and rendering run on this PC. Nothing to set up.</p>
        </Block>

        <Block title="AI writer" ok={st.ai.on} summary={st.ai.on ? `${st.ai.name} (${st.ai.model}) writes hooks, titles and the edit` : 'Optional, but titles and edits are much better with it'}>
          <p className="muted">Surge works without AI: its built-in writer uses the transcript and chat. Connect an AI service for sharper hooks, titles and edits: Anthropic, OpenAI, Google Gemini, DeepSeek, OpenRouter, a free model, or one running on this PC.</p>
          <AiConnectors onChange={() => void refresh()} />
        </Block>

        <Block title="YouTube" ok={ytReady && g.channels > 0 ? g.broken.length === 0 : null}
          summary={`${g.channels} channel${g.channels === 1 ? '' : 's'} connected, ${g.uploads_today} of ${g.upload_limit} uploads used today`}>
          <ol className="steps-list">
            <Step n={1}>Create a project in <Ext href="https://console.cloud.google.com/projectcreate">Google Cloud</Ext>.</Step>
            <Step n={2}>Turn on <Ext href="https://console.cloud.google.com/apis/library/youtube.googleapis.com">YouTube Data API v3</Ext> for it.</Step>
            <Step n={3}>Set up the <Ext href="https://console.cloud.google.com/auth/branding">OAuth consent screen</Ext> as External and add the Google accounts that own your channels as test users. Then press <b>Publish app</b>: while the app is in testing, Google signs your channels out every 7 days. The "unverified app" warning you'll see when connecting is expected for your own accounts.</Step>
            <Step n={4}>Under <Ext href="https://console.cloud.google.com/apis/credentials">Credentials</Ext>, create an OAuth client ID of type <b>Desktop app</b> and download its JSON file.</Step>
            <Step n={5}>
              <div className="inline-form">
                <input ref={file} type="file" accept=".json,application/json" hidden onChange={(e) => e.target.files?.[0] && uploadSecret(e.target.files[0])} />
                <button className="btn primary" onClick={() => file.current?.click()}>{g.client ? 'Replace client file' : 'Add client file'}</button>
                {g.client && <span className="status"><span className={`dot ${ytReady ? 'on' : 'warn'}`} />{g.client.type === 'installed' ? 'Desktop client' : 'Wrong client type'}, project {g.client.project_id}</span>}
              </div>
            </Step>
            <Step n={6}>Connect each channel on the <button className="link-btn" onClick={() => go('accounts')}>Accounts</button> page. The browser asks which channel; repeat for every channel.</Step>
            <Step n={7}>Until Google approves your project in the free <Ext href="https://support.google.com/youtube/contact/yt_api_form">YouTube API audit</Ext>, every upload stays private. Apply as soon as the first channel is connected.</Step>
          </ol>
          <div className="quota">
            <div className="quota-bar"><i style={{ width: `${Math.min(100, (g.uploads_today / Math.max(1, g.upload_limit)) * 100)}%` }} /></div>
            <span className="muted">Uploads today: <b className="num">{g.uploads_today}</b> of</span>
            <input className="input num" type="number" min={1} value={limit} onChange={(e) => setLimit(Number(e.target.value))} onBlur={() => save({ yt_daily_uploads: limit })} aria-label="Daily upload allowance" />
            <span className="faint">your project's daily allowance (see Quotas in Google Cloud). The counter resets at midnight Pacific.</span>
          </div>
          {g.broken.length > 0 && <p className="test-line bad">Needs reconnecting: {g.broken.join(', ')}</p>}
          <TestLine t={tests.google ?? null} />
        </Block>

        <Block title="Instagram" ok={igReady && m.accounts > 0 ? m.broken.length === 0 : null}
          summary={`${m.accounts} account${m.accounts === 1 ? '' : 's'} connected${m.expiring.length ? `, ${m.expiring.length} sign-in${m.expiring.length === 1 ? '' : 's'} expiring soon` : ''}`}>
          <ol className="steps-list">
            <Step n={1}>Switch every account to a <b>Business</b> or <b>Creator</b> account in the Instagram app (Settings, Account type and tools).</Step>
            <Step n={2}>Create a <Ext href="https://developers.facebook.com/apps/creation/">Meta app</Ext> of type Business and add the <b>Instagram</b> product, then open <b>API setup with Instagram login</b>.</Step>
            <Step n={3}>Under App roles, add each account as an <b>Instagram Tester</b>, then accept the invite while signed in as that account at <Ext href="https://www.instagram.com/accounts/manage_access/">instagram.com, Apps and websites, Tester invites</Ext>.</Step>
            <Step n={4}>
              Copy the <b>Instagram app ID</b> and <b>Instagram app secret</b> from that page:
              <div className="inline-form">
                <input className="input" placeholder="Instagram app ID" value={meta.id} onChange={(e) => setMeta({ ...meta, id: e.target.value })} />
                <input className="input" type="password" placeholder="Instagram app secret" value={meta.secret} onChange={(e) => setMeta({ ...meta, secret: e.target.value })} autoComplete="off" />
                <button className="btn primary" disabled={!meta.id || !meta.secret} onClick={async () => { await save({ meta_app_id: meta.id.trim(), meta_app_secret: meta.secret.trim() }); await test('meta') }}>Save and check</button>
              </div>
            </Step>
            <Step n={5}>
              In Business login settings, add this redirect address:
              <div className="inline-form">
                <input className="input" value={meta.redirect} onChange={(e) => setMeta({ ...meta, redirect: e.target.value })} onBlur={() => save({ meta_redirect_uri: meta.redirect.trim() })} />
                <button className="btn icon" aria-label="Copy redirect address" onClick={() => copy(meta.redirect)}><Copy size={15} /></button>
              </div>
              <span className="faint">If Meta accepts <code>http://localhost:8765/ig-callback</code>, sign-in finishes by itself. With the https address, copy the page address you land on and paste it in Surge.</span>
            </Step>
            <Step n={6}>Connect accounts on the <button className="link-btn" onClick={() => go('accounts')}>Accounts</button> page, one sign-in each. With a Business portfolio, a system-user token (starts with EAA) connects every linked account at once.</Step>
            <Step n={7}>Until Meta reviews the app, only tester accounts can post. That covers your own accounts.</Step>
          </ol>
          {m.broken.length > 0 && <p className="test-line bad">Needs reconnecting: {m.broken.join(', ')}</p>}
          <TestLine t={tests.meta ?? null} />
        </Block>
      </div>
    </>
  )
}
