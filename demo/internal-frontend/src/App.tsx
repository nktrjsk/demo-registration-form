import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  backend,
  getUserInfo,
  fetchPublicConfig,
  fetchSchedule,
  type UserInfo,
  type MeetingSchedule,
  type PublicConfig,
} from './api'
import { MeetingForm } from './MeetingForm'
import { HistoryTab } from './HistoryTab'
import './App.css'

type Tab = 'current' | 'history'

const WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const


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

  const toggle = () => setTheme(t => t === 'dark' ? 'light' : 'dark')
  return { theme, toggle }
}


function App() {
  const { t, i18n } = useTranslation()
  const { theme, toggle: toggleTheme } = useTheme()
  const [user, setUser] = useState<UserInfo | null>(null)
  const [publicConfig, setPublicConfig] = useState<PublicConfig | null>(null)
  const [schedule, setSchedule] = useState<MeetingSchedule | null>(null)
  const [editingSchedule, setEditingSchedule] = useState(false)
  const [draftWeekday, setDraftWeekday] = useState(0)
  const [draftStartTime, setDraftStartTime] = useState('15:00')
  const [scheduleError, setScheduleError] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('current')

  useEffect(() => {
    getUserInfo().then(setUser).catch(err => console.error('Failed to fetch user info:', err))
    fetchPublicConfig().then(setPublicConfig).catch(err => console.error('Failed to fetch config:', err))
    fetchSchedule().then(setSchedule).catch(err => console.error('Failed to fetch schedule:', err))
  }, [])

  const changeLang = (e: React.ChangeEvent<HTMLSelectElement>) => {
    i18n.changeLanguage(e.target.value)
  }

  const isAdmin = !!(user?.groups && publicConfig && user.groups.includes(publicConfig.admin_group))

  const beginEditSchedule = () => {
    if (!schedule) return
    setDraftWeekday(schedule.weekday)
    setDraftStartTime(schedule.start_time)
    setScheduleError(null)
    setEditingSchedule(true)
  }

  const saveSchedule = async () => {
    setScheduleError(null)
    try {
      const updated = await backend.put<MeetingSchedule>('/schedule', {
        weekday: draftWeekday,
        start_time: draftStartTime,
      })
      setSchedule(updated)
      setEditingSchedule(false)
    } catch (err) {
      setScheduleError(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="app">
      <div className="toolbar">
        <select className="lang-select" value={i18n.language} onChange={changeLang}>
          <option value="en">{t('language.en')}</option>
          <option value="cs">{t('language.cs')}</option>
        </select>
        <button onClick={toggleTheme}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
      </div>

      <h1>{t('app.title')}</h1>

      {user && (
        <div className="user-bar">
          <span>{user.email || user.preferredUsername}</span>{' '}
          <button className="sign-out" onClick={() => window.location.href = '/oauth2/sign_out'}>
            {t('userInfo.signOut')}
          </button>
        </div>
      )}

      <nav className="tabs">
        <button
          className={tab === 'current' ? 'tab active' : 'tab'}
          onClick={() => setTab('current')}
        >
          {t('tabs.current')}
        </button>
        <button
          className={tab === 'history' ? 'tab active' : 'tab'}
          onClick={() => setTab('history')}
        >
          {t('tabs.history')}
        </button>
      </nav>

      {tab === 'current' && <MeetingForm />}
      {tab === 'history' && <HistoryTab isAdmin={isAdmin} />}

      {schedule && (
        <div className="card">
          <h2>{t('schedule.title')}</h2>
          <p>
            {t('schedule.current')}:{' '}
            <strong>{t(`weekday.${WEEKDAYS[schedule.weekday]}`)} {schedule.start_time}</strong>
          </p>
          {isAdmin && !editingSchedule && (
            <button onClick={beginEditSchedule}>{t('schedule.edit')}</button>
          )}
          {isAdmin && editingSchedule && (
            <div className="schedule-edit">
              <label>
                {t('schedule.weekday')}:{' '}
                <select
                  value={draftWeekday}
                  onChange={e => setDraftWeekday(parseInt(e.target.value, 10))}
                >
                  {WEEKDAYS.map((w, i) => (
                    <option key={w} value={i}>{t(`weekday.${w}`)}</option>
                  ))}
                </select>
              </label>{' '}
              <label>
                {t('schedule.startTime')}:{' '}
                <input
                  type="time"
                  value={draftStartTime}
                  onChange={e => setDraftStartTime(e.target.value)}
                />
              </label>{' '}
              <button onClick={saveSchedule}>{t('schedule.save')}</button>{' '}
              <button onClick={() => setEditingSchedule(false)}>{t('schedule.cancel')}</button>
              {scheduleError && <p className="expired">{scheduleError}</p>}
            </div>
          )}
        </div>
      )}

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
