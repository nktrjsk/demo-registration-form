import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backend,
  type Attendee,
  type CurrentMeeting,
  type MeetingDetails,
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
  const [entry, setEntry] = useState<MyEntry | null>(null)
  const [subscribed, setSubscribed] = useState<MeetingProject[]>([])
  const [extraProjects, setExtraProjects] = useState<MeetingProject[]>([])
  const [draftAttending, setDraftAttending] = useState(false)
  const [draftEntries, setDraftEntries] = useState<Record<number, string>>({})
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<Date | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [roster, setRoster] = useState<RosterEntry[] | null>(null)
  const [details, setDetails] = useState<MeetingDetails | null>(null)

  // Search / catalog UI
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<MeetingProject[]>([])
  const [searching, setSearching] = useState(false)

  // New-project sub-form
  const [creatingNew, setCreatingNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newLeader, setNewLeader] = useState<Person | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)

  // Autosave plumbing.
  // We mirror drafts into refs so handlers can pass the just-committed
  // value into doSave synchronously, without waiting for the next
  // render. saveInFlightRef + pendingSaveRef coalesce overlapping
  // saves so we never have two PUTs racing the entry state.
  const draftAttendingRef = useRef(draftAttending)
  const draftEntriesRef = useRef(draftEntries)
  const meetingRef = useRef<CurrentMeeting | null>(null)
  const entryRef = useRef<MyEntry | null>(null)
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveInFlightRef = useRef(false)
  const pendingSaveRef = useRef(false)

  meetingRef.current = meeting
  entryRef.current = entry

  useEffect(() => {
    let alive = true
    Promise.all([
      backend.get<{ meeting: CurrentMeeting | null }>('/meeting/current'),
      backend.get<{ subscriptions: MeetingProject[] }>('/me/subscriptions'),
    ])
      .then(async ([currentRes, subsRes]) => {
        if (!alive) return
        const m = currentRes.meeting
        setMeeting(m)
        setSubscribed(subsRes.subscriptions)
        if (m) {
          const [e, rosterRes, detailsRes] = await Promise.all([
            backend.get<MyEntry>(`/meeting/${m.id}/my-entry`),
            backend.get<{ attendees: RosterEntry[] }>(`/meeting/${m.id}/attendees`),
            backend.get<MeetingDetails>(`/meeting/${m.id}/details`),
          ])
          if (!alive) return
          setEntry(e)
          setRoster(rosterRes.attendees)
          setDetails(detailsRes)
          const loadedEntries = Object.fromEntries(
            e.project_entries.map(pe => [pe.project_id, pe.description]),
          )
          draftAttendingRef.current = e.attending
          draftEntriesRef.current = loadedEntries
          setDraftAttending(e.attending)
          setDraftEntries(loadedEntries)
          // Any project that appears in the user's previous entries but isn't in
          // their subscriptions needs to be loaded as an `extra` so it shows up.
          const subIds = new Set(subsRes.subscriptions.map(p => p.id))
          const missingIds = e.project_entries
            .map(pe => pe.project_id)
            .filter(id => !subIds.has(id))
          if (missingIds.length > 0) {
            const all = await backend.get<{ projects: MeetingProject[] }>('/projects?limit=200')
            if (!alive) return
            setExtraProjects(all.projects.filter(p => missingIds.includes(p.id)))
          }
        }
      })
      .catch(err => alive && setError(err instanceof Error ? err.message : String(err)))
      .finally(() => alive && setLoading(false))
    return () => { alive = false }
  }, [])

  // Combined ordered project list to render checkboxes for: subscribed first,
  // then extras (projects the user has notes on but didn't subscribe via the
  // catalog directly), then any search-result that's been "added to the form"
  // — i.e., currently has a draft entry but isn't in the two lists above.
  const knownIds = useMemo(
    () => new Set([...subscribed, ...extraProjects].map(p => p.id)),
    [subscribed, extraProjects],
  )

  const allProjectsCache = useMemo(() => {
    const map = new Map<number, MeetingProject>()
    for (const p of subscribed) map.set(p.id, p)
    for (const p of extraProjects) map.set(p.id, p)
    for (const p of searchResults) map.set(p.id, p)
    return map
  }, [subscribed, extraProjects, searchResults])

  // Non-admin users see a read-only attendance status sourced from the roster
  // (which carries the 3-state yes/no/no_response). The own /my-entry payload
  // can't distinguish "not attending" from "no row yet" on its own.
  const selfStatus: 'yes' | 'no' | 'no_response' = useMemo(() => {
    if (!entry || !roster) return 'no_response'
    const row = roster.find(r => r.email === entry.user_email)
    return row?.status ?? 'no_response'
  }, [entry, roster])

  const visibleProjects = useMemo(() => {
    const out: MeetingProject[] = []
    const seen = new Set<number>()
    const push = (p: MeetingProject) => {
      if (seen.has(p.id)) return
      seen.add(p.id)
      out.push(p)
    }
    for (const p of subscribed) push(p)
    for (const p of extraProjects) push(p)
    // Also surface any draftEntries project_ids that we somehow have cached
    // (e.g., just added from search) but aren't yet in subscribed/extra.
    for (const idStr of Object.keys(draftEntries)) {
      const id = Number(idStr)
      const p = allProjectsCache.get(id)
      if (p) push(p)
    }
    return out
  }, [subscribed, extraProjects, draftEntries, allProjectsCache])

  const runSearch = async (q: string) => {
    setSearchQuery(q)
    if (q.trim().length === 0) {
      setSearchResults([])
      return
    }
    setSearching(true)
    try {
      const r = await backend.get<{ projects: MeetingProject[] }>(
        `/projects?q=${encodeURIComponent(q)}&limit=20`,
      )
      setSearchResults(r.projects)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSearching(false)
    }
  }

  const addProjectToForm = (p: MeetingProject) => {
    if (!(p.id in draftEntriesRef.current)) {
      const next = { ...draftEntriesRef.current, [p.id]: '' }
      draftEntriesRef.current = next
      setDraftEntries(next)
      setSavedAt(null)
      void doSave()
    }
    if (!knownIds.has(p.id)) {
      setExtraProjects(prev => [...prev, p])
    }
  }

  const toggleProject = (id: number) => {
    const next = { ...draftEntriesRef.current }
    if (id in next) delete next[id]
    else next[id] = ''
    draftEntriesRef.current = next
    setDraftEntries(next)
    setSavedAt(null)
    void doSave()
  }

  const updateDescription = (id: number, text: string) => {
    const next = { ...draftEntriesRef.current, [id]: text }
    draftEntriesRef.current = next
    setDraftEntries(next)
    setSavedAt(null)
    scheduleSave()
  }

  const onAttendingChange = (checked: boolean) => {
    draftAttendingRef.current = checked
    setDraftAttending(checked)
    setSavedAt(null)
    void doSave()
  }

  const flushSave = () => {
    if (saveTimerRef.current) {
      clearTimeout(saveTimerRef.current)
      saveTimerRef.current = null
    }
    void doSave()
  }

  const submitNewProject = async () => {
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
      addProjectToForm(created)
      setNewName('')
      setNewLeader(null)
      setCreatingNew(false)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
    }
  }

  const doSave = async () => {
    const m = meetingRef.current
    const persisted = entryRef.current
    if (!m || !persisted) return

    // If a save is already in flight, mark that another one is needed
    // and return — the in-flight call will retrigger after it finishes,
    // which guarantees we eventually catch up to the latest drafts
    // without ever having two PUTs racing the entry state.
    if (saveInFlightRef.current) {
      pendingSaveRef.current = true
      return
    }

    const attending = draftAttendingRef.current
    const entries = draftEntriesRef.current

    // Cheap dirty-check against the last persisted snapshot. Skipping
    // no-op saves keeps the autosave indicator honest and avoids
    // spamming the server when handlers fire without real changes.
    const persistedEntries: Record<number, string> = Object.fromEntries(
      persisted.project_entries.map(pe => [pe.project_id, pe.description]),
    )
    const draftKeys = Object.keys(entries)
    const persistedKeys = Object.keys(persistedEntries)
    const sameLength = draftKeys.length === persistedKeys.length
    const sameValues = sameLength && draftKeys.every(
      k => persistedEntries[Number(k)] === entries[Number(k)],
    )
    const attendingChanged = isAdmin && attending !== persisted.attending
    const dirty = attendingChanged || !sameLength || !sameValues
    if (!dirty) return

    saveInFlightRef.current = true
    setSaving(true)
    setError(null)
    try {
      // Attendance is admin-only — non-admin callers must omit the field
      // (the backend would reject the PUT with 403 otherwise).
      const body: { attending?: boolean; project_entries: { project_id: number; description: string }[] } = {
        project_entries: Object.entries(entries).map(([pid, desc]) => ({
          project_id: parseInt(pid, 10),
          description: desc,
        })),
      }
      if (isAdmin) body.attending = attending
      const updated = await backend.put<MyEntry>(`/meeting/${m.id}/my-entry`, body)
      setEntry(updated)
      setSavedAt(new Date())
      // Auto-subscribed projects should now appear in the subscriptions list,
      // and the roster should reflect the user's new attendance status.
      const [subs, rosterRes] = await Promise.all([
        backend.get<{ subscriptions: MeetingProject[] }>('/me/subscriptions'),
        backend.get<{ attendees: RosterEntry[] }>(`/meeting/${m.id}/attendees`),
      ])
      setSubscribed(subs.subscriptions)
      setRoster(rosterRes.attendees)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      saveInFlightRef.current = false
      setSaving(false)
      if (pendingSaveRef.current) {
        pendingSaveRef.current = false
        void doSave()
      }
    }
  }

  const scheduleSave = () => {
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(() => {
      saveTimerRef.current = null
      void doSave()
    }, 800)
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
  if (!entry) return null

  return (
    <div className="card">
      <h2>{formatMeetingDate(meeting.meeting_date, i18n.language)}</h2>
      <p className="muted">
        {t('form.yourEmail')}:{' '}
        <strong data-testid="meeting-form-email">{entry.user_email}</strong>
      </p>

      {isAdmin ? (
        <label className="block">
          <input
            type="checkbox"
            checked={draftAttending}
            onChange={e => onAttendingChange(e.target.checked)}
          />{' '}
          {t('form.attending')}
        </label>
      ) : (
        <p className="block muted" data-testid="meeting-form-attendance">
          {t('form.attendanceLabel')}:{' '}
          <span className={`roster-status roster-status--${selfStatus}`}>
            {t(`roster.status.${selfStatus}`)}
          </span>
        </p>
      )}

      <h3>{t('form.myProjects')}</h3>
      {visibleProjects.length === 0 ? (
        <p>{t('form.noSubscriptions')}</p>
      ) : (
        <ul className="project-list">
          {visibleProjects.map(p => (
            <li key={p.id} className="project-row">
              <label>
                <input
                  type="checkbox"
                  checked={p.id in draftEntries}
                  onChange={() => toggleProject(p.id)}
                />{' '}
                <strong>{p.name}</strong>{' '}
                <span className="project-leader">({t('form.leader')}: {p.leader.display_name}{p.leader.resolved ? '' : ` · ${t('leaderPicker.placeholderBadge')}`})</span>
              </label>
              {p.id in draftEntries && (
                <textarea
                  value={draftEntries[p.id]}
                  onChange={e => updateDescription(p.id, e.target.value)}
                  onBlur={flushSave}
                  placeholder={t('form.descriptionPlaceholder')}
                  rows={2}
                />
              )}
            </li>
          ))}
        </ul>
      )}

      <h3>{t('form.addProjectSection')}</h3>
      <div className="project-search">
        <input
          type="search"
          value={searchQuery}
          onChange={e => runSearch(e.target.value)}
          placeholder={t('form.searchPlaceholder')}
        />
        {searching && <span className="muted"> {t('form.searching')}</span>}
      </div>
      {searchResults.length > 0 && (
        <ul className="search-results">
          {searchResults
            .filter(p => !(p.id in draftEntries))
            .map(p => (
              <li key={p.id}>
                <button onClick={() => addProjectToForm(p)}>
                  + {p.name} ({p.leader.display_name})
                </button>
              </li>
            ))}
        </ul>
      )}

      {!creatingNew ? (
        <button onClick={() => { setCreatingNew(true); setCreateError(null) }}>
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
              isAdmin={isAdmin}
            />
          </label>{' '}
          <button onClick={submitNewProject}>{t('form.add')}</button>{' '}
          <button onClick={() => { setCreatingNew(false); setCreateError(null) }}>
            {t('form.cancel')}
          </button>
          {createError && <p className="expired">{createError}</p>}
        </div>
      )}

      <hr />
      <div className="save-row save-row--auto" aria-live="polite">
        {saving && <span className="muted">{t('form.saving')}</span>}
        {!saving && savedAt && (
          <span className="form-saved">
            {t('form.savedAt', { time: savedAt.toLocaleTimeString() })}
          </span>
        )}
        {error && <p className="expired">{error}</p>}
      </div>

      {roster !== null && (
        <section className="roster">
          <h3>{t('roster.title')}</h3>
          {roster.length === 0 ? (
            <p className="muted">{t('roster.empty')}</p>
          ) : (
            <ul className="roster-list">
              {roster.map(r => (
                <li key={r.email} className="roster-row">
                  <span className="roster-name">{r.display_name}</span>
                  <span className={`roster-status roster-status--${r.status}`}>
                    {t(`roster.status.${r.status}`)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {details !== null && (
        <ColleaguesNotes
          attendees={details.attendees}
          projects={details.meeting.projects}
          roster={roster}
          selfEmail={entry.user_email}
        />
      )}
    </div>
  )
}


function ColleaguesNotes({
  attendees,
  projects,
  roster,
  selfEmail,
}: {
  attendees: Attendee[]
  projects: MeetingProject[]
  roster: RosterEntry[] | null
  selfEmail: string
}) {
  const { t } = useTranslation()
  const nameByEmail = useMemo(() => {
    const m = new Map<string, string>()
    if (roster) for (const r of roster) m.set(r.email, r.display_name)
    return m
  }, [roster])

  // Group entries by project, excluding the current user (they see their
  // own notes in the form above). Only projects that actually have at
  // least one colleague entry are surfaced — the sample meeting layout
  // groups updates under the project they belong to.
  const byProject = useMemo(() => {
    const m = new Map<number, { email: string; description: string }[]>()
    for (const a of attendees) {
      if (a.email === selfEmail) continue
      for (const pe of a.project_entries) {
        const list = m.get(pe.project_id) ?? []
        list.push({ email: a.email, description: pe.description })
        m.set(pe.project_id, list)
      }
    }
    return m
  }, [attendees, selfEmail])

  const visibleProjects = projects.filter(p => byProject.has(p.id))

  return (
    <section className="colleagues-notes">
      <h3>{t('colleaguesNotes.title')}</h3>
      {visibleProjects.length === 0 ? (
        <p className="muted">{t('colleaguesNotes.empty')}</p>
      ) : (
        <ul className="colleagues-notes-list">
          {visibleProjects.map(p => {
            const entries = byProject.get(p.id) ?? []
            return (
              <li key={p.id} className="colleagues-notes-row">
                <div className="colleagues-notes-head">
                  <span className="colleagues-notes-project">{p.name}</span>
                  <span className="colleagues-notes-leader">
                    ({t('form.leader')}: {p.leader.display_name})
                  </span>
                </div>
                <ul className="colleagues-notes-entries">
                  {entries.map(e => (
                    <li key={e.email}>
                      <em>{nameByEmail.get(e.email) ?? e.email}:</em>{' '}
                      {e.description}
                    </li>
                  ))}
                </ul>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
