import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import { errorText, load, openUpdate, setState, settingsUnsaved, toast, useStore } from '../lib/store'
import type { Settings as S, UpdateInfo } from '../lib/types'
import { AiConnectors } from '../components/AiConnectors'
import { Field, Range, Seg, Toggle } from '../components/ui'

const SECTIONS = [
  ['clipping', 'Clipping'], ['look', 'Captions and look'], ['edit', 'Sound and effects'], ['ai', 'AI writer'], ['distribution', 'Distribution'],
  ['platforms', 'Platforms'], ['engine', 'Engine'],
] as const

const SAMPLE: Record<string, { font: string; hi: string; text: [string, string] }> = {
  punch: { font: '900 15px var(--font)', hi: '#FFE500', text: ['NO WAY', 'BRO'] },
  neon: { font: '900 15px var(--font)', hi: '#3BFF6E', text: ['HE DID', 'IT'] },
  comic: { font: '800 16px var(--font)', hi: '#FF5A1F', text: ['RUN', 'NOW'] },
  tall: { font: '800 17px var(--font)', hi: '#FFE500', text: ['WAIT', 'WHAT'] },
  clean: { font: '700 14px var(--font)', hi: '#FFFFFF', text: ['That was', 'close'] },
}

export function Settings() {
  const saved = useStore((s) => s.settings)
  const styles = useStore((s) => s.styles)
  const hasSecret = useStore((s) => s.hasGoogleSecret)
  const encoder = useStore((s) => s.encoder)
  const update = useStore((s) => s.update)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const draft = useStore((s) => s.settingsDraft)
  const dirty = useStore((s) => settingsUnsaved(s))
  const file = useRef<HTMLInputElement>(null)
  const d: S | null = draft ?? saved

  const save = async () => {
    if (!d || saving) return
    setSaving(true)
    try {
      const next = await api.put<S>('/api/settings', d)
      setState({ settings: next, settingsDraft: null })
      toast('Settings saved', 'good')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setSaving(false)
    }
  }
  const discard = () => setState({ settingsDraft: null })

  useEffect(() => { // Ctrl+S saves while this page is open
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        if (settingsUnsaved()) void save()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!d || !saved) return <p className="muted">Loading settings…</p>

  const set = (k: string) => (v: string | number | boolean) => setState((s) => ({ settingsDraft: { ...(s.settingsDraft ?? saved), [k]: v } }))
  const num = (k: string) => Number(d[k])
  const str = (k: string) => String(d[k] ?? '')
  const changed = Object.keys(d).filter((k) => d[k] !== saved[k]).length

  const checkNow = async () => {
    setChecking(true)
    try {
      const u = await api.post<UpdateInfo>('/api/update/check')
      setState({ update: u })
      if (u.error) toast(u.error, 'bad')
      else toast(u.available ? `Surge ${u.latest} is available` : `You're on the latest version (${u.current})`, 'good')
    } catch (e) {
      toast(errorText(e), 'bad')
    } finally {
      setChecking(false)
    }
  }

  const uploadSecret = async (f: File) => {
    const fd = new FormData()
    fd.append('file', f)
    try {
      await api.post('/api/settings/google-secret', fd)
      toast('Google client added. Connect channels on the Accounts page.', 'good')
      void load.state()
    } catch (e) {
      toast(errorText(e), 'bad')
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Changes apply to new clips and posts. Clips already made keep their look until you re-render them.</p>
        </div>
        <div className="actions">
          <button className="btn" disabled={!dirty || saving} onClick={discard}>Discard</button>
          <button className="btn primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </div>
      <div className="settings">
        <nav aria-label="Settings sections">
          {SECTIONS.map(([id, label]) => <a key={id} href={`#set-${id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`set-${id}`)?.scrollIntoView({ behavior: 'smooth' }) }}>{label}</a>)}
        </nav>
        <div>
          <section id="set-clipping">
            <header><h2>Clipping</h2><p>How eagerly Surge cuts. Lower sensitivity means more clips; the target pulls it up or down on its own to hit your pace.</p></header>
            <div className="grid2">
              <Field label="Sensitivity" hint="Excitement level that triggers a cut" htmlFor="sens">
                <Range id="sens" value={num('sensitivity')} min={1} max={5} step={0.1} onChange={set('sensitivity')} />
              </Field>
              <Field label="Target clips per hour, per live stream" hint="45 an hour fills 20 to 30 clips every 20 minutes across two streams" htmlFor="tph">
                <Range id="tph" value={num('target_per_hour')} min={5} max={120} onChange={set('target_per_hour')} />
              </Field>
              <Field label="Clips from a past broadcast" htmlFor="vt"><Range id="vt" value={num('vod_target')} min={3} max={60} onChange={set('vod_target')} /></Field>
              <Field label="Gap between clips" hint="Seconds before the same stream can trigger again" htmlFor="cd"><Range id="cd" value={num('cooldown')} min={10} max={180} onChange={set('cooldown')} suffix="s" /></Field>
              <Field label="Shortest clip" htmlFor="cmin"><Range id="cmin" value={num('clip_min')} min={5} max={60} onChange={set('clip_min')} suffix="s" /></Field>
              <Field label="Longest clip" htmlFor="cmax"><Range id="cmax" value={num('clip_max')} min={15} max={180} onChange={set('clip_max')} suffix="s" /></Field>
              <Field label="Setup before the peak" htmlFor="pre"><Range id="pre" value={num('pre_roll')} min={4} max={60} onChange={set('pre_roll')} suffix="s" /></Field>
              <Field label="Reaction after the peak" htmlFor="post"><Range id="post" value={num('post_roll')} min={2} max={30} onChange={set('post_roll')} suffix="s" /></Field>
              <Field label="Chat delay" hint="How far chat trails the stream" htmlFor="lag"><Range id="lag" value={num('chat_lag')} min={0} max={15} step={0.5} onChange={set('chat_lag')} suffix="s" /></Field>
            </div>
          </section>

          <section id="set-look">
            <header><h2>Captions and look</h2><p>The GTA clip standard is a split layout: facecam on top, gameplay below, big word captions, a hook over the seam.</p></header>
            <div className="field">
              <span className="label">Caption style</span>
              <div className="style-swatches">
                {Object.entries(styles).map(([k, label]) => {
                  const s = SAMPLE[k] ?? SAMPLE.punch
                  return (
                    <button key={k} type="button" className="swatch" aria-pressed={d.caption_style === k} onClick={() => set('caption_style')(k)}>
                      <span className="s" style={{ font: s.font, textTransform: k === 'clean' ? 'none' : 'uppercase' }}>{s.text[0]} <em style={{ color: s.hi }}>{s.text[1]}</em></span>
                      <span className="l">{label}</span>
                    </button>
                  )
                })}
              </div>
            </div>
            <div className="grid2">
              <div className="field"><span className="label">Default layout</span>
                <Seg label="Default layout" value={str('layout')} onChange={set('layout')} options={[{ value: 'auto', label: 'Auto' }, { value: 'split', label: 'Split' }, { value: 'full', label: 'Full' }, { value: 'blur', label: 'Blur' }, { value: 'pip', label: 'PiP' }]} />
              </div>
              <div className="field"><span className="label">Swearing</span>
                <Seg label="Swearing" value={str('profanity')} onChange={set('profanity')} options={[{ value: 'mask', label: 'Mask in captions' }, { value: 'bleep', label: 'Mask and bleep' }, { value: 'off', label: 'Leave as is' }]} />
              </div>
              <Field label="Words per caption" htmlFor="cw"><Range id="cw" value={num('caption_words')} min={1} max={6} onChange={set('caption_words')} /></Field>
              <Field label="Hook on screen for" htmlFor="hs"><Range id="hs" value={num('hook_seconds')} min={0} max={8} step={0.5} onChange={set('hook_seconds')} suffix="s" /></Field>
            </div>
            <div className="grid2">
              <Field label="Caption accuracy" hint="Large turbo is the most accurate; small is fastest">
                <select className="select" value={str('whisper_final_model')} onChange={(e) => set('whisper_final_model')(e.target.value)}>
                  <option value="large-v3-turbo">Large turbo (best)</option>
                  <option value="distil-large-v3">Distil large</option>
                  <option value="medium.en">Medium, English</option>
                  <option value="small.en">Small, English (fastest)</option>
                </select>
              </Field>
              <Field label="Live listening model" hint="Feeds the voice signal and live transcript">
                <select className="select" value={str('whisper_live_model')} onChange={(e) => set('whisper_live_model')(e.target.value)}>
                  <option value="base.en">Base, English</option>
                  <option value="small.en">Small, English</option>
                  <option value="tiny.en">Tiny, English</option>
                </select>
              </Field>
            </div>
            <Field label="Words to listen for" hint="Names and slang Whisper should spell right: streamers, places, crews" htmlFor="hw">
              <input id="hw" className="input" value={str('hotwords')} onChange={(e) => set('hotwords')(e.target.value)} />
            </Field>
          </section>

          <section id="set-edit">
            <header><h2>Sound and effects</h2><p>The meme edit layer: sounds on the punchline, punch-in zooms, shakes, flashes, emoji stickers and music whose drop lands on the moment. The AI writer places them when a connector is set; otherwise Surge places them from the chat reaction and the peak.</p></header>
            <div className="field"><span className="label">How much editing</span>
              <Seg label="How much editing" value={str('fx_level')} onChange={set('fx_level')} options={[{ value: 'off', label: 'Off' }, { value: 'subtle', label: 'Subtle' }, { value: 'medium', label: 'Medium' }, { value: 'max', label: 'Max' }]} />
            </div>
            <div className="grid2">
              <Field label="Clips that get meme sounds" hint="The rest keep only the visual edit so the sounds don't wear out. Sounds also rotate and never repeat back to back" htmlFor="ratio">
                <Range id="ratio" value={num('sfx_ratio')} min={0} max={100} step={5} onChange={set('sfx_ratio')} suffix="%" />
              </Field>
              <div className="field"><span className="label">Music</span>
                <Seg label="Music" value={str('music_mode')} onChange={set('music_mode')} options={[{ value: 'off', label: 'Off' }, { value: 'fits', label: 'When it fits' }, { value: 'always', label: 'Always' }]} />
                <span className="hint">When it fits: chases, fails and goofy moments.</span>
              </div>
              <Field label="Meme sound volume" htmlFor="sfxv"><Range id="sfxv" value={num('sfx_volume')} min={-15} max={3} onChange={set('sfx_volume')} suffix=" dB" /></Field>
              <Field label="Music volume under the voice" htmlFor="mv"><Range id="mv" value={num('music_volume')} min={-35} max={-10} onChange={set('music_volume')} suffix=" dB" /></Field>
            </div>
            <Toggle checked={!!d.stickers} onChange={set('stickers')} label="Emoji stickers on reactions" />
          </section>

          <section id="set-ai">
            <header><h2>AI writer</h2><p>The AI picks the best moments, trims dead air and writes hooks, titles and a different angle for each account. Connect Anthropic, or any service that speaks the OpenAI format, with just a base URL and an API key. Without a connector, Surge writes copy from the transcript and chat on its own. Connectors save by themselves: the save bar at the bottom is for the other settings.</p></header>
            <AiConnectors />
          </section>


          <section id="set-distribution">
            <header><h2>Distribution</h2><p>With autopilot on, clips that score high enough are scheduled without you, spread across matching accounts.</p></header>
            <Toggle checked={!!d.autopilot} onChange={set('autopilot')} label="Autopilot: schedule strong clips automatically" />
            <div className="grid2">
              <Field label="Score needed for autopilot" htmlFor="aas"><Range id="aas" value={num('auto_approve_score')} min={5} max={10} step={0.5} onChange={set('auto_approve_score')} /></Field>
              <Field label="Accounts per clip" hint="Each gets its own title and hashtag mix" htmlFor="mapc"><Range id="mapc" value={num('max_accounts_per_clip')} min={1} max={10} onChange={set('max_accounts_per_clip')} /></Field>
              <Field label="Minimum gap between posts on one account" hint="For set posting times. Spread through the day spaces posts to fit the day's count" htmlFor="gap"><Range id="gap" value={num('min_gap_minutes')} min={15} max={360} step={5} onChange={set('min_gap_minutes')} suffix="m" /></Field>
              <Field label="Shift each slot by up to" hint="Posts don't all land on the exact minute" htmlFor="jit"><Range id="jit" value={num('slot_jitter_minutes')} min={0} max={30} onChange={set('slot_jitter_minutes')} suffix="m" /></Field>
            </div>
            <Field label="Hashtags every post must carry" hint="From your clipping campaign rules, separated by spaces" htmlFor="ch"><input id="ch" className="input" value={str('campaign_hashtags')} onChange={(e) => set('campaign_hashtags')(e.target.value)} placeholder="#gta6 #clipcampaign" /></Field>
            <Field label="Accounts every post must mention" htmlFor="cm"><input id="cm" className="input" value={str('campaign_mentions')} onChange={(e) => set('campaign_mentions')(e.target.value)} placeholder="@streamer" /></Field>
            <Field label="Never post a clip containing" hint="Words checked in titles, descriptions and what's said, separated by commas" htmlFor="bw"><input id="bw" className="input" value={str('banned_words')} onChange={(e) => set('banned_words')(e.target.value)} /></Field>
          </section>

          <section id="set-platforms">
            <header><h2>Platforms</h2><p>Surge posts through the official YouTube and Instagram APIs using your own developer apps, so the accounts stay yours and nothing is shared.</p></header>
            <div className="guide">
              <b>YouTube</b>
              <ol>
                <li>In Google Cloud Console, create a project and enable <b>YouTube Data API v3</b>.</li>
                <li>Set up the OAuth consent screen, add your Google accounts as test users, then create an OAuth client of type <b>Desktop app</b> and download its JSON file.</li>
                <li>Add that file below. Until Google approves the project in its free API audit, YouTube keeps every upload private; the audit form is in the YouTube API Services documentation.</li>
              </ol>
            </div>
            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <input ref={file} type="file" accept="application/json,.json" hidden onChange={(e) => e.target.files?.[0] && uploadSecret(e.target.files[0])} />
              <button className="btn" onClick={() => file.current?.click()}>{hasSecret ? 'Replace Google client file' : 'Add Google client file'}</button>
              <span className="status"><span className={`dot ${hasSecret ? 'on' : ''}`} />{hasSecret ? 'Client file added' : 'No client file yet'}</span>
            </div>
            <div className="guide">
              <b>Instagram</b>
              <ol>
                <li>Create a Meta app with the <b>Instagram API with Instagram login</b> product. Accounts must be Business or Creator.</li>
                <li>Add the redirect address below to the app's valid OAuth redirect URIs, or skip sign-in and paste access tokens instead.</li>
                <li>Until Meta reviews the app, only accounts added as testers in the app can post.</li>
              </ol>
            </div>
            <div className="grid2">
              <Field label="Meta app ID" htmlFor="mai"><input id="mai" className="input" value={str('meta_app_id')} onChange={(e) => set('meta_app_id')(e.target.value)} /></Field>
              <Field label="Meta app secret" htmlFor="mas" hint="Stored encrypted on this PC"><input id="mas" className="input" type="password" value={str('meta_app_secret')} onChange={(e) => set('meta_app_secret')(e.target.value)} autoComplete="off" /></Field>
              <Field label="Redirect address" htmlFor="mru"><input id="mru" className="input" value={str('meta_redirect_uri')} onChange={(e) => set('meta_redirect_uri')(e.target.value)} /></Field>
              <Field label="Graph API version" htmlFor="gv"><input id="gv" className="input" value={str('ig_graph_version')} onChange={(e) => set('ig_graph_version')(e.target.value)} /></Field>
            </div>
          </section>

          <section id="set-engine">
            <header><h2>Engine</h2><p>Video encoder in use: <b>{encoder || 'detecting'}</b>. Parallel workers share the CPU between transcribing and rendering.</p></header>
            <div className="grid2">
              <Field label="Clips processed at once" hint="Takes effect after restarting Surge" htmlFor="rw"><Range id="rw" value={num('render_workers')} min={1} max={8} onChange={set('render_workers')} /></Field>
              <Field label="Frame rate" htmlFor="fps">
                <Seg label="Frame rate" value={String(d.fps)} onChange={(v) => set('fps')(Number(v))} options={[{ value: '30', label: '30 fps' }, { value: '60', label: '60 fps' }]} />
              </Field>
            </div>
            <div className="field">
              <span className="label">Updates</span>
              <Toggle checked={!!d.check_updates} onChange={set('check_updates')} label="Look for a newer version when Surge starts" />
              <div className="update-row">
                <button className="btn sm" disabled={checking} onClick={checkNow}>{checking ? 'Checking…' : 'Check now'}</button>
                {update?.available && <button className="btn sm primary" onClick={() => void openUpdate()}>Download {update.latest}</button>}
                <span className="hint">
                  Version {update?.current ?? '…'}{update?.available ? `, ${update.latest} is available` : ''}. Surge only asks GitHub for the latest release; your clips, accounts and settings never leave this PC.
                </span>
              </div>
            </div>
          </section>
        </div>
      </div>
      <div className={`save-bar${dirty ? ' dirty' : ''}`} role="region" aria-label="Save settings">
        <span role="status">{dirty ? `${changed} unsaved change${changed === 1 ? '' : 's'}` : 'All settings saved'}</span>
        <span className="faint kbd-hint">Ctrl+S</span>
        <button className="btn" disabled={!dirty || saving} onClick={discard}>Discard</button>
        <button className="btn primary" disabled={!dirty || saving} onClick={() => void save()}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    </>
  )
}
