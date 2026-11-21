import { useEffect, useMemo, useState } from 'react'
import { Check, Copy, ExternalLink, LogIn, Plus, Send, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, toast, useStore } from '../lib/store'
import type { Campaign, Site, Submission } from '../lib/types'
import { compact, dayClock, fromLocalInput, toLocalInput } from '../lib/format'
import { Avatar, ChipsInput, Field, Modal, Seg, Toggle, confirmDanger } from '../components/ui'

type Draft = Omit<Campaign, 'id' | 'stats'> & { id?: number }
const EMPTY: Draft = {
  name: '', url: '', rate: 1, budget: 0, hashtags: [], mentions: [], streamers: [], accounts: [], platforms: ['youtube', 'instagram'],
  min_seconds: 30, max_seconds: 60, status: 'active', ends_at: null, notes: '',
  site_id: null, campaign_url: '', submit_url: '', submit_mode: 'link', code: '', dedicated: 1,
}

const open = async (url: string | null | undefined, title: string) => {
  if (!url) return toast('Add the page address first', 'bad')
  try {
    const r = await api.post<{ opened: string }>('/api/open', { url, title })
    if (r.opened === 'browser') toast('Opened in your browser')
  } catch (e) {
    toast(errorText(e), 'bad')
  }
}

// ------------------------------------------------------------------ sites

function SiteRow({ s, onChange }: { s: Site; onChange: () => void }) {
  const accounts = useStore((st) => st.accounts)
  const [url, setUrl] = useState(s.url ?? '')
  const patch = (p: Partial<Site>) => api.patch(`/api/sites/${s.id}`, p).then(onChange).catch((e) => toast(errorText(e), 'bad'))
  const toggleAcc = (id: number) => patch({ accounts: s.accounts.includes(id) ? s.accounts.filter((x) => x !== id) : [...s.accounts, id] })
  return (
    <article className={`site${s.enabled ? '' : ' off'}`}>
      <header>
        <button type="button" role="switch" aria-checked={!!s.enabled} aria-label={`Use ${s.name}`} className="toggle" onClick={() => patch({ enabled: s.enabled ? 0 : 1 })} />
        <h2>{s.name}</h2>
        {s.campaigns > 0 && <span className="chip">{s.campaigns} campaign{s.campaigns === 1 ? '' : 's'}</span>}
        <span style={{ flex: 1 }} />
        {s.enabled ? <button className="btn sm" onClick={() => open(url, s.name)}><LogIn size={14} />Sign in / open</button> : null}
        {s.custom ? <button className="btn ghost icon sm" aria-label={`Remove ${s.name}`} onClick={async () => { if (confirmDanger(`Remove ${s.name}?`)) { await api.del(`/api/sites/${s.id}`); onChange() } }}><Trash2 size={14} /></button> : null}
      </header>
      <p className="faint site-notes">{s.notes}</p>
      {s.enabled ? (
        <>
          {(s.custom || s.key === 'discord') && (
            <div className="inline-form">
              <input className="input" placeholder="https://discord.gg/… or the site's address" value={url} onChange={(e) => setUrl(e.target.value)} onBlur={() => url !== (s.url ?? '') && patch({ url })} aria-label={`${s.name} address`} />
            </div>
          )}
          <div className="field" style={{ marginTop: 10 }}>
            <span className="label">Your accounts linked on {s.name}</span>
            <div className="targets">
              {accounts.map((a) => {
                const on = s.accounts.includes(a.id)
                return (
                  <label key={a.id} className={`target-opt${on ? ' on' : ''}`}>
                    <input type="checkbox" checked={on} onChange={() => toggleAcc(a.id)} />
                    <Avatar src={a.avatar} name={a.name} platform={a.platform} />
                    <span>{a.name}</span>
                  </label>
                )
              })}
              {accounts.length === 0 && <span className="faint">Connect accounts first, then tick the ones you've linked on this site.</span>}
            </div>
          </div>
        </>
      ) : null}
    </article>
  )
}

function Sites({ sites, max, reload }: { sites: Site[]; max: number; reload: () => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const add = async () => {
    try {
      await api.post('/api/sites', { name, url })
      setName('')
      setUrl('')
      reload()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <>
      <p className="muted" style={{ marginTop: 0, maxWidth: '86ch' }}>
        Switch on the sites you use (up to {max}). Sign in on each one in its own Surge window: you type your password on the site itself, and the sign-in is kept for next time. Join campaigns there, link your social accounts there, then tick those accounts below.
      </p>
      <div className="sites">{sites.map((s) => <SiteRow key={s.id} s={s} onChange={reload} />)}</div>
      {sites.length < max && (
        <div className="inline-form" style={{ marginTop: 14 }}>
          <input className="input" placeholder="Another site or agency" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="input" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="btn" disabled={!name.trim()} onClick={add}><Plus size={14} />Add site ({sites.length}/{max})</button>
        </div>
      )}
    </>
  )
}

// ------------------------------------------------------------------ campaign editor

function Editor({ initial, sites, onClose, onSaved }: { initial: Draft; sites: Site[]; onClose: () => void; onSaved: () => void }) {
  const accounts = useStore((s) => s.accounts)
  const [d, setD] = useState<Draft>(initial)
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD({ ...d, [k]: v })
  const site = sites.find((s) => s.id === d.site_id)
  const pickable = site && site.accounts.length ? accounts.filter((a) => site.accounts.includes(a.id)) : accounts
  const save = async () => {
    try {
      if (d.id) await api.patch(`/api/campaigns/${d.id}`, d)
      else await api.post('/api/campaigns', d)
      toast(d.streamers.length ? 'Campaign saved. Its streamers are on your watchlist.' : 'Campaign saved', 'good')
      onSaved()
      onClose()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <Modal title={d.id ? `Edit ${d.name}` : 'New campaign'} onClose={onClose} wide>
      <div className="form" style={{ gap: 16 }}>
        <div className="grid2">
          <div className="field">
            <label htmlFor="cs">Site</label>
            <select id="cs" className="select" value={d.site_id ?? ''} onChange={(e) => set('site_id', e.target.value ? Number(e.target.value) : null)}>
              <option value="">Pick the site you joined it on</option>
              {sites.filter((s) => s.enabled).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </div>
          <Field label="Name" htmlFor="cn"><input id="cn" className="input" value={d.name} onChange={(e) => set('name', e.target.value)} placeholder="Kai Cenat GTA VI" /></Field>
          <Field label="Campaign page" htmlFor="cu"><input id="cu" className="input" value={d.campaign_url ?? ''} onChange={(e) => set('campaign_url', e.target.value)} placeholder="Address of the campaign you joined" /></Field>
          <Field label="Where you submit links" htmlFor="csu" hint="Leave empty if it's the campaign page"><input id="csu" className="input" value={d.submit_url ?? ''} onChange={(e) => set('submit_url', e.target.value)} placeholder="https://…" /></Field>
        </div>
        <div className="grid2">
          <div className="field">
            <span className="label">How your posts reach the campaign</span>
            <Seg label="Submission" value={d.submit_mode} onChange={(v) => set('submit_mode', v)} options={[{ value: 'link', label: 'I paste links' }, { value: 'tracked', label: 'Site tracks my accounts' }]} />
            <span className="hint">{d.submit_mode === 'tracked' ? 'Surge puts the code and tags in every caption so the site finds your posts.' : 'Surge queues every post link; you paste them on the site in one go.'}</span>
          </div>
          <Field label="Tracking code" htmlFor="cc" hint="If the campaign gives you one for captions or your bio"><input id="cc" className="input" value={d.code ?? ''} onChange={(e) => set('code', e.target.value)} placeholder="e.g. #ad or a campaign code" /></Field>
        </div>
        <Field label="Streamers it covers" hint="Their streams are clipped and posted for this campaign only, and they go on your watchlist"><ChipsInput values={d.streamers} onChange={(v) => set('streamers', v)} placeholder="kaicenat" normalize={(s) => s.trim().toLowerCase()} /></Field>
        <div className="grid2">
          <Field label="Pay per 1,000 views ($)" htmlFor="cr"><input id="cr" className="input num" type="number" step={0.1} min={0} value={d.rate} onChange={(e) => set('rate', Number(e.target.value))} /></Field>
          <Field label="Most you can earn ($)" htmlFor="cb" hint="0 for no cap"><input id="cb" className="input num" type="number" min={0} value={d.budget} onChange={(e) => set('budget', Number(e.target.value))} /></Field>
          <Field label="Hashtags every post must carry"><ChipsInput values={d.hashtags} onChange={(v) => set('hashtags', v)} placeholder="#kaicenat" normalize={(s) => (s.trim() ? '#' + s.trim().replace(/^#+/, '') : '')} /></Field>
          <Field label="Accounts every post must mention"><ChipsInput values={d.mentions} onChange={(v) => set('mentions', v)} placeholder="@kaicenat" normalize={(s) => (s.trim() ? '@' + s.trim().replace(/^@+/, '') : '')} /></Field>
          <Field label="Shortest clip allowed (s)" htmlFor="cmin"><input id="cmin" className="input num" type="number" min={0} value={d.min_seconds} onChange={(e) => set('min_seconds', Number(e.target.value))} /></Field>
          <Field label="Longest clip allowed (s)" htmlFor="cmax" hint="0 for no limit"><input id="cmax" className="input num" type="number" min={0} value={d.max_seconds} onChange={(e) => set('max_seconds', Number(e.target.value))} /></Field>
        </div>
        <div className="field">
          <span className="label">Accounts that post for it{site && site.accounts.length ? `: linked on ${site.name}` : ''}</span>
          {site && !site.accounts.length && <span className="hint">Tick which accounts are linked on {site.name} in Sites to narrow this list.</span>}
          <div className="targets">
            {pickable.map((a) => {
              const on = d.accounts.includes(a.id)
              return (
                <label key={a.id} className={`target-opt${on ? ' on' : ''}`}>
                  <input type="checkbox" checked={on} onChange={() => set('accounts', on ? d.accounts.filter((x) => x !== a.id) : [...d.accounts, a.id])} />
                  <Avatar src={a.avatar} name={a.name} platform={a.platform} />
                  <span>{a.name}</span>
                </label>
              )
            })}
            {pickable.length === 0 && <span className="faint">Connect accounts first.</span>}
          </div>
          <Toggle checked={!!d.dedicated} onChange={(v) => set('dedicated', v ? 1 : 0)} label="These accounts post this campaign only" />
        </div>
        <div className="grid2">
          <Field label="Ends" htmlFor="ce"><input id="ce" className="input" type="datetime-local" value={d.ends_at ? toLocalInput(d.ends_at) : ''} onChange={(e) => set('ends_at', e.target.value ? fromLocalInput(e.target.value) : null)} /></Field>
          <div className="field"><span className="label">Status</span>
            <Seg label="Status" value={d.status} onChange={(v) => set('status', v)} options={[{ value: 'active', label: 'Active' }, { value: 'paused', label: 'Paused' }, { value: 'ended', label: 'Ended' }]} />
          </div>
        </div>
        <Field label="Other rules from the campaign page" htmlFor="cnotes"><textarea id="cnotes" className="textarea" rows={2} value={d.notes ?? ''} onChange={(e) => set('notes', e.target.value)} /></Field>
        <div style={{ display: 'flex', gap: 8 }}><button className="btn primary" disabled={!d.name.trim()} onClick={save}>Save campaign</button><button className="btn" onClick={onClose}>Cancel</button></div>
      </div>
    </Modal>
  )
}

// ------------------------------------------------------------------ submissions

function Submissions({ c, onClose }: { c: Campaign; onClose: () => void }) {
  const [rows, setRows] = useState<Submission[]>([])
  const reload = () => api.get<Submission[]>(`/api/campaigns/${c.id}/submissions`).then(setRows).catch(() => {})
  useEffect(() => {
    void reload()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.id])
  const pending = rows.filter((r) => r.url && r.submit_status === 'pending')
  const mark = async (ids: number[], status = 'submitted') => {
    await api.post(`/api/campaigns/${c.id}/submissions`, { ids, status })
    void reload()
  }
  const copyPending = async () => {
    if (!pending.length) return toast('Nothing waiting to submit', 'bad')
    await navigator.clipboard.writeText(pending.map((r) => r.url).join('\n'))
    toast(`Copied ${pending.length} link${pending.length === 1 ? '' : 's'}`, 'good')
    void open(c.submit_url || c.campaign_url, c.name)
  }
  return (
    <Modal title={`Submissions for ${c.name}`} onClose={onClose} wide>
      <p className="muted" style={{ marginTop: 0 }}>
        {c.submit_mode === 'tracked'
          ? 'This site tracks your linked accounts, so posts count without pasting. Links are here if the site asks for them anyway.'
          : 'Copy the waiting links, paste them on the campaign page that opens, then mark them submitted.'}
      </p>
      <div className="inline-form" style={{ marginBottom: 12 }}>
        <button className="btn primary" onClick={copyPending}><Send size={14} />Copy {pending.length} waiting and open the site</button>
        <button className="btn" disabled={!pending.length} onClick={() => mark(pending.map((r) => r.id))}><Check size={14} />Mark all submitted</button>
      </div>
      <table className="table">
        <thead><tr><th>Post</th><th>Account</th><th>Views</th><th>Likes</th><th>Status</th><th /></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="t-title" title={r.title}>{r.title}<div className="faint" style={{ fontSize: 12 }}>{dayClock(r.scheduled_at)}</div></td>
              <td>{r.account}</td>
              <td className="num">{compact(r.views)}</td>
              <td className="num">{compact(r.likes)}</td>
              <td><span className="status"><span className={`dot ${r.submit_status === 'submitted' || r.submit_status === 'tracked' ? 'on' : r.url ? 'warn' : ''}`} />
                {!r.url ? 'Not posted yet' : r.submit_status === 'tracked' ? 'Tracked by the site' : r.submit_status === 'submitted' ? 'Submitted' : 'Waiting to submit'}</span></td>
              <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                {r.url && <button className="btn ghost icon sm" aria-label="Copy link" onClick={() => navigator.clipboard.writeText(r.url!).then(() => toast('Copied'))}><Copy size={14} /></button>}
                {r.url && r.submit_status === 'pending' && <button className="btn sm" onClick={() => mark([r.id])}>Mark submitted</button>}
              </td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={6} className="faint">No posts for this campaign yet.</td></tr>}
        </tbody>
      </table>
    </Modal>
  )
}

// ------------------------------------------------------------------ page

export function Campaigns() {
  const accounts = useStore((s) => s.accounts)
  const [tab, setTab] = useState<'campaigns' | 'sites'>('campaigns')
  const [list, setList] = useState<Campaign[]>([])
  const [sites, setSites] = useState<Site[]>([])
  const [max, setMax] = useState(12)
  const [edit, setEdit] = useState<Draft | null>(null)
  const [subs, setSubs] = useState<Campaign | null>(null)
  const reload = () => {
    api.get<Campaign[]>('/api/campaigns').then(setList).catch(() => {})
    api.get<{ sites: Site[]; max: number }>('/api/sites').then((r) => { setSites(r.sites); setMax(r.max) }).catch(() => {})
  }
  useEffect(reload, [])
  const siteName = useMemo(() => Object.fromEntries(sites.map((s) => [s.id, s.name])), [sites])
  const totals = list.reduce((t, c) => ({ earned: t.earned + c.stats.earned, views: t.views + c.stats.views, likes: t.likes + c.stats.likes, waiting: t.waiting + c.stats.to_submit }), { earned: 0, views: 0, likes: 0, waiting: 0 })

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Campaigns</h1>
          <p>Join campaigns on your sites, then set them up here. A campaign's streamers are watched and clipped automatically, their clips post only to that campaign's accounts, and every link lands in its submission queue.</p>
        </div>
        <div className="actions">
          <Seg label="View" value={tab} onChange={setTab} options={[{ value: 'campaigns', label: 'Campaigns', n: list.length }, { value: 'sites', label: 'Sites', n: sites.filter((s) => s.enabled).length }]} />
          {tab === 'campaigns' && <button className="btn primary" onClick={() => setEdit({ ...EMPTY, site_id: sites.find((s) => s.enabled)?.id ?? null })}><Plus size={15} />New campaign</button>}
        </div>
      </div>

      {tab === 'sites' ? <Sites sites={sites} max={max} reload={reload} /> : (
        <>
          {list.length > 0 && (
            <div className="kpis">
              <div className="kpi"><b>${totals.earned.toFixed(2)}</b><span>earned so far</span></div>
              <div className="kpi"><b>{compact(totals.views)}</b><span>campaign views</span></div>
              <div className="kpi"><b>{compact(totals.likes)}</b><span>likes</span></div>
              <div className="kpi"><b>{totals.waiting}</b><span>links waiting to submit</span></div>
            </div>
          )}
          {list.length === 0 ? (
            <div className="empty">
              <h3>No campaigns yet</h3>
              <p>Switch on your sites under Sites and sign in. Join a campaign there, then add it here with its streamers and the accounts that post for it. From then on Surge clips, posts and queues submissions for it by itself.</p>
            </div>
          ) : (
            <div className="campaigns">
              {list.map((c) => {
                const pct = c.budget ? Math.min(100, (c.stats.earned / c.budget) * 100) : 0
                return (
                  <article className="camp" key={c.id}>
                    <header>
                      <span className={`dot ${c.status === 'active' ? 'on' : ''}`} />
                      <h2>{c.name}</h2>
                      {c.site_id && <span className="chip">{siteName[c.site_id] ?? 'site'}</span>}
                      <span className="faint">${c.rate.toFixed(2)} per 1K{c.budget ? `, up to $${c.budget}` : ''}</span>
                      <span style={{ flex: 1 }} />
                      {(c.campaign_url || c.url) && <button className="btn ghost sm" onClick={() => open(c.campaign_url || c.url, c.name)}><ExternalLink size={14} />Campaign page</button>}
                      <button className="btn sm primary" onClick={() => setSubs(c)}><Send size={14} />Submissions{c.stats.to_submit ? ` (${c.stats.to_submit})` : ''}</button>
                      <button className="btn sm" onClick={() => setEdit({ ...EMPTY, ...c })}>Edit</button>
                      <button className="btn ghost icon sm" aria-label={`Delete ${c.name}`} onClick={async () => { if (confirmDanger(`Delete ${c.name}? Posts stay up; they just stop counting toward it here.`)) { await api.del(`/api/campaigns/${c.id}`); reload() } }}><Trash2 size={14} /></button>
                    </header>
                    <div className="camp-stats">
                      <div className="readout"><b>${c.stats.earned.toFixed(2)}</b><span>earned</span></div>
                      <div className="readout"><b>{compact(c.stats.views)}</b><span>views</span></div>
                      <div className="readout"><b>{compact(c.stats.likes)}</b><span>likes</span></div>
                      <div className="readout"><b>{compact(c.stats.comments)}</b><span>comments</span></div>
                      <div className="readout"><b>{c.stats.published}/{c.stats.posts}</b><span>posts live</span></div>
                      <div className="readout"><b>{c.stats.submitted}</b><span>{c.submit_mode === 'tracked' ? 'tracked' : 'submitted'}</span></div>
                      {c.budget > 0 && <div className="camp-budget"><div className="progress"><i style={{ width: `${pct}%` }} /></div><span className="faint">{pct.toFixed(0)}% of the cap</span></div>}
                    </div>
                    <div className="camp-rules">
                      {c.streamers.map((s) => <span key={s} className="chip">{s}</span>)}
                      {c.hashtags.map((t) => <span key={t} className="chip">{t}</span>)}
                      {c.mentions.map((t) => <span key={t} className="chip">{t}</span>)}
                      {c.code && <span className="chip">code {c.code}</span>}
                      {(c.min_seconds > 0 || c.max_seconds > 0) && <span className="chip">{c.min_seconds || 0}–{c.max_seconds || '∞'} s</span>}
                      {accounts.filter((a) => c.accounts.includes(a.id)).slice(0, 10).map((a) => <Avatar key={a.id} src={a.avatar} name={a.name} platform={a.platform} />)}
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}
      {edit && <Editor initial={edit} sites={sites} onClose={() => setEdit(null)} onSaved={reload} />}
      {subs && <Submissions c={subs} onClose={() => { setSubs(null); reload() }} />}
    </>
  )
}
