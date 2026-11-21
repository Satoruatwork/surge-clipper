import { Plus, Trash2, Wand2 } from 'lucide-react'
import { useStore } from '../lib/store'
import type { Cue, CueKind, FxPlan } from '../lib/types'

const KIND_LABEL: Record<CueKind, string> = { sound: 'Sound', zoom: 'Punch zoom', shake: 'Shake', flash: 'Flash', sticker: 'Sticker' }
const ZOOMS = [{ v: 0.06, l: 'Subtle' }, { v: 0.13, l: 'Punch' }, { v: 0.22, l: 'Big' }]

export function FxEditor({ plan, duration, peak, onChange, onAuto, busy }: {
  plan: FxPlan | null
  duration: number
  peak: number
  onChange: (p: FxPlan) => void
  onAuto: () => void
  busy: boolean
}) {
  const lib = useStore((s) => s.library)
  const p: FxPlan = plan ?? { cues: [], music: null }
  const sounds = [...(lib?.sounds ?? [])].sort((a, b) => (b.rank ?? 6) - (a.rank ?? 6))
  const set = (cues: Cue[]) => onChange({ ...p, cues: [...cues].sort((a, b) => a.t - b.t), source: 'edited' })
  const update = (i: number, patch: Partial<Cue>) => set(p.cues.map((c, j) => (j === i ? { ...c, ...patch } : c)))
  const changeKind = (i: number, kind: CueKind) => {
    const base: Cue = { t: p.cues[i].t, kind, why: p.cues[i].why }
    if (kind === 'sound') base.tags = ['impact']
    if (kind === 'sticker') base.name = lib?.stickers[0]?.name ?? 'skull'
    if (kind === 'shake') base.d = 0.6
    set(p.cues.map((c, j) => (j === i ? base : c)))
  }
  const sourceText = p.source === 'ai' || p.source === 'claude' ? 'Placed by the AI writer' : p.source === 'builtin' ? 'Placed by Surge' : p.cues.length ? 'Edited by you' : 'No effects yet'

  return (
    <div className="section">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h3 style={{ margin: 0 }}>Sound and effects</h3>
        <span className="faint" style={{ fontSize: 13 }}>{sourceText}</span>
        <span style={{ flex: 1 }} />
        <button className="btn sm" onClick={onAuto} disabled={busy}><Wand2 size={14} />Place again</button>
      </div>
      {(p as FxPlan & { sounds_skipped?: boolean }).sounds_skipped && (
        <p className="faint" style={{ margin: 0, fontSize: 13 }}>This clip is in the share that skips meme sounds, so they keep their punch on the others. Add one here if this moment needs it.</p>
      )}
      <div className="field">
        <label htmlFor="fx-music">Music</label>
        <select id="fx-music" className="select" value={p.music?.song ?? ''}
          onChange={(e) => onChange({ ...p, music: e.target.value ? { song: e.target.value } : null, source: 'edited' })}>
          <option value="">No music</option>
          {(lib?.songs ?? []).map((s) => (
            <option key={s.id} value={s.id}>{s.name}{s.moods?.length ? `, ${s.moods.join(' / ')}` : ''}</option>
          ))}
        </select>
        <span className="hint">The drop lands on the peak at {peak.toFixed(1)} s, and the music ducks while the streamer talks.</span>
      </div>
      <div className="cues">
        {p.cues.length === 0 && <span className="faint" style={{ fontSize: 13 }}>No effects on this clip.</span>}
        {p.cues.map((c, i) => (
          <div className="cue" key={i}>
            <input className="input num" type="number" step={0.1} min={0} max={duration} value={c.t}
              aria-label="Time in seconds" onChange={(e) => update(i, { t: Number(e.target.value) })} />
            <select className="select" value={c.kind} aria-label="Effect" onChange={(e) => changeKind(i, e.target.value as CueKind)}>
              {(Object.keys(KIND_LABEL) as CueKind[]).map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
            {c.kind === 'sound' && (
              <select className="select" aria-label="Sound" value={c.name ?? ''}
                onChange={(e) => update(i, e.target.value ? { name: e.target.value, tags: undefined } : { name: undefined, tags: c.tags?.length ? c.tags : ['impact'] })}>
                <option value="">Best fit for {c.tags?.join(', ') || 'the moment'}</option>
                {sounds.map((s) => <option key={s.id} value={s.name}>{s.name}</option>)}
              </select>
            )}
            {c.kind === 'sticker' && (
              <span className="cue-sticker">
                {lib?.stickers.find((s) => s.name === c.name)?.url && <img src={lib.stickers.find((s) => s.name === c.name)!.url!} alt="" />}
                <select className="select" aria-label="Sticker" value={c.name} onChange={(e) => update(i, { name: e.target.value })}>
                  {(lib?.stickers ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </span>
            )}
            {c.kind === 'zoom' && (
              <select className="select" aria-label="Zoom size" value={c.amount ?? 0.13} onChange={(e) => update(i, { amount: Number(e.target.value) })}>
                {ZOOMS.map((z) => <option key={z.v} value={z.v}>{z.l}</option>)}
              </select>
            )}
            {c.kind === 'shake' && (
              <input className="input num" type="number" step={0.1} min={0.2} max={1.5} value={c.d ?? 0.6} aria-label="Shake length in seconds"
                onChange={(e) => update(i, { d: Number(e.target.value) })} />
            )}
            {c.kind === 'flash' && <span />}
            <span className="cue-why" title={c.why}>{c.why}</span>
            <button className="btn ghost icon sm" aria-label="Remove effect" onClick={() => set(p.cues.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </div>
        ))}
      </div>
      <div>
        <button className="btn sm" onClick={() => set([...p.cues, { t: Math.round(peak * 10) / 10, kind: 'zoom', amount: 0.13, why: 'added by you' }])}>
          <Plus size={14} />Add effect
        </button>
      </div>
    </div>
  )
}
