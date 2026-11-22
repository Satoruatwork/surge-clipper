import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { errorText, load, toast, useStore } from '../lib/store'
import type { PostRow } from '../lib/types'
import { dayClock, fromLocalInput, toLocalInput, until } from '../lib/format'
import { Avatar, Modal, Seg } from '../components/ui'

const HOUR = 3600

function PostModal({ p, onClose }: { p: PostRow; onClose: () => void }) {
  const [at, setAt] = useState(toLocalInput(p.scheduled_at))
  const [title, setTitle] = useState(p.title)
  const editable = p.status === 'queued' || p.status === 'failed'
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn()
      toast(ok, 'good')
      void load.posts()
      onClose()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  return (
    <Modal title={p.account_name ?? 'Post'} onClose={onClose}>
      <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 18 }}>
        <div style={{ aspectRatio: '9/16', borderRadius: 8, overflow: 'hidden', background: 'var(--night)' }}>
          {p.thumb_url && <img src={p.thumb_url} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />}
        </div>
        <div style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
          <div className="status"><span className={`dot ${p.status === 'failed' ? 'live' : p.status === 'published' || p.status === 'scheduled' ? 'on' : 'warn'}`} />{p.status}, {dayClock(p.scheduled_at)}</div>
          {p.error && <p style={{ margin: 0, color: 'var(--sodium)', fontSize: 13 }}>{p.error}</p>}
          <div className="field">
            <label htmlFor="pt">Title</label>
            <input id="pt" className="input" value={title} disabled={!editable} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="pa">Goes out at</label>
            <input id="pa" className="input" type="datetime-local" value={at} disabled={!editable} onChange={(e) => setAt(e.target.value)} />
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {editable && <button className="btn primary" onClick={() => act(() => api.patch(`/api/posts/${p.id}`, { title, scheduled_at: fromLocalInput(at) }), 'Post updated')}>Save</button>}
            {p.status === 'queued' && <button className="btn" onClick={() => act(() => api.post(`/api/posts/${p.id}/now`), 'Posting now')}>Post now</button>}
            {p.status === 'failed' && <button className="btn" onClick={() => act(() => api.post(`/api/posts/${p.id}/retry`), 'Retrying')}>Retry</button>}
            {editable && <button className="btn danger" onClick={() => act(() => api.post(`/api/posts/${p.id}/cancel`), 'Post cancelled')}>Cancel post</button>}
            {p.url && <a className="btn ghost" href={p.url} target="_blank" rel="noreferrer">Open on {p.platform === 'youtube' ? 'YouTube' : 'Instagram'}</a>}
          </div>
        </div>
      </div>
    </Modal>
  )
}

export function Schedule() {
  const posts = useStore((s) => s.posts)
  const accounts = useStore((s) => s.accounts)
  const uploads = useStore((s) => s.uploads)
  const [span, setSpan] = useState<'24' | '48' | '168'>('48')
  const [open, setOpen] = useState<PostRow | null>(null)
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    void load.posts()
    const t = setInterval(() => setNow(Date.now() / 1000), 30000)
    return () => clearInterval(t)
  }, [])

  const hours = Number(span)
  const t0 = Math.floor((now - Math.min(6, hours * 0.12) * HOUR) / HOUR) * HOUR
  const t1 = t0 + hours * HOUR
  const pct = (t: number) => ((t - t0) / (t1 - t0)) * 100
  const tickEvery = hours <= 24 ? 2 : hours <= 48 ? 4 : 12
  const ticks = useMemo(() => {
    const out: number[] = []
    for (let t = t0; t <= t1; t += HOUR) if (new Date(t * 1000).getHours() % tickEvery === 0) out.push(t)
    return out
  }, [t0, t1, tickEvery])

  const lanes = accounts.filter((a) => a.active || posts.some((p) => p.account_id === a.id))
  const upcoming = posts.filter((p) => ['queued', 'uploading', 'failed'].includes(p.status) || (p.status === 'scheduled' && p.scheduled_at > now))

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Schedule</h1>
          <p>Every account's posting slots. YouTube Shorts upload early and YouTube publishes them on time, even with this PC off. Instagram Reels post from this app at their slot, so keep it running.</p>
        </div>
        <div className="actions">
          <Seg label="Range" value={span} onChange={setSpan} options={[{ value: '24', label: '24 hours' }, { value: '48', label: '2 days' }, { value: '168', label: 'Week' }]} />
        </div>
      </div>

      {lanes.length === 0 ? (
        <div className="empty">
          <h3>No accounts to schedule on</h3>
          <p>Connect your YouTube channels and Instagram accounts on the Accounts page. Approved clips are then spread across their posting windows automatically.</p>
        </div>
      ) : (
        <div className="lanes" role="table" aria-label="Posting timeline">
          <div className="lane axis" role="row">
            <div className="who" />
            <div className="track">
              {ticks.map((t) => (
                <span key={t} style={{ left: `${pct(t)}%` }}>
                  {new Date(t * 1000).getHours() === 0
                    ? new Date(t * 1000).toLocaleDateString([], { weekday: 'short', day: 'numeric' })
                    : new Date(t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              ))}
            </div>
          </div>
          {lanes.map((a) => {
            const mine = posts.filter((p) => p.account_id === a.id && p.scheduled_at >= t0 && p.scheduled_at <= t1)
            return (
              <div className="lane" role="row" key={a.id}>
                <div className="who">
                  <Avatar src={a.avatar} name={a.name} platform={a.platform} />
                  <div>
                    <b>{a.name}</b>
                    <span>{a.today}/{a.cap_now} today</span>
                  </div>
                </div>
                <div className="track">
                  {ticks.map((t) => <i key={t} className={`tick${new Date(t * 1000).getHours() === 0 ? ' day' : ''}`} style={{ left: `${pct(t)}%` }} />)}
                  <i className="now" style={{ left: `${pct(now)}%` }} />
                  {mine.map((p) => (
                    <button
                      key={p.id}
                      className={`pin ${p.status}`}
                      style={{ left: `${pct(p.scheduled_at)}%` }}
                      onClick={() => setOpen(p)}
                      title={`${p.title}\n${dayClock(p.scheduled_at)}, ${p.status}${uploads[p.id] && p.status === 'uploading' ? ` ${Math.round(uploads[p.id] * 100)}%` : ''}`}
                      aria-label={`${p.title}, ${p.status}, ${dayClock(p.scheduled_at)}`}
                    >
                      {p.thumb_url && <img src={p.thumb_url} alt="" />}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <div style={{ marginTop: 28 }}>
        <h2 style={{ marginBottom: 8 }}>Up next</h2>
        {upcoming.length === 0 ? (
          <p className="muted">Nothing queued. Approve clips and they land here.</p>
        ) : (
          <table className="table">
            <thead>
              <tr><th>Account</th><th>Clip</th><th>Goes out</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {upcoming.map((p) => (
                <tr key={p.id}>
                  <td><span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}><Avatar src={p.avatar} name={p.account_name} platform={p.platform} />{p.account_name}</span></td>
                  <td className="t-title" title={p.title}>{p.title}</td>
                  <td className="num">{dayClock(p.scheduled_at)} <span className="faint">{until(p.scheduled_at)}</span></td>
                  <td>
                    <span className="status"><span className={`dot ${p.status === 'failed' ? 'live' : p.status === 'scheduled' ? 'on' : 'warn'}`} />
                      {p.status === 'uploading' && uploads[p.id] ? `uploading ${Math.round(uploads[p.id] * 100)}%` : p.status}
                    </span>
                  </td>
                  <td style={{ textAlign: 'right' }}><button className="btn sm" onClick={() => setOpen(p)}>Edit</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {open && <PostModal p={open} onClose={() => setOpen(null)} />}
    </>
  )
}
