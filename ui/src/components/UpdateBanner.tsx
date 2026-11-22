import { useState } from 'react'
import { CircleArrowUp, X } from 'lucide-react'
import { installUpdate, openUpdate, useStore } from '../lib/store'
import type { UpdateInfo } from '../lib/types'

const KEY = 'surge.update.hidden'
const read = () => {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
const write = (v: string) => {
  try {
    localStorage.setItem(KEY, v)
  } catch {
    /* storage blocked: the banner just comes back next start */
  }
}
const mb = (bytes: number | null | undefined) => (bytes ? `${Math.max(1, Math.round(bytes / 1048576))} MB` : '')

/** What the update buttons do: a small update when the release has one for this version, else the installer. */
export function UpdateActions({ u, compact }: { u: UpdateInfo; compact?: boolean }) {
  const job = u.job ?? { state: 'idle', progress: 0, message: '', error: null }
  const small = u.can_patch && !!u.patch && job.state !== 'failed'
  const working = ['downloading', 'checking', 'restarting'].includes(job.state)
  if (working) {
    const pct = Math.round((job.progress || 0) * 100)
    return (
      <div className="update-progress" role="status" aria-live="polite">
        <span>{job.message}{job.state === 'downloading' ? ` · ${pct}%` : '…'}</span>
        <progress max={100} value={job.state === 'downloading' ? pct : undefined} aria-label="Update progress" />
      </div>
    )
  }
  return (
    <>
      {small && <button className="btn sm primary" onClick={() => void installUpdate()}>Update now{u.patch?.size ? ` (${mb(u.patch.size)})` : ''}</button>}
      <button className={`btn sm${small ? ' ghost' : ' primary'}`} onClick={() => void openUpdate()}>
        {small ? 'Full installer' : compact ? `Download ${u.latest}` : 'Download update'}{u.size ? ` (${mb(u.size)})` : ''}
      </button>
      {job.state === 'failed' && job.error && <span className="update-err" role="alert">{job.error}</span>}
    </>
  )
}

/** Shown when GitHub has a newer release. Hiding it lasts until the version after that one. */
export function UpdateBanner() {
  const u = useStore((s) => s.update)
  const [hidden, setHidden] = useState(read)
  const [notes, setNotes] = useState(false)
  if (!u?.available || !u.latest || hidden === u.latest) return null
  const latest = u.latest
  const dismiss = () => {
    write(latest)
    setHidden(latest)
  }
  const small = u.can_patch && !!u.patch
  return (
    <div className="update-bar" role="status">
      <CircleArrowUp size={20} strokeWidth={1.8} aria-hidden="true" />
      <div className="txt">
        <b>Surge {latest} is available</b>
        <span>
          You have {u.current}. {small ? `Update now downloads only what changed (${mb(u.patch?.size) || 'a few MB'}), restarts Surge and resumes live recordings.` : 'Updating keeps your clips, accounts and settings.'}
        </span>
      </div>
      <button className="btn sm ghost" aria-expanded={notes} onClick={() => setNotes(!notes)}>What's new</button>
      <UpdateActions u={u} />
      <button className="btn sm ghost icon" aria-label={`Hide until the version after ${latest}`} title="Hide until the next version" onClick={dismiss}>
        <X size={16} />
      </button>
      {notes && (
        <div className="notes">
          <pre>{u.notes || 'No release notes.'}</pre>
          {u.sha256 && <p>Installer SHA-256: <code>{u.sha256}</code></p>}
        </div>
      )}
    </div>
  )
}
