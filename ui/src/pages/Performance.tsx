import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { errorText, toast } from '../lib/store'
import type { Breakdown, Insights } from '../lib/types'
import { compact } from '../lib/format'

function Bars({ data }: { data: { day: string; views: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.views))
  return (
    <div className="bars" role="img" aria-label="Views per day">
      {data.map((d) => (
        <div key={d.day} className="bar" title={`${d.day}: ${d.views.toLocaleString()} views`}>
          <i style={{ height: `${Math.max(2, (d.views / max) * 100)}%` }} />
          <span>{d.day.slice(5)}</span>
        </div>
      ))}
    </div>
  )
}

function Table({ title, rows, unit }: { title: string; rows: Breakdown[]; unit: string }) {
  const max = Math.max(1, ...rows.map((r) => r.avg))
  return (
    <section className="perf-table">
      <h2>{title}</h2>
      {rows.length === 0 ? <p className="faint">No data yet.</p> : (
        <table className="table">
          <thead><tr><th>{unit}</th><th>Posts</th><th>Views</th><th>Per post</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td>{r.name}</td>
                <td className="num">{r.posts}</td>
                <td className="num">{compact(r.views)}</td>
                <td className="num"><span className="avg-bar"><i style={{ width: `${(r.avg / max) * 100}%` }} /></span>{compact(r.avg)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export function Performance() {
  const [d, setD] = useState<Insights | null>(null)
  const [store, setStore] = useState<{ raw_gb: number; clips_gb: number; runs_gb: number; total_gb: number; free_gb: number } | null>(null)
  const [days, setDays] = useState(3)
  const load = () => {
    api.get<Insights>('/api/insights').then(setD).catch(() => {})
    api.get<typeof store>('/api/storage').then(setStore).catch(() => {})
  }
  useEffect(load, [])
  const clean = async () => {
    try {
      const r = await api.post<{ freed_gb: number }>('/api/storage/cleanup', { raw_days: days })
      toast(`Freed ${r.freed_gb} GB`, 'good')
      load()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  if (!d) return <p className="muted">Loading performance…</p>
  const earned = d.campaigns.reduce((n, c) => n + c.earned, 0)
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Performance</h1>
          <p>What's getting views, so you can lean into it: streamers, reactions, sounds, layouts and accounts, ranked by views per post. Numbers refresh every 30 minutes from YouTube and Instagram.</p>
        </div>
      </div>
      <div className="kpis">
        <div className="kpi"><b>{compact(d.total_views)}</b><span>views on Surge posts</span></div>
        <div className="kpi"><b>{d.posts}</b><span>posts live or scheduled</span></div>
        <div className="kpi"><b>{compact(d.posts ? d.total_views / d.posts : 0)}</b><span>views per post</span></div>
        <div className="kpi"><b>${earned.toFixed(2)}</b><span>campaign earnings</span></div>
      </div>
      <section className="perf-table">
        <h2>Views per day</h2>
        {d.daily.length ? <Bars data={d.daily} /> : <p className="faint">The daily chart fills in once posts have been tracked for two days.</p>}
      </section>
      <section className="perf-table">
        <h2>Top posts</h2>
        {d.top.length === 0 ? <p className="faint">No posts yet. Approve clips and connect accounts to start.</p> : (
          <div className="top-posts">
            {d.top.map((t, i) => (
              <a key={i} className="top-post" href={t.url ?? undefined} target="_blank" rel="noreferrer">
                <div className="mini">{t.thumb_url && <img src={t.thumb_url} alt="" />}</div>
                <div><b>{t.title}</b><span className="faint">{t.account}</span></div>
                <span className="num">{compact(t.views)}</span>
              </a>
            ))}
          </div>
        )}
      </section>
      <div className="perf-grid">
        <Table title="Streamers" rows={d.streamers} unit="Streamer" />
        <Table title="Chat reaction" rows={d.reactions} unit="Reaction" />
        <Table title="Meme sounds" rows={d.sounds} unit="Sound" />
        <Table title="Layouts" rows={d.layouts} unit="Layout" />
        <Table title="Accounts" rows={d.accounts} unit="Account" />
      </div>
      {store && (
        <section className="perf-table">
          <h2>Storage</h2>
          <p className="muted" style={{ marginTop: 0 }}>Surge uses {store.total_gb} GB ({store.clips_gb} GB finished clips, {store.raw_gb} GB source footage, {store.runs_gb} GB recordings). {store.free_gb} GB free on the drive.</p>
          <div className="inline-form">
            <span className="muted">Delete source footage older than</span>
            <input className="input num" style={{ width: 64 }} type="number" min={0} value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Days" />
            <span className="muted">days, plus recordings of stopped streams</span>
            <button className="btn" onClick={clean}>Free up space</button>
          </div>
          <p className="faint">Finished clips stay. Clips whose footage is gone can't be re-rendered.</p>
        </section>
      )}
    </>
  )
}
