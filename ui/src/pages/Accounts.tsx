import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, RefreshCw, ShieldCheck, Trash2, Users } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, toast, useStore } from '../lib/store'
import type { Account, Group } from '../lib/types'
import { ago, compact, dayClock } from '../lib/format'
import { Avatar, ChipsInput, Modal, Seg, Toggle, confirmDanger } from '../components/ui'

const YT_TARGET = 15
const IG_TARGET = 25
const timeOk = (s: string) => (/^\d{1,2}:\d{2}$/.test(s.trim()) ? s.trim().padStart(5, '0') : '')
const DEFAULT_TIMES = ['10:00', '13:00', '16:00', '19:00', '22:00']
const ALL_DAY = '00:00-00:00'
const isSpan = (w: string) => w.includes('-')
const minutesOf = (s: string) => { const [h, m] = s.split(':').map(Number); return h * 60 + m }
const spanLength = (from: string, to: string) => { const a = minutesOf(from), b = minutesOf(to); return b > a ? b - a : b + 1440 - a }
const every = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} h${Math.round(m % 60) ? ` ${Math.round(m % 60)} min` : ''}` : `${Math.max(1, Math.round(m))} min`)

/** Set times post once each; "spread through the day" stores one span ("08:00-23:30", "00:00-00:00" for all day)
 * and the engine spaces the day's posts evenly through it. */
function PostingTimes({ id, windows, cap, onChange }: { id: string; windows: string[]; cap: number; onChange: (w: string[]) => void }) {
  const span = windows.find(isSpan)
  const times = windows.filter((w) => !isSpan(w))
  const [lastTimes, setLastTimes] = useState(times.length ? times : DEFAULT_TIMES)
  const [from, to] = (span ?? ALL_DAY).split('-')
  const setSpan = (f: string, t: string) => { if (f && t && `${f}-${t}` !== span) onChange([`${f}-${t}`]) }
  const length = spanLength(from, to)
  return (
    <div className="field when">
      <span className="label">When to post</span>
      <Seg label="When to post" value={span ? 'spread' : 'times'}
        onChange={(v) => { if (v === 'spread') { if (times.length) setLastTimes(times); onChange([ALL_DAY]) } else onChange(lastTimes) }}
        options={[{ value: 'spread', label: 'Spread through the day' }, { value: 'times', label: 'Set times' }]} />
      {span ? (
        <>
          <div className="when-span">
            <label htmlFor={`${id}-from`}>From</label>
            <input id={`${id}-from`} key={`f${from}`} className="input num" type="time" defaultValue={from} onBlur={(e) => setSpan(e.target.value, to)} />
            <label htmlFor={`${id}-to`}>to</label>
            <input id={`${id}-to`} key={`t${to}`} className="input num" type="time" defaultValue={to === '24:00' ? '00:00' : to} onBlur={(e) => setSpan(from, e.target.value)} />
          </div>
          <span className="hint">
            {length >= 1440 ? 'All day' : `${from} to ${to}`}: {cap} post{cap === 1 ? '' : 's'}, about one every {every(length / Math.max(1, cap))}, each a few minutes off the exact time.
          </span>
        </>
      ) : (
        <>
          <ChipsInput values={times} placeholder="19:00" normalize={timeOk} onChange={(v) => { setLastTimes(v); onChange(v) }} />
          {cap > times.length && (
            <span className="hint">
              {times.length ? `${times.length} time${times.length === 1 ? '' : 's'} means at most ${times.length} post${times.length === 1 ? '' : 's'} a day.` : 'Add a time.'} Spread through the day posts all {cap}.
            </span>
          )}
        </>
      )}
    </div>
  )
}

function health(a: Account): { dot: string; text: string } {
  if (!a.active) return { dot: '', text: 'Paused' }
  if (a.status === 'error') return { dot: 'live', text: a.error?.includes('Reconnect') ? 'Sign-in expired' : 'Needs attention' }
  if (a.token_days !== null && a.token_days < 10) return { dot: 'warn', text: `Sign-in renews in ${Math.max(0, Math.round(a.token_days))} d` }
  if (a.failed_24h) return { dot: 'warn', text: `${a.failed_24h} failed today` }
  return { dot: 'on', text: a.last_check ? `Healthy, checked ${ago(a.last_check)}` : 'Connected' }
}

function AccountRow({ a, groups, selected, onSelect, onReconnect }: {
  a: Account; groups: Group[]; selected: boolean; onSelect: () => void; onReconnect: () => void
}) {
  const [open, setOpen] = useState(false)
  const [windows, setWindows] = useState(a.windows)
  const [tags, setTags] = useState(a.tags)
  const [checking, setChecking] = useState(false)
  const h = health(a)
  const save = async (patch: Record<string, unknown>) => {
    try {
      await api.patch(`/api/accounts/${a.id}`, patch)
      void load.accounts()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const check = async () => {
    setChecking(true)
    try {
      const r = await api.post<{ ok: boolean; error?: string }>(`/api/accounts/${a.id}/check`)
      toast(r.ok ? `${a.name} is healthy` : `${a.name}: ${r.error}`, r.ok ? 'good' : 'bad')
      void load.accounts()
    } finally {
      setChecking(false)
    }
  }
  const remove = async () => {
    if (!confirmDanger(`Disconnect ${a.name}? Its queued posts are cancelled. Posts already published stay up.`)) return
    await api.del(`/api/accounts/${a.id}`)
    toast(`${a.name} disconnected`)
    void load.accounts()
  }
  return (
    <div className={`acct2${a.active ? '' : ' off'}${selected ? ' sel' : ''}`}>
      <div className="acct2-main">
        <input type="checkbox" checked={selected} onChange={onSelect} aria-label={`Select ${a.name}`} />
        <button className="btn ghost icon sm" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Details">
          {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        <div className="id">
          <Avatar src={a.avatar} name={a.name} platform={a.platform} large />
          <div><b>{a.name}</b><span>{a.handle || a.external_id}</span></div>
        </div>
        <div className="readout">
          <b>{compact(a.followers)}</b>
          <span>{a.platform === 'youtube' ? 'subscribers' : 'followers'}{a.followers_gained > 0 ? `, +${compact(a.followers_gained)}` : ''}</span>
        </div>
        <div className="readout"><b>{compact(a.views)}</b><span>views from Surge</span></div>
        <div className="readout">
          <b className="num">{a.today}/{a.cap_now}</b>
          <span>{a.warmup && a.cap_now < a.daily_cap ? 'today, warming up' : 'today'}</span>
        </div>
        <div className="acct-health">
          <span className="status"><span className={`dot ${h.dot}`} />{h.text}</span>
          <span className="faint">{a.next_post ? `Next ${dayClock(a.next_post)}` : a.last_post_at ? `Last post ${ago(a.last_post_at)}` : 'Nothing queued'}</span>
        </div>
        <div className="acct-actions">
          {a.status === 'error' && <button className="btn sm primary" onClick={onReconnect}>Reconnect</button>}
          <button className="btn ghost icon sm" onClick={check} disabled={checking} aria-label={`Check ${a.name}`} title="Check sign-in"><ShieldCheck size={15} /></button>
          <button type="button" role="switch" aria-checked={!!a.active} aria-label={a.active ? 'Pause account' : 'Resume account'} className="toggle" onClick={() => save({ active: a.active ? 0 : 1 })} />
          <button className="btn ghost icon sm" onClick={remove} aria-label={`Disconnect ${a.name}`} title="Disconnect"><Trash2 size={15} /></button>
        </div>
      </div>
      {a.error && a.status === 'error' && <div className="err">{a.error}</div>}
      {open && (
        <div className="acct2-more">
          <PostingTimes id={`w-${a.id}`} windows={windows} cap={a.daily_cap} onChange={(v) => { setWindows(v); void save({ windows: v }) }} />
          <div className="field tags">
            <span className="label">Niche tags</span>
            <ChipsInput values={tags} placeholder="streamer or game, * for any" normalize={(s) => s.trim().toLowerCase()} onChange={(v) => { setTags(v); void save({ tags: v }) }} />
          </div>
          <div className="field cap">
            <label htmlFor={`cap-${a.id}`}>Posts a day</label>
            <input id={`cap-${a.id}`} className="input num" type="number" min={1} max={50} defaultValue={a.daily_cap}
              onBlur={(e) => Number(e.target.value) !== a.daily_cap && save({ daily_cap: Number(e.target.value) })} />
          </div>
          <div className="field grp">
            <label htmlFor={`grp-${a.id}`}>Group</label>
            <select id={`grp-${a.id}`} className="select" value={a.group_id ?? ''} onChange={(e) => save({ group_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">No group</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </div>
          <div className="field">
            <span className="label">New account</span>
            <Toggle checked={!!a.warmup} onChange={(v) => save({ warmup: v ? 1 : 0 })} label="Warm up: 1 post a day, one more each week" />
          </div>
          {a.warmup && a.cap_now < a.daily_cap ? (
            <p className="warm-note">
              Warm-up holds this account at {a.cap_now} post{a.cap_now === 1 ? '' : 's'} a day for now and adds one a week, so {a.daily_cap} a day is {a.daily_cap - a.cap_now} week{a.daily_cap - a.cap_now === 1 ? '' : 's'} away.{' '}
              <button className="link-btn" onClick={() => save({ warmup: 0 })}>Turn warm-up off</button>
            </p>
          ) : null}
        </div>
      )}
    </div>
  )
}

function GroupsModal({ onClose }: { onClose: () => void }) {
  const groups = useStore((s) => s.groups)
  const [name, setName] = useState('')
  const add = async () => {
    try {
      await api.post('/api/groups', { name })
      setName('')
      void load.groups()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <Modal title="Groups" onClose={onClose}>
      <p className="muted" style={{ marginTop: 0 }}>Group accounts that share a niche (a streamer's fan pages, a GTA network). Set the group's posting times, tags and cap once, then push them to every member.</p>
      <div className="inline-form" style={{ marginBottom: 16 }}>
        <input className="input" placeholder="New group name, e.g. Kai network" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && add()} />
        <button className="btn primary" disabled={!name.trim()} onClick={add}>Add group</button>
      </div>
      {groups.map((g) => <GroupRow key={g.id} g={g} />)}
    </Modal>
  )
}

function GroupRow({ g }: { g: Group }) {
  const [windows, setWindows] = useState(g.windows)
  const [tags, setTags] = useState(g.tags)
  const patch = (p: Record<string, unknown>) => api.patch(`/api/groups/${g.id}`, p).then(() => load.groups())
  return (
    <div className="group-row">
      <header>
        <b>{g.name}</b><span className="faint">{g.members} account{g.members === 1 ? '' : 's'}</span>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={async () => { const r = await api.post<{ updated: number }>(`/api/groups/${g.id}/apply`); toast(`Applied to ${r.updated} account${r.updated === 1 ? '' : 's'}`, 'good'); void load.accounts() }}>Apply to members</button>
        <button className="btn ghost icon sm" aria-label={`Delete ${g.name}`} onClick={async () => { if (confirmDanger(`Delete the group ${g.name}? Its accounts stay connected.`)) { await api.del(`/api/groups/${g.id}`); void load.groups(); void load.accounts() } }}><Trash2 size={14} /></button>
      </header>
      <div className="grid2">
        <PostingTimes id={`gw-${g.id}`} windows={windows} cap={g.daily_cap} onChange={(v) => { setWindows(v); void patch({ windows: v }) }} />
        <div className="field"><span className="label">Niche tags</span><ChipsInput values={tags} normalize={(s) => s.trim().toLowerCase()} placeholder="kaicenat" onChange={(v) => { setTags(v); void patch({ tags: v }) }} /></div>
      </div>
      <div className="field" style={{ maxWidth: 160, marginTop: 10 }}>
        <label htmlFor={`gcap-${g.id}`}>Posts a day</label>
        <input id={`gcap-${g.id}`} className="input num" type="number" min={1} max={50} defaultValue={g.daily_cap} onBlur={(e) => patch({ daily_cap: Number(e.target.value) })} />
      </div>
    </div>
  )
}

function InstagramModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<'login' | 'token'>('login')
  const [token, setToken] = useState('')
  const [pasted, setPasted] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (fn: () => Promise<{ ids: number[] }>) => {
    setBusy(true)
    try {
      const r = await fn()
      toast(`Connected ${r.ids.length} Instagram account${r.ids.length === 1 ? '' : 's'}`, 'good')
      void load.accounts()
      onClose()
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }
  const openLogin = async () => {
    try {
      const r = await api.get<{ url: string }>('/api/accounts/instagram/auth-url')
      window.open(r.url, '_blank')
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <Modal title="Connect Instagram" onClose={onClose}>
      <div style={{ display: 'grid', gap: 16 }}>
        <Seg label="Method" value={tab} onChange={setTab} options={[{ value: 'login', label: 'Sign in with Instagram' }, { value: 'token', label: 'Paste an access token' }]} />
        {tab === 'login' ? (
          <>
            <p className="muted" style={{ margin: 0 }}>Sign in as the account you want to add. If your redirect address is the local one, Surge finishes by itself; otherwise copy the address of the page you land on and paste it below. Setup has the full checklist.</p>
            <div><button className="btn primary" onClick={openLogin}>Open Instagram sign-in</button></div>
            <div className="field">
              <label htmlFor="igc">Address you landed on</label>
              <input id="igc" className="input" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="https://localhost:8765/ig-callback?code=…" spellCheck={false} />
            </div>
            <div><button className="btn" disabled={busy || !pasted.includes('code')} onClick={() => run(() => api.post('/api/accounts/instagram/code', { url: pasted }))}>Finish connecting</button></div>
          </>
        ) : (
          <>
            <p className="muted" style={{ margin: 0 }}>Paste an Instagram login token (IGAA…) from your app's API setup page, or a Business portfolio system-user token (EAA…) to add every Instagram account linked to your Pages at once.</p>
            <textarea className="textarea" rows={3} value={token} onChange={(e) => setToken(e.target.value)} placeholder="IGAA… or EAA…" spellCheck={false} aria-label="Access token" />
            <div><button className="btn primary" disabled={busy || token.trim().length < 20} onClick={() => run(() => api.post('/api/accounts/instagram/token', { token }))}>Connect</button></div>
          </>
        )}
      </div>
    </Modal>
  )
}

export function Accounts({ goSetup }: { goSetup: () => void }) {
  const accounts = useStore((s) => s.accounts)
  const groups = useStore((s) => s.groups)
  const hasSecret = useStore((s) => s.hasGoogleSecret)
  const [ig, setIg] = useState(false)
  const [groupsOpen, setGroupsOpen] = useState(false)
  const [filter, setFilter] = useState<string>('all')
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [bulkCap, setBulkCap] = useState(3)

  const shown = useMemo(() => accounts.filter((a) =>
    filter === 'all' ? true : filter === 'none' ? !a.group_id : filter === 'issues' ? a.status === 'error' || a.failed_24h > 0 : String(a.group_id) === filter),
  [accounts, filter])
  const yt = shown.filter((a) => a.platform === 'youtube')
  const insta = shown.filter((a) => a.platform === 'instagram')
  const issues = accounts.filter((a) => a.status === 'error' || a.failed_24h > 0).length

  const connectYt = async () => {
    if (!hasSecret) {
      toast('Add your Google client file in Setup first', 'bad')
      goSetup()
      return
    }
    try {
      await api.post('/api/accounts/youtube/connect')
      toast('Your browser opened. Pick the channel and allow access.')
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const bulk = async (patch: Record<string, unknown>, label: string) => {
    try {
      await api.post('/api/accounts/bulk', { ids: [...sel], patch })
      toast(`${label} for ${sel.size} account${sel.size === 1 ? '' : 's'}`, 'good')
      void load.accounts()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const section = (title: string, list: typeof accounts, target: number, connect: () => void, connectLabel: string, empty: string) => (
    <section className="acct-group">
      <header>
        <h2>{title}</h2>
        <span className="cap">{list.length} of {target}</span>
        <button className="link-btn" onClick={() => setSel(new Set(list.map((a) => a.id)))}>Select all</button>
        <div className="actions"><button className="btn primary" onClick={connect}>{connectLabel}</button></div>
      </header>
      <div className="panel">
        {list.length === 0 ? <div style={{ padding: 18 }} className="muted">{empty}</div>
          : list.map((a) => <AccountRow key={a.id} a={a} groups={groups} selected={sel.has(a.id)} onSelect={() => toggle(a.id)} onReconnect={a.platform === 'youtube' ? connectYt : () => setIg(true)} />)}
      </div>
    </section>
  )

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Accounts</h1>
          <p>Every channel and profile Surge posts to: health, pace and niche. Open a row for posting times, tags, group and warm-up.</p>
        </div>
        <div className="actions">
          <button className="btn ghost" onClick={() => setGroupsOpen(true)}><Users size={15} />Groups</button>
          <button className="btn ghost" onClick={() => api.post('/api/accounts/check-all').then(() => toast('Checking every sign-in'))}><ShieldCheck size={15} />Check all</button>
          <button className="btn ghost" onClick={() => api.post('/api/accounts/refresh').then(() => toast('Refreshing views and followers'))}><RefreshCw size={15} />Refresh stats</button>
        </div>
      </div>

      <div className="toolbar">
        <Seg label="Show" value={filter} onChange={setFilter} options={[
          { value: 'all', label: 'All', n: accounts.length },
          ...groups.map((g) => ({ value: String(g.id), label: g.name, n: accounts.filter((a) => a.group_id === g.id).length })),
          { value: 'none', label: 'No group', n: accounts.filter((a) => !a.group_id).length },
          { value: 'issues', label: 'Needs attention', n: issues },
        ]} />
      </div>

      {sel.size > 0 && (
        <div className="bulk-bar" role="region" aria-label="Bulk actions">
          <b>{sel.size} selected</b>
          <button className="btn sm" onClick={() => bulk({ active: 0 }, 'Paused')}>Pause</button>
          <button className="btn sm" onClick={() => bulk({ active: 1 }, 'Resumed')}>Resume</button>
          <button className="btn sm" onClick={() => bulk({ warmup: 1 }, 'Warm-up on')}>Warm up</button>
          <button className="btn sm" onClick={() => bulk({ warmup: 0 }, 'Warm-up off')}>Warm-up off</button>
          <button className="btn sm" onClick={() => bulk({ windows: [ALL_DAY] }, 'Spread through the day')}>Spread through the day</button>
          <span className="inline-form">
            <input className="input num" type="number" min={1} max={50} value={bulkCap} onChange={(e) => setBulkCap(Number(e.target.value))} aria-label="Posts a day" style={{ width: 64 }} />
            <button className="btn sm" onClick={() => bulk({ daily_cap: bulkCap }, `Set ${bulkCap} a day`)}>Set posts a day</button>
          </span>
          <select className="select" style={{ width: 170, height: 28 }} value="" aria-label="Move to group" onChange={(e) => e.target.value && bulk({ group_id: e.target.value === 'none' ? null : Number(e.target.value) }, 'Group set')}>
            <option value="">Move to group…</option>
            {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            <option value="none">No group</option>
          </select>
          <span style={{ flex: 1 }} />
          <button className="btn ghost sm" onClick={() => setSel(new Set())}>Clear</button>
        </div>
      )}

      {section('YouTube channels', yt, YT_TARGET, connectYt, 'Connect YouTube channel',
        hasSecret ? 'No channels here yet. Connect one: your browser opens, you pick the channel, and it shows up here.'
          : 'Finish the YouTube steps in Setup first, then connect channels here.')}
      {section('Instagram accounts', insta, IG_TARGET, () => setIg(true), 'Connect Instagram',
        'Instagram needs Business or Creator accounts added as testers in your Meta app. Setup walks you through it.')}
      {ig && <InstagramModal onClose={() => setIg(false)} />}
      {groupsOpen && <GroupsModal onClose={() => { setGroupsOpen(false); void load.accounts() }} />}
    </>
  )
}
