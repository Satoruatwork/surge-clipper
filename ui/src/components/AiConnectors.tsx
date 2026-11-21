import { useEffect, useState } from 'react'
import { ExternalLink, Plus } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, toast } from '../lib/store'
import type { Connector, ConnectorPreset, ConnectorsData } from '../lib/types'
import { confirmDanger, Field, Seg } from './ui'

type Result = { ok: boolean; message: string }
type Protocol = 'openai' | 'anthropic'
interface Draft { id?: string; preset: string; name: string; protocol: Protocol; base_url: string; api_key: string; model: string; rank_model: string }

const fromPreset = (p: ConnectorPreset): Draft => ({ preset: p.id, name: p.name, protocol: p.protocol, base_url: p.base_url, api_key: '', model: p.model, rank_model: p.rank_model })
const payload = (d: Draft) => ({ id: d.id, name: d.name, protocol: d.protocol, base_url: d.base_url, api_key: d.api_key, model: d.model, rank_model: d.rank_model })
const hostOf = (url: string) => {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** Add, test and pick the AI service behind the writer: Anthropic, or any OpenAI-compatible service by base URL + key. */
export function AiConnectors({ onChange }: { onChange?: () => void }) {
  const [data, setData] = useState<ConnectorsData | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [models, setModels] = useState<string[]>([])
  const [busy, setBusy] = useState('')
  const [formResult, setFormResult] = useState<Result | null>(null)
  const [rowResult, setRowResult] = useState<Record<string, Result>>({})

  useEffect(() => {
    api.get<ConnectorsData>('/api/connectors').then(setData).catch((e) => toast(errorText(e), 'bad'))
  }, [])

  const apply = (d: ConnectorsData) => {
    setData(d)
    onChange?.()
  }
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d))

  const openNew = () => {
    const p = data?.presets[0]
    if (!p) return
    setDraft(fromPreset(p))
    setModels([])
    setFormResult(null)
  }
  const pickPreset = (id: string) => {
    const p = data?.presets.find((x) => x.id === id)
    if (!p) return
    setDraft(fromPreset(p))
    setModels([])
    setFormResult(null)
  }
  const edit = (c: Connector) => {
    setDraft({ id: c.id, preset: '', name: c.name, protocol: c.protocol, base_url: c.base_url, api_key: '', model: c.model, rank_model: c.rank_model })
    setModels([])
    setFormResult(null)
  }

  const fetchModels = async () => {
    if (!draft) return
    setBusy('models')
    setFormResult(null)
    try {
      const r = await api.post<{ models: string[] }>('/api/connectors/models', payload(draft))
      setModels(r.models)
      setFormResult({ ok: r.models.length > 0, message: r.models.length ? `${r.models.length} models found. Click a model box and pick one.` : 'The service answered but listed no models.' })
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
    } finally {
      setBusy('')
    }
  }
  const testDraft = async () => {
    if (!draft) return
    setBusy('test')
    setFormResult({ ok: true, message: 'Sending a real clip prompt…' })
    try {
      setFormResult(await api.post<Result>('/api/connectors/test', payload(draft)))
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
    } finally {
      setBusy('')
    }
  }
  const save = async () => {
    if (!draft) return
    setBusy('save')
    try {
      apply(await api.post<ConnectorsData>('/api/connectors', payload(draft)))
      setDraft(null)
      toast('Connector saved', 'good')
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
    } finally {
      setBusy('')
    }
  }
  const use = async (c: Connector) => {
    setBusy(`use:${c.id}`)
    try {
      apply(await api.post<ConnectorsData>(`/api/connectors/${encodeURIComponent(c.id)}/activate`))
      toast(`${c.name} writes your clips now`, 'good')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy('')
    }
  }
  const remove = async (c: Connector) => {
    if (!confirmDanger(`Remove ${c.name}? Its key is deleted from this PC.`)) return
    try {
      apply(await api.del<ConnectorsData>(`/api/connectors/${encodeURIComponent(c.id)}`))
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }
  const testRow = async (c: Connector) => {
    setBusy(`row:${c.id}`)
    setRowResult((r) => ({ ...r, [c.id]: { ok: true, message: 'Sending a real clip prompt…' } }))
    try {
      const r = await api.post<Result>('/api/connectors/test', { id: c.id })
      setRowResult((all) => ({ ...all, [c.id]: r }))
    } catch (e) {
      setRowResult((all) => ({ ...all, [c.id]: { ok: false, message: errorText(e) } }))
    } finally {
      setBusy('')
    }
  }

  if (!data) return <p className="muted">Loading…</p>
  const { connectors, active, presets, recommended } = data
  const preset = presets.find((p) => p.id === draft?.preset)
  const saved = connectors.find((c) => c.id === draft?.id)
  return (
    <div className="connectors">
      <details className="reco" open={connectors.length === 0}>
        <summary>Which model should I use?</summary>
        <p>{recommended.intro}</p>
        <dl>
          {recommended.picks.map((p) => <div key={p.label}><dt>{p.label}</dt><dd>{p.detail}</dd></div>)}
        </dl>
        <ul>
          {recommended.rules.map((r) => <li key={r}>{r}</li>)}
        </ul>
      </details>

      {connectors.length === 0 && !draft && <p className="muted">No connector yet, so Surge writes with its built-in writer. Add one for sharper hooks, titles and edits.</p>}

      {connectors.map((c) => (
        <div key={c.id} className={`connector${c.id === active ? ' active' : ''}`}>
          <div className="who">
            <b>{c.name}{c.id === active && <span className="badge-on">In use</span>}</b>
            <span>{c.protocol === 'anthropic' ? 'Anthropic API' : 'OpenAI-compatible'} · {hostOf(c.base_url)} · {c.has_key ? `key ${c.key_hint || 'saved'}` : 'no key'}</span>
            <span>Clips: <code>{c.model}</code> · Ranking: <code>{c.rank_model}</code></span>
          </div>
          <div className="acts">
            {c.id !== active && <button className="btn sm primary" disabled={!!busy} onClick={() => void use(c)}>Use this</button>}
            <button className="btn sm" disabled={!!busy} onClick={() => void testRow(c)}>{busy === `row:${c.id}` ? 'Testing…' : 'Test'}</button>
            <button className="btn sm ghost" disabled={!!busy} onClick={() => edit(c)}>Edit</button>
            <button className="btn sm ghost" disabled={!!busy} onClick={() => void remove(c)}>Remove</button>
          </div>
          {rowResult[c.id] && <p className={`test-line ${rowResult[c.id].ok ? 'ok' : 'bad'} result`} role="status">{rowResult[c.id].message}</p>}
        </div>
      ))}

      {draft ? (
        <form className="connector-form" onSubmit={(e) => { e.preventDefault(); void save() }}>
          <h3>{draft.id ? `Edit ${draft.name}` : 'Add a connector'}</h3>
          {!draft.id && (
            <Field label="Service" htmlFor="cn-preset" hint={preset?.note}>
              <select id="cn-preset" className="select" value={draft.preset} onChange={(e) => pickPreset(e.target.value)}>
                {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
          )}
          <div className="row2">
            <Field label="Name" htmlFor="cn-name"><input id="cn-name" className="input" value={draft.name} onChange={(e) => set({ name: e.target.value })} /></Field>
            <div className="field">
              <span className="label">Speaks</span>
              <Seg label="Protocol" value={draft.protocol} onChange={(v) => set({ protocol: v })} options={[{ value: 'openai', label: 'OpenAI-compatible' }, { value: 'anthropic', label: 'Anthropic' }]} />
            </div>
          </div>
          <Field label="Base URL" htmlFor="cn-url" hint="Where the service lives, for example https://api.openai.com/v1. Use https for anything on the internet.">
            <input id="cn-url" className="input" value={draft.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder="https://api.example.com/v1" spellCheck={false} />
          </Field>
          <Field label="API key" htmlFor="cn-key" hint="Stored encrypted on this PC and never shown again.">
            <input id="cn-key" className="input" type="password" autoComplete="off" value={draft.api_key} onChange={(e) => set({ api_key: e.target.value })}
              placeholder={saved?.has_key ? 'Key saved. Paste a new one to replace it' : preset?.key_url === null ? 'Optional for a server on your own PC' : 'Paste your key'} />
          </Field>
          {preset?.key_url && <a className="ext" href={preset.key_url} target="_blank" rel="noreferrer">Get a key from {preset.name}<ExternalLink size={12} /></a>}
          <div className="row2">
            <Field label="Model for each clip" htmlFor="cn-model" hint="Fast and cheap. Runs once per clip.">
              <input id="cn-model" className="input" list="cn-models" value={draft.model} onChange={(e) => set({ model: e.target.value })} spellCheck={false} />
            </Field>
            <Field label="Model for ranking a broadcast" htmlFor="cn-rank" hint="Stronger is better. Runs once per broadcast. Empty means the same model.">
              <input id="cn-rank" className="input" list="cn-models" value={draft.rank_model} onChange={(e) => set({ rank_model: e.target.value })} spellCheck={false} />
            </Field>
          </div>
          <datalist id="cn-models">
            {models.map((m) => <option key={m} value={m} />)}
          </datalist>
          <div className="acts">
            <button type="button" className="btn" disabled={!!busy || !draft.base_url.trim()} onClick={() => void fetchModels()}>{busy === 'models' ? 'Fetching…' : 'Fetch models'}</button>
            <button type="button" className="btn" disabled={!!busy || !draft.base_url.trim() || !draft.model.trim()} onClick={() => void testDraft()}>{busy === 'test' ? 'Testing…' : 'Test'}</button>
            <button type="submit" className="btn primary" disabled={!!busy}>Save</button>
            <button type="button" className="btn ghost" disabled={!!busy} onClick={() => { setDraft(null); setFormResult(null) }}>Cancel</button>
          </div>
          {formResult && <p className={`test-line ${formResult.ok ? 'ok' : 'bad'}`} role="status">{formResult.message}</p>}
        </form>
      ) : (
        <div><button className="btn" onClick={openNew}><Plus size={15} />Add connector</button></div>
      )}
    </div>
  )
}
