import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backend,
  type CurrentMeeting,
  type MeetingProject,
  type MyEntry,
  type Person,
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


export function MeetingForm() {
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

  // Search / catalog UI
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<MeetingProject[]>([])
  const [searching, setSearching] = useState(false)

  // New-project sub-form
  const [creatingNew, setCreatingNew] = useState(false)
  const [newName, setNewName] = useState('')
  const [newLeader, setNewLeader] = useState<Person | null>(null)
  const [createError, setCreateError] = useState<string | null>(null)

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
          const e = await backend.get<MyEntry>(`/meeting/${m.id}/my-entry`)
          if (!alive) return
          setEntry(e)
          setDraftAttending(e.attending)
          setDraftEntries(
            Object.fromEntries(e.project_entries.map(pe => [pe.project_id, pe.description])),
          )
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
    if (!(p.id in draftEntries)) {
      setDraftEntries(prev => ({ ...prev, [p.id]: '' }))
      setSavedAt(null)
    }
    if (!knownIds.has(p.id)) {
      setExtraProjects(prev => [...prev, p])
    }
  }

  const toggleProject = (id: number) => {
    setSavedAt(null)
    setDraftEntries(prev => {
      const copy = { ...prev }
      if (id in copy) delete copy[id]
      else copy[id] = ''
      return copy
    })
  }

  const updateDescription = (id: number, text: string) => {
    setSavedAt(null)
    setDraftEntries(prev => ({ ...prev, [id]: text }))
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

  const submit = async () => {
    if (!meeting) return
    setSaving(true)
    setError(null)
    try {
      const updated = await backend.put<MyEntry>(`/meeting/${meeting.id}/my-entry`, {
        attending: draftAttending,
        project_entries: Object.entries(draftEntries).map(([pid, desc]) => ({
          project_id: parseInt(pid, 10),
          description: desc,
        })),
      })
      setEntry(updated)
      setSavedAt(new Date())
      // Auto-subscribed projects should now appear in the subscriptions list.
      const subs = await backend.get<{ subscriptions: MeetingProject[] }>('/me/subscriptions')
      setSubscribed(subs.subscriptions)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="card"><p>{t('form.loading')}</p></div>
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

      <label className="block">
        <input
          type="checkbox"
          checked={draftAttending}
          onChange={e => { setDraftAttending(e.target.checked); setSavedAt(null) }}
        />{' '}
        {t('form.attending')}
      </label>

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
      <div className="save-row">
        <button className="primary" onClick={submit} disabled={saving}>
          {saving ? t('form.saving') : t('form.save')}
        </button>
        {savedAt && (
          <span className="form-saved">
            {t('form.savedAt', { time: savedAt.toLocaleTimeString() })}
          </span>
        )}
        {error && <p className="expired">{error}</p>}
      </div>
    </div>
  )
}
