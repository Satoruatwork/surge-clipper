import { useEffect, useMemo, useState } from 'react'
import { ExternalLink, FolderOpen, RotateCcw, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, load, toast, useStore } from '../lib/store'
import type { Clip, FxPlan, Word } from '../lib/types'
import { chatClassLabel, dayClock, fromLocalInput, timecode, toLocalInput } from '../lib/format'
import { Avatar, ChipsInput, Drawer, Field, Seg, Steps, STEP_LABEL, confirmDanger } from './ui'
import { FxEditor } from './FxEditor'

const LAYOUTS = [
  { value: 'split', label: 'Split' }, { value: 'full', label: 'Full' }, { value: 'blur', label: 'Blur' }, { value: 'pip', label: 'PiP' },
]

export function ClipEditor({ id, onClose }: { id: number; onClose: () => void }) {
  const live = useStore((s) => s.clips.find((c) => c.id === id))
  const styles = useStore((s) => s.styles)
  const accounts = useStore((s) => s.accounts)
  const [clip, setClip] = useState<Clip | null>(null)
  const [hook, setHook] = useState('')
  const [title, setTitle] = useState('')
  const [desc, setDesc] = useState('')
  const [tags, setTags] = useState<string[]>([])
  const [words, setWords] = useState<Word[]>([])
  const [edited, setEdited] = useState<Set<number>>(new Set())
  const [layout, setLayout] = useState('split')
  const [style, setStyle] = useState('punch')
  const [trimStart, setTrimStart] = useState(0)
  const [trimEnd, setTrimEnd] = useState(0)
  const [targets, setTargets] = useState<number[]>([])
  const [when, setWhen] = useState<'slot' | 'pick'>('slot')
  const [at, setAt] = useState(toLocalInput(Date.now() / 1000 + 3600))
  const [busy, setBusy] = useState(false)
  const [fxPlan, setFxPlan] = useState<FxPlan | null>(null)
  const [fxDirty, setFxDirty] = useState(false)

  const reset = (c: Clip) => {
    setClip(c)
    setHook(c.hook ?? '')
    setTitle(c.title ?? '')
    setDesc(c.description ?? '')
    setTags(c.hashtags ?? [])
    setWords(c.words ?? [])
    setEdited(new Set())
    setLayout(c.layout ?? 'split')
    setStyle(c.caption_style ?? 'punch')
    setTrimStart(0)
    setTrimEnd(0)
    setFxPlan(c.fx && 'cues' in c.fx ? (c.fx as FxPlan) : null)
    setFxDirty(false)
  }

  useEffect(() => {
    load.clip(id).then(reset).catch((e) => toast(errorText(e), 'bad'))
  }, [id])

  // pick up a finished re-render
  useEffect(() => {
    if (live && clip && live.updated_at !== clip.updated_at && ['ready', 'approved', 'failed'].includes(live.status)) {
      load.clip(id).then((c) => {
        setClip(c)
        setWords(c.words ?? [])
      }).catch(() => {})
    }
  }, [live?.updated_at, live?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const status = live?.status ?? clip?.status
  const working = !!status && ['queued', 'cutting', 'transcribing', 'writing', 'rendering'].includes(status)
  const copyDirty = clip && (title !== (clip.title ?? '') || desc !== (clip.description ?? '') || tags.join() !== (clip.hashtags ?? []).join())
  const lookDirty = clip && (hook !== (clip.hook ?? '') || edited.size > 0 || layout !== clip.layout || style !== clip.caption_style || trimStart !== 0 || trimEnd !== 0 || fxDirty)
  const posts = clip?.post_list ?? []
  const postedTo = new Set(posts.filter((p) => p.status !== 'cancelled').map((p) => p.account_id))
  const activeAccounts = useMemo(() => accounts.filter((a) => a.active), [accounts])

  const saveCopy = async () => {
    await api.patch(`/api/clips/${id}`, { title, description: desc, hashtags: tags, hook })
  }

  const rerender = async () => {
    setBusy(true)
    try {
      await saveCopy()
      await api.post(`/api/clips/${id}/rerender`, {
        hook, layout, caption_style: style, trim_start: trimStart, trim_end: trimEnd,
        ...(edited.size ? { words } : {}),
        ...(fxDirty ? { fx: fxPlan ?? { cues: [], music: null } } : {}),
      })
      setFxDirty(false)
      toast('Re-rendering with your changes')
      setEdited(new Set())
      setTrimStart(0)
      setTrimEnd(0)
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  const approve = async () => {
    setBusy(true)
    try {
      if (copyDirty) await saveCopy()
      const r = await api.post<{ posts: number[] }>(`/api/clips/${id}/approve`, {
        accounts: targets.length ? targets : null,
        when: when === 'pick' ? fromLocalInput(at) : null,
      })
      toast(r.posts.length ? `Scheduled on ${r.posts.length} account${r.posts.length === 1 ? '' : 's'}` : 'Approved. Connect accounts to schedule it.', 'good')
      setTargets([])
      reset(await load.clip(id))
      void load.posts()
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  const autoFx = async () => {
    setBusy(true)
    try {
      const plan = await api.post<FxPlan>('/api/clips/' + id + '/autofx')
      setFxPlan(plan)
      setFxDirty(true)
      toast('New edit placed. Re-render to hear it.')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy(false)
    }
  }

  const reject = async () => {
    await api.post(`/api/clips/${id}/reject`)
    toast('Rejected')
    onClose()
  }

  const remove = async () => {
    if (!confirmDanger('Delete this clip and its files? Posts already published stay up.')) return
    await api.del(`/api/clips/${id}`)
    toast('Clip deleted')
    onClose()
  }

  if (!clip) {
    return <Drawer title="Loading clip" onClose={onClose}><div className="overlay-body muted">Loading…</div></Drawer>
  }

  const sig = clip.signals ?? {}
  return (
    <Drawer
      title={clip.streamer ? `${clip.streamer}, ${timecode(clip.duration)}` : `Clip ${clip.id}`}
      onClose={onClose}
      aside={clip.chat_class ? <span className={`chip ${clip.chat_class}`}>{chatClassLabel[clip.chat_class] ?? clip.chat_class}</span> : null}
      footer={
        <>
          <button className="btn ghost sm" onClick={() => api.post(`/api/clips/${id}/reveal`)}><FolderOpen size={15} />Show in folder</button>
          <button className="btn ghost sm" onClick={remove}><Trash2 size={15} />Delete</button>
          <span className="grow" />
          {clip.status === 'rejected'
            ? <button className="btn" onClick={() => api.post(`/api/clips/${id}/restore`).then(() => load.clip(id).then(reset))}><RotateCcw size={15} />Restore</button>
            : <button className="btn danger" onClick={reject} disabled={busy}>Reject</button>}
          <button className="btn primary" onClick={approve} disabled={busy || working || !clip.video_url}>
            {targets.length ? `Schedule on ${targets.length} account${targets.length === 1 ? '' : 's'}` : 'Approve and schedule'}
          </button>
        </>
      }
    >
      <div className="editor">
        <div className="player">
          {working ? (
            <div style={{ aspectRatio: '9/16', borderRadius: 10, background: 'var(--night)', display: 'grid', placeContent: 'center', gap: 10, padding: 24, textAlign: 'center' }}>
              <span className="muted">{STEP_LABEL[status!]}</span>
              <Steps status={status!} />
            </div>
          ) : clip.video_url ? (
            <video key={clip.video_url} src={clip.video_url} controls autoPlay playsInline />
          ) : (
            <div className="empty">This clip has no video. {clip.error}</div>
          )}
          <div className="player sig">
            <div className="readout"><b>{clip.score.toFixed(1)}</b><span>score</span></div>
            <div className="readout"><b>{(sig.chat ?? 0).toFixed(1)}</b><span>chat spike</span></div>
            <div className="readout"><b>{(sig.audio ?? 0).toFixed(1)}</b><span>volume spike</span></div>
            <div className="readout"><b>{(sig.voice ?? 0).toFixed(1)}</b><span>voice</span></div>
          </div>
          {clip.chat?.length ? (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {clip.chat.slice(0, 6).map(([m, n]) => <span key={m} className="chip" title={m}>{m.slice(0, 22)}{n > 1 ? ` ×${n}` : ''}</span>)}
            </div>
          ) : null}
        </div>

        <div className="form">
          <Field label="Hook on screen" hint="Shown over the first seconds. Changing it needs a re-render." htmlFor="hook">
            <input id="hook" className="input" value={hook} onChange={(e) => setHook(e.target.value)} maxLength={60} />
          </Field>
          <Field label="Title" hint={`${title.length}/100`} htmlFor="title">
            <input id="title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={100} />
          </Field>
          <Field label="Description" htmlFor="desc">
            <textarea id="desc" className="textarea" value={desc} onChange={(e) => setDesc(e.target.value)} rows={3} />
          </Field>
          <Field label="Hashtags" hint="Each account gets these with the long tail shuffled.">
            <ChipsInput values={tags} onChange={setTags} placeholder="#gta6" normalize={(s) => (s.trim() ? `#${s.trim().replace(/^#+/, '').replace(/[^\w]/g, '')}` : '')} />
          </Field>
          {clip.variants?.length ? (
            <div className="field">
              <span className="label">Other angles, used for your other accounts</span>
              <div style={{ display: 'grid', gap: 6 }}>
                {clip.variants.map((v, i) => (
                  <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 13 }}>
                    <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} className="muted">{v.title}</span>
                    <button className="btn ghost sm" onClick={() => { if (v.title) setTitle(v.title); if (v.hook) setHook(v.hook) }}>Use this</button>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          {copyDirty && !lookDirty && (
            <div><button className="btn" onClick={() => saveCopy().then(() => { toast('Copy saved', 'good'); return load.clip(id).then(reset) }).catch((e) => toast(errorText(e), 'bad'))}>Save copy</button></div>
          )}

          <div className="section">
            <h3>Captions</h3>
            <p className="faint" style={{ margin: 0, fontSize: 13 }}>Click a word to fix it. Words in amber were hard to hear.</p>
            <div className="words">
              {words.length === 0 && <span className="faint">No speech in this clip.</span>}
              {words.map((w, i) => (
                <input
                  key={i}
                  value={w.w}
                  size={Math.max(2, w.w.length)}
                  className={edited.has(i) ? 'edited' : (w.p ?? 1) < 0.55 ? 'low' : ''}
                  aria-label={`Word at ${w.s.toFixed(1)} seconds`}
                  onChange={(e) => {
                    const v = e.target.value
                    setWords((ws) => ws.map((x, j) => (j === i ? { ...x, w: v } : x)))
                    setEdited((s) => new Set(s).add(i))
                  }}
                />
              ))}
            </div>
            <div className="two">
              <div className="field">
                <span className="label">Layout</span>
                <Seg label="Layout" value={layout} onChange={setLayout} options={LAYOUTS} />
              </div>
              <div className="field">
                <span className="label">Caption style</span>
                <Seg label="Caption style" value={style} onChange={setStyle} options={Object.entries(styles).map(([value, label]) => ({ value, label }))} />
              </div>
            </div>
            <div className="two">
              <Field label="Cut from the start (s)" htmlFor="ts">
                <input id="ts" className="input num" type="number" step={0.5} min={0} max={20} value={trimStart} onChange={(e) => setTrimStart(Number(e.target.value))} />
              </Field>
              <Field label="Cut from the end (s)" htmlFor="te">
                <input id="te" className="input num" type="number" step={0.5} min={0} max={20} value={trimEnd} onChange={(e) => setTrimEnd(Number(e.target.value))} />
              </Field>
            </div>
          </div>

          <FxEditor
            plan={fxPlan}
            duration={clip.duration ?? 30}
            peak={Math.max(0, clip.peak - (clip.start ?? clip.peak))}
            busy={busy}
            onAuto={autoFx}
            onChange={(pl) => { setFxPlan(pl); setFxDirty(true) }}
          />
          <div><button className="btn primary" onClick={rerender} disabled={busy || working || !lookDirty}>Re-render with changes</button></div>

          <div className="section">
            <h3>Where it goes</h3>
            {activeAccounts.length === 0 ? (
              <p className="muted" style={{ margin: 0 }}>Connect YouTube channels and Instagram accounts on the Accounts page to schedule clips.</p>
            ) : (
              <>
                <p className="faint" style={{ margin: 0, fontSize: 13 }}>Leave all unticked and Surge picks the accounts with the most room, matching their niche tags.</p>
                <div className="targets">
                  {activeAccounts.map((a) => {
                    const done = postedTo.has(a.id)
                    const on = targets.includes(a.id)
                    return (
                      <label key={a.id} className={`target-opt${on ? ' on' : ''}`} style={done ? { opacity: 0.5 } : undefined}>
                        <input type="checkbox" checked={on} disabled={done} onChange={() => setTargets((t) => (on ? t.filter((x) => x !== a.id) : [...t, a.id]))} />
                        <Avatar src={a.avatar} name={a.name} platform={a.platform} />
                        <span>{a.name}{done ? ', already scheduled' : ''}</span>
                      </label>
                    )
                  })}
                </div>
                <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Seg label="When" value={when} onChange={setWhen} options={[{ value: 'slot', label: 'Next free slot' }, { value: 'pick', label: 'Pick a time' }]} />
                  {when === 'pick' && <input className="input" style={{ width: 220 }} type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />}
                </div>
              </>
            )}
            {posts.filter((p) => p.status !== 'cancelled').map((p) => (
              <div className="post-line" key={p.id}>
                <Avatar src={p.account_avatar} name={p.account_name} platform={p.account_platform} />
                <span>{p.account_name}<span className="faint">, {dayClock(p.scheduled_at)}</span>{p.error && <span style={{ color: 'var(--sodium)', display: 'block', fontSize: 12 }}>{p.error}</span>}</span>
                <span className="status"><span className={`dot ${p.status === 'published' || p.status === 'scheduled' ? 'on' : p.status === 'failed' ? 'live' : 'warn'}`} />{p.status}{p.views ? `, ${p.views.toLocaleString()} views` : ''}</span>
                {p.url ? <a href={p.url} target="_blank" rel="noreferrer" aria-label="Open post"><ExternalLink size={15} /></a> : <span />}
              </div>
            ))}
          </div>
        </div>
      </div>
    </Drawer>
  )
}
