import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backend,
  type CurrentMeeting,
  type Demo,
  type MeetingProject,
  type MyEntry,
  type Person,
  type RosterEntry,
} from './api'
import { LeaderPicker } from './LeaderPicker'


function formatMeetingDate(iso: string, locale: string): string {
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(locale, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  })
}


interface MeetingFormProps {
  isAdmin?: boolean
}


export function MeetingForm({ isAdmin }: MeetingFormProps = {}) {
  const { t, i18n } = useTranslation()
  const [meeting, setMeeting] = useState<CurrentMeeting | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selfEmail, setSelfEmail] = useState<string>('')
  const [roster, setRoster] = useState<RosterEntry[]>([])
  const [demos, setDemos] = useState<Demo[]>([])
  const [descriptionDrafts, setDescriptionDrafts] = useState<Record<number, string>>({})

  // Refs that mirror state, so handlers and timers can read the latest
  // values without waiting for re-render.
  const meetingRef = useRef<CurrentMeeting | null>(null)
  const demosRef = useRef<Demo[]>([])
  const draftsRef = useRef<Record<number, string>>({})
  meetingRef.current = meeting
  demosRef.current = demos
  draftsRef.current = descriptionDrafts

  // Autosave plumbing keyed by user_email — a user can have several demos
  // and a single PUT writes their whole set, so we coalesce per user.
  const saveTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const saveInFlight = useRef(new Set<string>())
  const savePending = useRef(new Set<string>())
  const [savingUsers, setSavingUsers] = useState<Set<string>>(new Set())

  const refreshDemosAndRoster = useCallback(async (meetingId: number) => {
    const [demosRes, rosterRes] = await Promise.all([
      backend.get<{ demos: Demo[] }>(`/meeting/${meetingId}/demos`),
      backend.get<{ attendees: RosterEntry[] }>(`/meeting/${meetingId}/attendees`),
    ])
    setDemos(demosRes.demos)
    setRoster(rosterRes.attendees)
    // Clear drafts for demos that no longer exist (e.g., after delete);
    // keep drafts for still-existing demos so an in-flight typing burst
    // isn't clobbered by the refetch.
    setDescriptionDrafts(prev => {
      const valid = new Set(demosRes.demos.map(d => d.id))
      const next: Record<number, string> = {}
      for (const [k, v] of Object.entries(prev)) {
        if (valid.has(Number(k))) next[Number(k)] = v
      }
      return next
    })
  }, [])

  useEffect(() => {
    let alive = true
    backend
      .get<{ meeting: CurrentMeeting | null }>('/meeting/current')
      .then(async res => {
        if (!alive) return
        const m = res.meeting
        setMeeting(m)
        if (m) {
          const myEntryRes = await backend.get<MyEntry>(`/meeting/${m.id}/my-entry`)
          if (!alive) return
          setSelfEmail(myEntryRes.user_email)
          await refreshDemosAndRoster(m.id)
        }
      })
      .catch(e => alive && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [refreshDemosAndRoster])

  // --- Save one user's demos via PUT /my-entry or /entries/{email} ---
  //
  // The backend's PUT replaces the user's project_entries list. We send
  // every demo currently in `demos` for that user, with the latest draft
  // description (if any).
  const flushUser = useCallback(async (email: string) => {
    const m = meetingRef.current
    if (!m) return
    if (saveInFlight.current.has(email)) {
      savePending.current.add(email)
      return
    }
    saveInFlight.current.add(email)
    setSavingUsers(prev => new Set(prev).add(email))
    setError(null)
    try {
      const userDemos = demosRef.current.filter(d => d.user_email === email)
      const project_entries = userDemos.map(d => ({
        project_id: d.project.id,
        description: draftsRef.current[d.id] ?? d.description,
      }))
      const path = email === selfEmail
        ? `/meeting/${m.id}/my-entry`
        : `/meeting/${m.id}/entries/${encodeURIComponent(email)}`
      await backend.put(path, { project_entries })
      await refreshDemosAndRoster(m.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      saveInFlight.current.delete(email)
      setSavingUsers(prev => {
        const n = new Set(prev)
        n.delete(email)
        return n
      })
      if (savePending.current.has(email)) {
        savePending.current.delete(email)
        void flushUser(email)
      }
    }
  }, [selfEmail, refreshDemosAndRoster])

  const scheduleUserSave = useCallback((email: string) => {
    const existing = saveTimers.current.get(email)
    if (existing) clearTimeout(existing)
    const handle = setTimeout(() => {
      saveTimers.current.delete(email)
      void flushUser(email)
    }, 800)
    saveTimers.current.set(email, handle)
  }, [flushUser])

  const flushUserNow = useCallback((email: string) => {
    const existing = saveTimers.current.get(email)
    if (existing) {
      clearTimeout(existing)
      saveTimers.current.delete(email)
    }
    void flushUser(email)
  }, [flushUser])

  // --- Editing handlers ---

  const onDescriptionChange = (demo: Demo, value: string) => {
    setDescriptionDrafts(prev => ({ ...prev, [demo.id]: value }))
    scheduleUserSave(demo.user_email)
  }

  const onDeleteDemo = async (demo: Demo) => {
    // Optimistically drop the demo from local state, then PUT the user's
    // remaining demos. The autosave path handles refresh.
    setDemos(prev => prev.filter(d => d.id !== demo.id))
    flushUserNow(demo.user_email)
  }

  const onAddDemoForSelf = async (project: MeetingProject) => {
    const m = meetingRef.current
    if (!m || !selfEmail) return
    // Compose the next project_entries set for the current user: every
    // existing demo + this new project (empty description). Then PUT.
    const myDemos = demosRef.current.filter(d => d.user_email === selfEmail)
    if (myDemos.some(d => d.project.id === project.id)) return  // already added
    const project_entries = [
      ...myDemos.map(d => ({
        project_id: d.project.id,
        description: draftsRef.current[d.id] ?? d.description,
      })),
      { project_id: project.id, description: '' },
    ]
    try {
      await backend.put(`/meeting/${m.id}/my-entry`, { project_entries })
      await refreshDemosAndRoster(m.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  // --- Admin attendance ---

  const setAttendance = async (email: string, attending: boolean) => {
    const m = meetingRef.current
    if (!m) return
    try {
      await backend.put(
        `/meeting/${m.id}/entries/${encodeURIComponent(email)}`,
        { attending },
      )
      await refreshDemosAndRoster(m.id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  // --- Admin reorder (drag and drop) ---

  const onReorder = async (newOrder: Demo[]) => {
    const m = meetingRef.current
    if (!m) return
    setDemos(newOrder)  // optimistic
    try {
      await backend.put(`/meeting/${m.id}/demos/order`, {
        order: newOrder.map(d => d.id),
      })
      // No need to refetch — order_index updates are inert for the UI.
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      // Server is the source of truth on failure.
      await refreshDemosAndRoster(m.id)
    }
  }

  if (loading) return <div className="card"><p>{t('form.loading')}</p></div>
  if (error && !meeting) {
    return (
      <div className="card">
        <h2>{t('form.title')}</h2>
        <p className="expired">{t('form.loadError', { error })}</p>
      </div>
    )
  }
  if (!meeting) {
    return (
      <div className="card">
        <h2>{t('form.title')}</h2>
        <p>{t('form.noMeeting')}</p>
      </div>
    )
  }

  return (
    <>
      <div className="card">
        <h2>{formatMeetingDate(meeting.meeting_date, i18n.language)}</h2>
        <AttendanceSection
          roster={roster}
          selfEmail={selfEmail}
          isAdmin={!!isAdmin}
          onSetAttendance={setAttendance}
        />
      </div>
      <div className="card">
        <DemoListSection
          demos={demos}
          drafts={descriptionDrafts}
          savingUsers={savingUsers}
          selfEmail={selfEmail}
          isAdmin={!!isAdmin}
          onDescriptionChange={onDescriptionChange}
          onDescriptionBlur={(email: string) => flushUserNow(email)}
          onDeleteDemo={onDeleteDemo}
          onReorder={onReorder}
          onAddDemoForSelf={onAddDemoForSelf}
        />
        {error && <p className="expired">{error}</p>}
      </div>
    </>
  )
}


// ---------------------------------------------------------------------------
// Attendance section
// ---------------------------------------------------------------------------

function AttendanceSection({
  roster,
  selfEmail,
  isAdmin,
  onSetAttendance,
}: {
  roster: RosterEntry[]
  selfEmail: string
  isAdmin: boolean
  onSetAttendance: (email: string, attending: boolean) => Promise<void>
}) {
  const { t } = useTranslation()
  const [busy, setBusy] = useState<string | null>(null)

  const handle = async (email: string, attending: boolean) => {
    setBusy(email)
    try {
      await onSetAttendance(email, attending)
    } finally {
      setBusy(null)
    }
  }

  if (roster.length === 0) {
    return (
      <section className="attendance-section">
        <h3>{t('attendance.title')}</h3>
        <p className="muted">{t('attendance.empty')}</p>
      </section>
    )
  }

  return (
    <section className="attendance-section">
      <h3>{t('attendance.title')}</h3>
      {!isAdmin && (
        <p className="muted">{t('attendance.adminOnlyHint')}</p>
      )}
      <ul className="admin-attendance-list" data-testid="attendance-list">
        {roster.map(r => {
          const isSelf = r.email === selfEmail
          return (
            <li key={r.email} className="admin-attendance-row">
              <span className="admin-attendance-name">
                {r.display_name}
                {isSelf && <span className="self-badge"> ({t('attendance.you')})</span>}
              </span>
              <span className={`roster-status roster-status--${r.status}`}>
                {t(`roster.status.${r.status}`)}
              </span>
              {isAdmin && (
                <span className="admin-attendance-buttons">
                  <button
                    className={r.status === 'yes' ? 'primary' : ''}
                    disabled={busy === r.email}
                    onClick={() => handle(r.email, true)}
                  >
                    {t('adminAttendance.attending')}
                  </button>{' '}
                  <button
                    className={r.status === 'no' ? 'primary' : ''}
                    disabled={busy === r.email}
                    onClick={() => handle(r.email, false)}
                  >
                    {t('adminAttendance.notAttending')}
                  </button>
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}


// ---------------------------------------------------------------------------
// Demo list section
// ---------------------------------------------------------------------------

function DemoListSection({
  demos,
  drafts,
  savingUsers,
  selfEmail,
  isAdmin,
  onDescriptionChange,
  onDescriptionBlur,
  onDeleteDemo,
  onReorder,
  onAddDemoForSelf,
}: {
  demos: Demo[]
  drafts: Record<number, string>
  savingUsers: Set<string>
  selfEmail: string
  isAdmin: boolean
  onDescriptionChange: (demo: Demo, value: string) => void
  onDescriptionBlur: (email: string) => void
  onDeleteDemo: (demo: Demo) => void
  onReorder: (newOrder: Demo[]) => void
  onAddDemoForSelf: (project: MeetingProject) => void
}) {
  const { t } = useTranslation()
  const [dragId, setDragId] = useState<number | null>(null)
  const [dragOverId, setDragOverId] = useState<number | null>(null)

  const onDragStart = (e: React.DragEvent, demo: Demo) => {
    if (!isAdmin) return
    setDragId(demo.id)
    e.dataTransfer.effectAllowed = 'move'
    // Firefox needs setData to start the drag.
    try { e.dataTransfer.setData('text/plain', String(demo.id)) } catch { /* noop */ }
  }

  const onDragOver = (e: React.DragEvent, demo: Demo) => {
    if (!isAdmin || dragId === null) return
    e.preventDefault()
    if (dragOverId !== demo.id) setDragOverId(demo.id)
  }

  const onDrop = (e: React.DragEvent, target: Demo) => {
    if (!isAdmin || dragId === null) return
    e.preventDefault()
    const fromIdx = demos.findIndex(d => d.id === dragId)
    const toIdx = demos.findIndex(d => d.id === target.id)
    setDragId(null)
    setDragOverId(null)
    if (fromIdx === -1 || toIdx === -1 || fromIdx === toIdx) return
    const next = demos.slice()
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    onReorder(next)
  }

  const onDragEnd = () => {
    setDragId(null)
    setDragOverId(null)
  }

  return (
    <section className="demo-list-section">
      <h3>{t('demos.title')}</h3>
      {demos.length === 0 ? (
        <p className="muted">{t('demos.empty')}</p>
      ) : (
        <ol className="demo-list">
          {demos.map((demo, idx) => {
            const isOwn = demo.user_email === selfEmail
            const editable = isOwn || isAdmin
            const draftValue = drafts[demo.id] ?? demo.description
            return (
              <li
                key={demo.id}
                className={[
                  'demo-row',
                  dragId === demo.id ? 'demo-row--dragging' : '',
                  dragOverId === demo.id && dragId !== demo.id ? 'demo-row--drop-target' : '',
                ].filter(Boolean).join(' ')}
                draggable={isAdmin}
                onDragStart={e => onDragStart(e, demo)}
                onDragOver={e => onDragOver(e, demo)}
                onDrop={e => onDrop(e, demo)}
                onDragEnd={onDragEnd}
                data-testid="demo-row"
              >
                <div className="demo-row__lead">
                  {isAdmin && (
                    <span className="demo-row__handle" aria-label="drag">≡</span>
                  )}
                  <span className="demo-row__number">{idx + 1}.</span>
                </div>
                <div className="demo-row__body">
                  <div className="demo-row__head">
                    <span className="demo-row__project">{demo.project.name}</span>
                    <span className="project-leader">
                      ({t('form.leader')}: {demo.project.leader.display_name}
                      {demo.project.leader.resolved ? '' : ` · ${t('leaderPicker.placeholderBadge')}`})
                    </span>
                  </div>
                  <div className="demo-row__presenter">
                    {demo.presenter_display_name}
                  </div>
                  {editable ? (
                    <textarea
                      className="demo-row__description"
                      value={draftValue}
                      onChange={e => onDescriptionChange(demo, e.target.value)}
                      onBlur={() => onDescriptionBlur(demo.user_email)}
                      placeholder={t('form.descriptionPlaceholder')}
                      rows={2}
                    />
                  ) : (
                    <p className="demo-row__description demo-row__description--readonly">
                      {demo.description || <span className="muted">{t('demos.noDescription')}</span>}
                    </p>
                  )}
                  <div className="demo-row__meta" aria-live="polite">
                    {savingUsers.has(demo.user_email) && (
                      <span className="muted">{t('form.saving')}</span>
                    )}
                  </div>
                </div>
                {editable && (
                  <button
                    className="demo-row__delete"
                    aria-label={t('demos.delete')}
                    onClick={() => onDeleteDemo(demo)}
                    title={t('demos.delete')}
                  >×</button>
                )}
              </li>
            )
          })}
        </ol>
      )}

      <AddDemoControl onPick={onAddDemoForSelf} />
    </section>
  )
}


// ---------------------------------------------------------------------------
// Add-demo control (project search + create-new sub-form)
// ---------------------------------------------------------------------------

function AddDemoControl({ onPick }: { onPick: (project: MeetingProject) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<MeetingProject[]>([])
  const [searching, setSearching] = useState(false)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newLeader, setNewLeader] = useState<Person | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)

  // Default to a small recent list while the box is open but empty.
  useEffect(() => {
    if (!open) return
    let alive = true
    backend
      .get<{ projects: MeetingProject[] }>('/projects?limit=20')
      .then(r => alive && setResults(r.projects))
      .catch(() => { /* swallow — search handles errors */ })
    return () => { alive = false }
  }, [open])

  const runSearch = async (q: string) => {
    setQuery(q)
    setSearching(true)
    try {
      const r = await backend.get<{ projects: MeetingProject[] }>(
        `/projects?q=${encodeURIComponent(q)}&limit=20`,
      )
      setResults(r.projects)
    } catch {
      setResults([])
    } finally {
      setSearching(false)
    }
  }

  const submitNew = async () => {
    setCreateError(null)
    const name = newName.trim()
    if (!name || !newLeader) {
      setCreateError(t('form.projectFieldsRequired'))
      return
    }
    try {
      const created = await backend.post<MeetingProject>('/projects', {
        name,
        leader_person_id: newLeader.id,
      })
      onPick(created)
      setNewName('')
      setNewLeader(null)
      setCreating(false)
      setOpen(false)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
    }
  }

  if (!open) {
    return (
      <button className="add-demo-toggle primary" onClick={() => setOpen(true)}>
        {t('demos.addDemo')}
      </button>
    )
  }

  return (
    <div className="add-demo">
      <div className="project-search">
        <input
          type="search"
          autoFocus
          value={query}
          onChange={e => runSearch(e.target.value)}
          placeholder={t('form.searchPlaceholder')}
        />{' '}
        <button onClick={() => { setOpen(false); setCreating(false) }}>
          {t('form.cancel')}
        </button>
        {searching && <span className="muted"> {t('form.searching')}</span>}
      </div>
      {results.length > 0 && (
        <ul className="search-results">
          {results.map(p => (
            <li key={p.id}>
              <button onClick={() => { onPick(p); setOpen(false) }}>
                + {p.name} ({p.leader.display_name})
              </button>
            </li>
          ))}
        </ul>
      )}

      {!creating ? (
        <button onClick={() => setCreating(true)}>
          {t('form.createProject')}
        </button>
      ) : (
        <div className="add-project">
          <label>
            {t('form.projectName')}:{' '}
            <input
              type="text"
              value={newName}
              onChange={e => setNewName(e.target.value)}
            />
          </label>{' '}
          <label className="leader-label">
            {t('form.leader')}:{' '}
            <LeaderPicker
              value={newLeader}
              onChange={setNewLeader}
              placeholder={t('form.leaderPlaceholder')}
            />
          </label>{' '}
          <button className="primary" onClick={submitNew}>{t('form.add')}</button>{' '}
          <button onClick={() => setCreating(false)}>{t('form.cancel')}</button>
          {createError && <p className="expired">{createError}</p>}
        </div>
      )}
    </div>
  )
}
