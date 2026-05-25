import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { backend, ApiError } from './api'
import './App.css'

// ---- wire types (match backend JSON) ----

interface Attendee {
  id: number
  name: string
  email?: string
}

interface AttendanceRow {
  attendee_id: number
  present: boolean
}

interface UpdateWire {
  id?: number
  text: string
  owner_id?: number
}

interface ProjectWire {
  id?: number
  name: string
  leader_id?: number
  updates: UpdateWire[]
}

interface MeetingWire {
  id?: number
  date: string
  title: string
  created_by?: string
  created_at?: string
  updated_at?: string
  attendance: AttendanceRow[]
  projects: ProjectWire[]
}

interface MeetingSummary {
  id: number
  date: string
  title: string
  present_count: number
  total_count: number
}

// ---- local UI state (id strings keep newly-added items renderable) ----

interface LocalUpdate {
  localId: string
  serverId?: number
  text: string
  ownerId?: number
}

interface LocalProject {
  localId: string
  serverId?: number
  name: string
  leaderId?: number
  updates: LocalUpdate[]
}

interface FormState {
  meetingId?: number
  date: string
  title: string
  // attendance is keyed by attendee id (numbers). Map gives O(1) toggles + merging.
  presence: Map<number, boolean>
  projects: LocalProject[]
}

const todayISO = () => new Date().toISOString().slice(0, 10)
const uid = () => Math.random().toString(36).slice(2, 10)

function emptyForm(): FormState {
  return {
    meetingId: undefined,
    date: todayISO(),
    title: '',
    presence: new Map(),
    projects: [],
  }
}

function mergePresence(attendees: Attendee[], rows: AttendanceRow[] | null | undefined): Map<number, boolean> {
  const recorded = new Map((rows ?? []).map(r => [r.attendee_id, r.present]))
  const out = new Map<number, boolean>()
  for (const a of attendees) out.set(a.id, recorded.get(a.id) ?? false)
  return out
}

// Format an arbitrary thrown value into a clear, user-facing string. Backend
// errors surface their `detail` field; network failures get a specific
// message; unexpected JS exceptions are labelled as such so the user can tell
// it's a bug rather than a backend problem.
function describeError(action: string, err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401 || err.status === 403) {
      return `${action}: not authorized (${err.message})`
    }
    if (err.status >= 500) {
      return `${action}: server error — ${err.message}`
    }
    return `${action}: ${err.message}`
  }
  if (err instanceof TypeError) {
    // fetch() throws TypeError on network / CORS failures; .map on null also
    // throws TypeError. Distinguish via message text.
    const msg = err.message
    if (/failed to fetch|network|load failed/i.test(msg)) {
      return `${action}: network error — could not reach the server`
    }
    return `${action}: unexpected client error — ${msg}`
  }
  if (err instanceof Error) return `${action}: ${err.message}`
  return `${action}: ${String(err)}`
}

function projectsFromWire(projs: ProjectWire[] | null | undefined): LocalProject[] {
  if (!projs) return []
  return projs.map(p => ({
    localId: uid(),
    serverId: p.id,
    name: p.name,
    leaderId: p.leader_id,
    updates: (p.updates ?? []).map(u => ({
      localId: uid(),
      serverId: u.id,
      text: u.text,
      ownerId: u.owner_id,
    })),
  }))
}

function formToWire(f: FormState, attendees: Attendee[]): MeetingWire {
  return {
    date: f.date,
    title: f.title,
    attendance: attendees.map(a => ({
      attendee_id: a.id,
      present: f.presence.get(a.id) ?? false,
    })),
    projects: f.projects.map(p => ({
      name: p.name,
      leader_id: p.leaderId,
      updates: p.updates.map(u => ({ text: u.text, owner_id: u.ownerId })),
    })),
  }
}

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    const stored = localStorage.getItem('theme')
    if (stored === 'light' || stored === 'dark') return stored
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  })
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])
  const toggle = () => setTheme(t => (t === 'dark' ? 'light' : 'dark'))
  return { theme, toggle }
}

function App() {
  const { t, i18n } = useTranslation()
  const { theme, toggle: toggleTheme } = useTheme()

  const [attendees, setAttendees] = useState<Attendee[]>([])
  const [meetings, setMeetings] = useState<MeetingSummary[]>([])
  const [form, setForm] = useState<FormState>(emptyForm)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [newAttendeeName, setNewAttendeeName] = useState('')
  const [newAttendeeEmail, setNewAttendeeEmail] = useState('')

  const refreshAttendees = useCallback(async () => {
    const res = await backend.get<{ attendees: Attendee[] }>('/attendees')
    setAttendees(res.attendees)
    return res.attendees
  }, [])

  const refreshMeetings = useCallback(async () => {
    const res = await backend.get<{ meetings: MeetingSummary[] }>('/meetings')
    setMeetings(res.meetings)
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [attList] = await Promise.all([refreshAttendees(), refreshMeetings()])
        if (cancelled) return
        // Start with a fresh meeting, but seed presence from current attendees.
        setForm(f => ({ ...f, presence: mergePresence(attList, []) }))
      } catch (e) {
        setError(describeError('Failed to load attendees and meetings', e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [refreshAttendees, refreshMeetings])

  const loadMeeting = async (id: number) => {
    setError(null)
    try {
      const m = await backend.get<MeetingWire>(`/meetings/${id}`)
      setForm({
        meetingId: m.id,
        date: m.date,
        title: m.title,
        presence: mergePresence(attendees, m.attendance),
        projects: projectsFromWire(m.projects),
      })
    } catch (e) {
      setError(describeError('Failed to load meeting', e))
    }
  }

  const startNewMeeting = () => {
    setForm({
      meetingId: undefined,
      date: todayISO(),
      title: '',
      presence: mergePresence(attendees, []),
      projects: [],
    })
  }

  const togglePresent = (attendeeId: number) => {
    setForm(f => {
      const next = new Map(f.presence)
      next.set(attendeeId, !next.get(attendeeId))
      return { ...f, presence: next }
    })
  }

  const addAttendee = async () => {
    const name = newAttendeeName.trim()
    if (!name) return
    try {
      const created = await backend.post<Attendee>('/attendees', {
        name,
        email: newAttendeeEmail.trim(),
      })
      setAttendees(prev => [...prev, created])
      setForm(f => {
        const next = new Map(f.presence)
        next.set(created.id, true)
        return { ...f, presence: next }
      })
      setNewAttendeeName('')
      setNewAttendeeEmail('')
    } catch (e) {
      setError(describeError('Failed to add attendee', e))
    }
  }

  const updateProject = (localId: string, patch: Partial<LocalProject>) => {
    setForm(f => ({
      ...f,
      projects: f.projects.map(p => (p.localId === localId ? { ...p, ...patch } : p)),
    }))
  }

  const addProject = () => {
    setForm(f => ({
      ...f,
      projects: [...f.projects, { localId: uid(), name: '', updates: [] }],
    }))
  }

  const removeProject = (localId: string) => {
    setForm(f => ({ ...f, projects: f.projects.filter(p => p.localId !== localId) }))
  }

  const addUpdate = (projectLocalId: string) => {
    setForm(f => ({
      ...f,
      projects: f.projects.map(p =>
        p.localId === projectLocalId
          ? { ...p, updates: [...p.updates, { localId: uid(), text: '' }] }
          : p
      ),
    }))
  }

  const updateUpdate = (projectLocalId: string, updateLocalId: string, patch: Partial<LocalUpdate>) => {
    setForm(f => ({
      ...f,
      projects: f.projects.map(p =>
        p.localId === projectLocalId
          ? {
              ...p,
              updates: p.updates.map(u => (u.localId === updateLocalId ? { ...u, ...patch } : u)),
            }
          : p
      ),
    }))
  }

  const removeUpdate = (projectLocalId: string, updateLocalId: string) => {
    setForm(f => ({
      ...f,
      projects: f.projects.map(p =>
        p.localId === projectLocalId
          ? { ...p, updates: p.updates.filter(u => u.localId !== updateLocalId) }
          : p
      ),
    }))
  }

  const saveMeeting = async () => {
    setSaving(true)
    setError(null)
    try {
      const payload = formToWire(form, attendees)
      const saved = form.meetingId
        ? await backend.put<MeetingWire>(`/meetings/${form.meetingId}`, payload)
        : await backend.post<MeetingWire>('/meetings', payload)
      setForm({
        meetingId: saved.id,
        date: saved.date,
        title: saved.title,
        presence: mergePresence(attendees, saved.attendance),
        projects: projectsFromWire(saved.projects),
      })
      await refreshMeetings()
    } catch (e) {
      setError(describeError('Failed to save meeting', e))
    } finally {
      setSaving(false)
    }
  }

  const presentCount = Array.from(form.presence.values()).filter(Boolean).length
  const changeLang = (e: React.ChangeEvent<HTMLSelectElement>) => i18n.changeLanguage(e.target.value)

  if (loading) {
    return <div className="app meeting-app"><p>Loading…</p></div>
  }

  return (
    <div className="app meeting-app">
      <div className="toolbar">
        <select className="lang-select" value={i18n.language} onChange={changeLang}>
          <option value="en">{t('language.en')}</option>
          <option value="cs">{t('language.cs')}</option>
        </select>
        <button onClick={toggleTheme}>{theme === 'dark' ? '☀️' : '🌙'}</button>
      </div>

      <header className="meeting-header">
        <h1>Project Status Meeting</h1>

        <div className="meeting-picker">
          <label>
            Open meeting
            <select
              value={form.meetingId ?? ''}
              onChange={e => {
                const v = e.target.value
                if (!v) startNewMeeting()
                else loadMeeting(Number(v))
              }}
            >
              <option value="">— new meeting —</option>
              {meetings.map(m => (
                <option key={m.id} value={m.id}>
                  {m.date} · {m.title || '(untitled)'} ({m.present_count}/{m.total_count})
                </option>
              ))}
            </select>
          </label>
          <button className="ghost" onClick={startNewMeeting}>+ New meeting</button>
        </div>

        <div className="meeting-meta">
          <label>
            Date
            <input
              type="date"
              value={form.date}
              onChange={e => setForm(f => ({ ...f, date: e.target.value }))}
            />
          </label>
          <label className="grow">
            Title
            <input
              type="text"
              value={form.title}
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
              placeholder="e.g. Weekly status"
            />
          </label>
        </div>

        {error && <p className="error-banner">{error}</p>}
      </header>

      <div className="meeting-grid">
        <section className="card attendees">
          <div className="card-head">
            <h2>Attendees</h2>
            <span className="badge">{presentCount}/{attendees.length}</span>
          </div>

          <ul className="attendee-list">
            {attendees.map((a, idx) => (
              <li key={a.id} className="attendee-row">
                <span className="ordinal">{idx + 1}</span>
                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={form.presence.get(a.id) ?? false}
                    onChange={() => togglePresent(a.id)}
                  />
                  <span className="attendee-name">{a.name}</span>
                </label>
                {a.email && <span className="attendee-email">{a.email}</span>}
              </li>
            ))}
            {attendees.length === 0 && <li className="empty">No attendees yet.</li>}
          </ul>

          <div className="add-row">
            <input
              type="text"
              placeholder="Name"
              value={newAttendeeName}
              onChange={e => setNewAttendeeName(e.target.value)}
            />
            <input
              type="email"
              placeholder="Email (optional)"
              value={newAttendeeEmail}
              onChange={e => setNewAttendeeEmail(e.target.value)}
            />
            <button onClick={addAttendee}>+ Add</button>
          </div>
        </section>

        <section className="card projects">
          <div className="card-head">
            <h2>Projects</h2>
            <button className="ghost" onClick={addProject}>+ Add project</button>
          </div>

          <div className="project-list">
            {form.projects.map(p => (
              <article key={p.localId} className="project">
                <div className="project-head">
                  <input
                    className="project-name"
                    type="text"
                    value={p.name}
                    placeholder="Project name"
                    onChange={e => updateProject(p.localId, { name: e.target.value })}
                  />
                  <select
                    value={p.leaderId ?? ''}
                    onChange={e => updateProject(p.localId, {
                      leaderId: e.target.value ? Number(e.target.value) : undefined,
                    })}
                  >
                    <option value="">— leader —</option>
                    {attendees.map(a => (
                      <option key={a.id} value={a.id}>{a.name}</option>
                    ))}
                  </select>
                  <button className="icon-btn" title="Remove project" onClick={() => removeProject(p.localId)}>×</button>
                </div>

                <ul className="updates">
                  {p.updates.map(u => (
                    <li key={u.localId} className="update-row">
                      <span className="bullet">•</span>
                      <input
                        type="text"
                        value={u.text}
                        placeholder="Update / action item"
                        onChange={e => updateUpdate(p.localId, u.localId, { text: e.target.value })}
                      />
                      <select
                        value={u.ownerId ?? ''}
                        onChange={e => updateUpdate(p.localId, u.localId, {
                          ownerId: e.target.value ? Number(e.target.value) : undefined,
                        })}
                      >
                        <option value="">— owner —</option>
                        {attendees.map(a => (
                          <option key={a.id} value={a.id}>{a.name}</option>
                        ))}
                      </select>
                      <button className="icon-btn" title="Remove" onClick={() => removeUpdate(p.localId, u.localId)}>×</button>
                    </li>
                  ))}
                </ul>
                <button className="ghost small" onClick={() => addUpdate(p.localId)}>+ Add update</button>
              </article>
            ))}
            {form.projects.length === 0 && <p className="empty">No projects yet — click "+ Add project".</p>}
          </div>
        </section>
      </div>

      <div className="meeting-actions">
        <button className="primary" onClick={saveMeeting} disabled={saving}>
          {saving ? 'Saving…' : form.meetingId ? 'Update meeting' : 'Save meeting'}
        </button>
      </div>

      <footer className="powered-by">
        <a href="https://bitswan.ai" target="_blank" rel="noopener noreferrer">
          <img src="/bitswan.svg" alt="BitSwan" className="powered-by-logo" />
          Powered by BitSwan
        </a>
      </footer>
    </div>
  )
}

export default App
