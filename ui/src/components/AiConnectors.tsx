import { useEffect, useState } from 'react'
import { ExternalLink, Plus } from 'lucide-react'
import { api } from '../lib/api'
import { errorText, toast } from '../lib/store'
import type { Connector, ConnectorPreset, ConnectorsData, ModelInfo, ModelListing, ModelPicks, ModelRole, PickResult } from '../lib/types'
import { confirmDanger, Field, Seg } from './ui'

type Result = { ok: boolean; message: string; tried?: PickResult['tried'] }
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
const ROLE_LABEL: Record<ModelRole, string> = { clip: 'Writing each clip', rank: 'Ranking a broadcast' }
const note = (m: ModelInfo, role: ModelRole) => (role === 'clip' ? m.clip_note : m.rank_note)

function PickLine({ label, m, role, active, busy, onUse }: {
  label: string; m: ModelInfo; role: ModelRole; active?: Connector; busy: string; onUse: (m: ModelInfo, role: ModelRole) => void
}) {
  const inUse = active && active.id === m.connector && (role === 'clip' ? active.model : active.rank_model) === m.id
  return (
    <div className="mp-line">
      <span className="mp-label">{label}</span>
      <span className="mp-model"><code>{m.id}</code><span className="faint"> via {m.connector_name}</span>{m.checked?.[role] && <span className="mt-ok"> tested</span>}</span>
      <span className="mp-cost">{note(m, role)}</span>
      {inUse ? <span className="badge-on">In use</span>
        : <button type="button" className="btn sm" disabled={!!busy} onClick={() => onUse(m, role)}>{busy === `${role}:${m.connector}:${m.id}` ? 'Testing…' : 'Use'}</button>}
    </div>
  )
}

/** The best and the cheapest model for each job across every saved key. Use tests a model before switching to it. */
function ModelPicksCard({ version, active, onUsed }: { version: number; active?: Connector; onUsed: (d: ConnectorsData) => void }) {
  const [data, setData] = useState<ModelPicks | null>(null)
  const [busy, setBusy] = useState('')
  useEffect(() => {
    let live = true
    setData(null)
    api.get<ModelPicks>('/api/connectors/picks')
      .then((d) => { if (live) setData(d) })
      .catch((e) => { if (live) setData({ picks: { clip: { best: null, cheapest: null }, rank: { best: null, cheapest: null } }, errors: [{ connector: '', name: 'Model picks', error: errorText(e) }] }) })
    return () => { live = false }
  }, [version])
  const use = async (m: ModelInfo, role: ModelRole) => {
    if (!m.connector) return
    setBusy(`${role}:${m.connector}:${m.id}`)
    try {
      const r = await api.post<ConnectorsData & { message: string }>(`/api/connectors/${encodeURIComponent(m.connector)}/use`, { role, model: m.id })
      onUsed(r)
      toast(`${m.id} now ${role === 'clip' ? 'writes each clip' : 'ranks broadcasts'}`, 'good')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setBusy('')
    }
  }
  return (
    <section className="model-picks" aria-label="Best and cheapest models">
      <h3>Best and cheapest models across your keys</h3>
      {!data ? <p className="muted">Comparing what your services offer…</p> : (
        <>
          <div className="mp-grid">
            {(['clip', 'rank'] as ModelRole[]).map((role) => {
              const { best, cheapest } = data.picks[role]
              const same = !!best && !!cheapest && best.id === cheapest.id && best.connector === cheapest.connector
              return (
                <div key={role} className="mp-role">
                  <b>{ROLE_LABEL[role]}</b>
                  {best ? <PickLine label={same ? 'Best and cheapest' : 'Best'} m={best} role={role} active={active} busy={busy} onUse={(m, r) => void use(m, r)} />
                    : <span className="faint">No model found on your keys.</span>}
                  {cheapest && !same && <PickLine label="Cheapest" m={cheapest} role={role} active={active} busy={busy} onUse={(m, r) => void use(m, r)} />}
                </div>
              )
            })}
          </div>
          {data.errors.map((e) => <p key={e.connector || e.name} className="test-line bad">{e.name}: {e.error}</p>)}
          <p className="faint mp-foot">Prices come from OpenCode's and OpenRouter's public model lists and Google's pricing page. Use tests a model with a real clip prompt before switching to it.</p>
        </>
      )}
    </section>
  )
}

/** What a service offers after Fetch models or Pick for me: recommended ones first, with what each costs Surge. */
function ModelTable({ listing, draft, onPick }: { listing: ModelListing; draft: Draft; onPick: (role: ModelRole, id: string) => void }) {
  const [all, setAll] = useState(false)
  const rows = all ? listing.details : listing.details.slice(0, 10)
  const tags = (m: ModelInfo) => [
    listing.best.clip === m.id && 'best for clips',
    listing.cheapest.clip === m.id && 'cheapest for clips',
    listing.best.rank === m.id && 'best for ranking',
    listing.cheapest.rank === m.id && 'cheapest for ranking',
    m.checked?.clip === true && 'works for clips',
    m.checked?.rank === true && 'works for ranking',
  ].filter((t): t is string => !!t)
  const failed = (m: ModelInfo) => (['clip', 'rank'] as ModelRole[]).filter((r) => m.checked?.[r] === false)
  if (!listing.details.length) return null
  return (
    <div className="model-table">
      <table className="table">
        <thead><tr><th>Model</th><th>Each clip</th><th>Ranking</th><th><span className="sr-only">Use for</span></th></tr></thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>
                <code>{m.id}</code>
                {tags(m).length > 0 && <span className="mt-tags">{tags(m).join(' · ')}</span>}
                {failed(m).length > 0 && <span className="mt-tags bad">failed Surge's test for {failed(m).map((r) => (r === 'clip' ? 'clips' : 'ranking')).join(' and ')} on this key</span>}
              </td>
              <td>{m.clip_note}</td>
              <td>{m.rank_note}</td>
              <td className="mt-acts">
                <button type="button" className="btn sm ghost" aria-pressed={draft.model === m.id} onClick={() => onPick('clip', m.id)}>Clips</button>
                <button type="button" className="btn sm ghost" aria-pressed={draft.rank_model === m.id} onClick={() => onPick('rank', m.id)}>Ranking</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {listing.details.length > 10 && <button type="button" className="link-btn" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${listing.details.length} models`}</button>}
    </div>
  )
}

function Tried({ tried }: { tried?: PickResult['tried'] }) {
  if (!tried?.length) return null
  return (
    <ul className="tried">
      {tried.map((t) => <li key={`${t.role}:${t.model}`} className={t.ok ? 'ok' : 'bad'}><code>{t.model}</code> for {t.role === 'clip' ? 'clips' : 'ranking'}: {t.ok ? 'works' : t.message}</li>)}
    </ul>
  )
}

/** Add, test and pick the AI service behind the writer: Anthropic, or any OpenAI-compatible service by base URL + key. */
export function AiConnectors({ onChange }: { onChange?: () => void }) {
  const [data, setData] = useState<ConnectorsData | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [listing, setListing] = useState<ModelListing | null>(null)
  const [busy, setBusy] = useState('')
  const [formResult, setFormResult] = useState<Result | null>(null)
  const [rowResult, setRowResult] = useState<Record<string, Result>>({})
  const [version, setVersion] = useState(0)

  useEffect(() => {
    api.get<ConnectorsData>('/api/connectors').then(setData).catch((e) => toast(errorText(e), 'bad'))
  }, [])

  const apply = (d: ConnectorsData) => {
    setData(d)
    setVersion((v) => v + 1)
    onChange?.()
  }
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d))
  const reset = (d: Draft | null) => {
    setDraft(d)
    setListing(null)
    setFormResult(null)
  }

  const openNew = () => {
    const p = data?.presets[0]
    if (p) reset(fromPreset(p))
  }
  const pickPreset = (id: string) => {
    const p = data?.presets.find((x) => x.id === id)
    if (p) reset({ ...fromPreset(p), api_key: draft?.api_key ?? '' })
  }
  const edit = (c: Connector) => reset({ id: c.id, preset: '', name: c.name, protocol: c.protocol, base_url: c.base_url, api_key: '', model: c.model, rank_model: c.rank_model })

  const fetchModels = async () => {
    if (!draft) return
    setBusy('models')
    setFormResult(null)
    try {
      const r = await api.post<ModelListing>('/api/connectors/models', payload(draft))
      setListing(r)
      setFormResult({ ok: r.details.length > 0, message: r.details.length ? `${r.details.length} models Surge can use, recommended ones first.` : 'The service answered but listed no chat models Surge can use.' })
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
    } finally {
      setBusy('')
    }
  }
  /** Test the best candidates on this key; fills the model fields with the first that work. */
  const pick = async (): Promise<PickResult | null> => {
    if (!draft) return null
    setBusy('pick')
    setFormResult({ ok: true, message: 'Testing the best models on this key with a real clip prompt…' })
    try {
      const r = await api.post<PickResult & { listing?: ModelListing }>('/api/connectors/pick', payload(draft))
      if (r.listing) setListing(r.listing)
      if (r.ok && r.model) set({ model: r.model, rank_model: r.rank_model || r.model })
      setFormResult({ ok: r.ok, message: r.message, tried: r.tried })
      return r
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
      return null
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
  const save = async (models?: { model: string; rank_model: string }) => {
    if (!draft) return
    setBusy('save')
    try {
      apply(await api.post<ConnectorsData>('/api/connectors', { ...payload(draft), ...models }))
      reset(null)
      toast(models ? `${draft.name} connected: ${models.model} writes clips, ${models.rank_model} ranks` : 'Connector saved', 'good')
    } catch (e) {
      setFormResult({ ok: false, message: errorText(e) })
    } finally {
      setBusy('')
    }
  }
  /** Key-only services: pick and test the models, then save. */
  const connect = async () => {
    const r = await pick()
    if (r?.ok && r.model) await save({ model: r.model, rank_model: r.rank_model || r.model })
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
  const quick = !!preset?.quick && !draft?.id

  const addressFields = draft && (
    <>
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
    </>
  )
  const keyField = draft && (
    <>
      <Field label="API key" htmlFor="cn-key" hint="Stored encrypted on this PC and never shown again.">
        <input id="cn-key" className="input" type="password" autoComplete="off" value={draft.api_key} onChange={(e) => set({ api_key: e.target.value })}
          placeholder={saved?.has_key ? 'Key saved. Paste a new one to replace it' : preset?.key_url === null ? 'Optional for a server on your own PC' : 'Paste your key'} />
      </Field>
      {preset?.key_url && <a className="ext" href={preset.key_url} target="_blank" rel="noreferrer">Get a key from {preset.name}<ExternalLink size={12} /></a>}
    </>
  )
  const modelFields = draft && (
    <>
      <div className="row2">
        <Field label="Model for each clip" htmlFor="cn-model" hint="Fast and cheap. Runs once per clip.">
          <input id="cn-model" className="input" list="cn-models" value={draft.model} onChange={(e) => set({ model: e.target.value })} spellCheck={false} />
        </Field>
        <Field label="Model for ranking a broadcast" htmlFor="cn-rank" hint="Stronger is better. Runs once per broadcast. Empty means the same model.">
          <input id="cn-rank" className="input" list="cn-models" value={draft.rank_model} onChange={(e) => set({ rank_model: e.target.value })} spellCheck={false} />
        </Field>
      </div>
      <datalist id="cn-models">
        {(listing?.models ?? []).map((m) => <option key={m} value={m} />)}
      </datalist>
      <div className="acts">
        <button type="button" className="btn" disabled={!!busy || !draft.base_url.trim()} onClick={() => void fetchModels()}>{busy === 'models' ? 'Fetching…' : 'Fetch models'}</button>
        <button type="button" className="btn" disabled={!!busy || !draft.base_url.trim()} onClick={() => void pick()}>{busy === 'pick' ? 'Testing models…' : 'Pick for me'}</button>
        <button type="button" className="btn" disabled={!!busy || !draft.base_url.trim() || !draft.model.trim()} onClick={() => void testDraft()}>{busy === 'test' ? 'Testing…' : 'Test'}</button>
        {quick && <button type="button" className="btn" disabled={!!busy || !draft.model.trim()} onClick={() => void save()}>Save these models</button>}
      </div>
    </>
  )
  const resultLine = formResult && (
    <div role="status">
      <p className={`test-line ${formResult.ok ? 'ok' : 'bad'}`}>{formResult.message}</p>
      <Tried tried={formResult.tried} />
    </div>
  )

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

      {connectors.length > 0 && <ModelPicksCard version={version} active={connectors.find((c) => c.id === active)} onUsed={apply} />}

      {connectors.length === 0 && !draft && <p className="muted">No connector yet, so Surge writes with its built-in writer. Add one for sharper hooks, titles and edits: a free Google Gemini key is enough.</p>}

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
        <form className="connector-form" onSubmit={(e) => { e.preventDefault(); void (quick ? connect() : save()) }}>
          <h3>{draft.id ? `Edit ${draft.name}` : 'Add a connector'}</h3>
          {!draft.id && (
            <Field label="Service" htmlFor="cn-preset" hint={preset?.note}>
              <select id="cn-preset" className="select" value={draft.preset} onChange={(e) => pickPreset(e.target.value)}>
                {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
            </Field>
          )}
          {quick ? (
            <>
              {keyField}
              <div className="acts">
                <button type="submit" className="btn primary" disabled={!!busy || !draft.api_key.trim()}>{busy === 'pick' ? 'Picking and testing models…' : busy === 'save' ? 'Saving…' : 'Connect'}</button>
                <button type="button" className="btn ghost" disabled={!!busy} onClick={() => reset(null)}>Cancel</button>
              </div>
              <p className="faint quick-hint">Connect tests the best models on your key with a real clip prompt and keeps the first that works for clips and for ranking.</p>
              {resultLine}
              <details className="advanced">
                <summary>Choose the models yourself</summary>
                <div className="advanced-body">{addressFields}{modelFields}</div>
              </details>
            </>
          ) : (
            <>
              {addressFields}
              {keyField}
              {modelFields}
              <div className="acts">
                <button type="submit" className="btn primary" disabled={!!busy}>Save</button>
                <button type="button" className="btn ghost" disabled={!!busy} onClick={() => reset(null)}>Cancel</button>
              </div>
              {resultLine}
            </>
          )}
          {listing && <ModelTable listing={listing} draft={draft} onPick={(role, id) => set(role === 'clip' ? { model: id } : { rank_model: id })} />}
        </form>
      ) : (
        <div><button className="btn" onClick={openNew}><Plus size={15} />Add connector</button></div>
      )}
    </div>
  )
}
