import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { QRCodeSVG } from 'qrcode.react'
import { Area, AreaChart, Line, PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, BellRing, CheckCircle2, ChevronLeft, ChevronRight, Droplets, ExternalLink, HelpCircle, Info, MapPin, Menu, MessageSquare, Monitor, Moon, Settings2, ShieldCheck, Sun, ThermometerSun, X } from 'lucide-react'
import { t, type Lang } from './i18n'
import { AuthProvider, useAppAuth } from './auth/AuthContext'
import { AuthHeaderControl } from './auth/AuthHeaderControl'
import { OnboardingGuide } from './OnboardingGuide'
import { LocationPicker } from './LocationPicker'
import { WaterloggingPanel } from './WaterloggingPanel'
import { ThemeProvider, useTheme, type ThemePref } from './ThemeContext'
import { codeSegments, currentIndex, isHttpsUrl, temperatureTrend } from './dashboard'
import './styles.css'
import './overrides.css'

const ONBOARDING_STORAGE_KEY = 'heatshield_onboarding_done'

/* ── Types ─────────────────────────────────────────────────────── */
type Hour = { time: string; temperature: number; humidity: number; risk: number; level?: string; apparentTemperature?: number }
type PlanView = { title: string; message: string; checklist: string[]; generated_by: string }
type Worker = { chatId: string; name: string; status: string; channel?: string }
type Alert = { alertId: string; status: string; chatId: string; channel?: string }
type Site = { siteId: string; name: string; workload: string; acclimatization: string; clothing?: string; latitude?: number; longitude?: number; defaultLanguage?: string; telegramBotUsername?: string; discordInviteUrl?: string }
type CanonicalPlan = { status?: string; date?: string; mode?: string; blocks?: Array<{ rest_minutes_per_hour: number; water_every_minutes: number }> }
type Controls = { time: string; level: string; restMinutesPerHour: number; waterEveryMinutes: number; allowedWork: string }
type ApiResult = { ok: boolean; status: number; payload: Record<string, any> }
type Dashboard = {
  forecast: { location: string; hourly: Hour[]; source: string }
  current: Hour
  peak: Hour
  level: string
  controls?: Controls
  plan: PlanView
  canonicalPlan?: CanonicalPlan
  site?: Site
  workers?: Worker[]
  alerts?: Alert[]
  updated_at: string
}

/* ── Constants ─────────────────────────────────────────────────── */
const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:3000'
const POLL_INTERVAL_MS = 30_000
const BAND_COLOR: Record<string, string> = { low: '#13805f', moderate: '#a86a14', high: '#bb4a1f', extreme: '#9e2a1f' }

const DEMO_DATA: Dashboard = {
  forecast: {
    location: 'Hyderabad, India',
    source: 'offline demo forecast',
    hourly: [
      { time: '06:00', temperature: 27, humidity: 74, risk: 22 },
      { time: '08:00', temperature: 30, humidity: 66, risk: 38 },
      { time: '10:00', temperature: 34, humidity: 57, risk: 61 },
      { time: '12:00', temperature: 38, humidity: 48, risk: 84 },
      { time: '14:00', temperature: 40, humidity: 44, risk: 91 },
      { time: '16:00', temperature: 37, humidity: 49, risk: 76 },
      { time: '18:00', temperature: 33, humidity: 61, risk: 52 },
    ],
  },
  current: { time: '06:00', temperature: 27, humidity: 74, risk: 22 },
  peak: { time: '14:00', temperature: 40, humidity: 44, risk: 91 },
  level: 'extreme',
  controls: { time: '14:00', level: 'EXTREME', restMinutesPerHour: 30, waterEveryMinutes: 15, allowedWork: 'STOP_HEAVY' },
  plan: {
    title: 'HeatShield morning plan',
    message: 'Peak risk is extreme at 14:00 (91/100).',
    checklist: [
      'Move heavy work before 10:00 or after 17:00.',
      'Drink water every 15 minutes; do not wait until thirsty.',
      'Use a buddy check and stop immediately for dizziness, confusion, or nausea.',
    ],
    generated_by: 'offline demo',
  },
  updated_at: new Date().toISOString(),
}

/* ── Theme toggle ──────────────────────────────────────────────── */
const THEME_ICON: Record<ThemePref, typeof Sun> = { system: Monitor, light: Sun, dark: Moon }
const THEME_LABEL: Record<ThemePref, string> = { system: 'System', light: 'Light', dark: 'Dark' }

function ThemeToggle({ variant = 'icon' }: { variant?: 'icon' | 'labeled' }) {
  const { theme, cycleTheme } = useTheme()
  const Icon = THEME_ICON[theme]
  const title = `Theme: ${THEME_LABEL[theme]} (click to change)`
  if (variant === 'labeled') {
    return (
      <button type="button" className="theme-toggle-labeled" onClick={cycleTheme} title={title} aria-label={title}>
        <Icon size={18} /> Theme: {THEME_LABEL[theme]}
      </button>
    )
  }
  return (
    <button type="button" className="icon-button" onClick={cycleTheme} title={title} aria-label={title}>
      <Icon size={18} />
    </button>
  )
}

/* ── QR Code ───────────────────────────────────────────────────── */
function QrCode({ value, size = 120 }: { value: string; size?: number }) {
  return (
    <div className="qr-code" role="img" aria-label="QR code to join on Telegram or Discord">
      <QRCodeSVG value={value} size={size} level="M" marginSize={2} />
    </div>
  )
}

/** Renders `backtick` spans in a translated string as inline code. */
function Inline({ text }: { text: string }) {
  return <>{codeSegments(text).map((part, idx) => idx % 2 ? <code key={idx}>{part}</code> : <React.Fragment key={idx}>{part}</React.Fragment>)}</>
}

/* ── Helpers ────────────────────────────────────────────────────── */
function todayString() {
  const d = new Date()
  const days = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
  const months = ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']
  return `${days[d.getDay()]}, ${String(d.getDate()).padStart(2, '0')} ${months[d.getMonth()]} ${d.getFullYear()}`
}

/* ── App ───────────────────────────────────────────────────────── */
function App() {
  const { user, getToken } = useAppAuth()
  const [data, setData] = useState<Dashboard | null>(null)
  const [language, setLanguage] = useState<Lang>('en')
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  // A plan built from past weather is shown on its own; today's plan and its status always come from the server.
  const [replayPlan, setReplayPlan] = useState<{ view: PlanView; date: string; status: string; controls?: Controls } | null>(null)
  const [siteFormError, setSiteFormError] = useState('')
  const [replay, setReplay] = useState(false)
  const [showSiteForm, setShowSiteForm] = useState(false)
  const [siteSection, setSiteSection] = useState<'details' | 'location' | 'channels'>('details')
  const [siteForm, setSiteForm] = useState({ name: 'My Work Site', workload: 'MODERATE', acclimatization: 'ACCLIMATIZED', clothing: 'NORMAL', defaultLanguage: 'te', latitude: 17.385, longitude: 78.487, telegramBotUsername: 'HeatShieldBot', discordInviteUrl: 'https://discord.gg/heatshield' })
  const [siteId, setSiteId] = useState('demo')
  const [isLive, setIsLive] = useState(false)
  const [onboardChannel, setOnboardChannel] = useState<'telegram' | 'discord'>('telegram')
  const [customEmailMessage, setCustomEmailMessage] = useState('')
  const [showEmailEditor, setShowEmailEditor] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(false)
  const [showMobileNav, setShowMobileNav] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const alertPanelRef = useRef<HTMLDivElement>(null)
  const i = t(language)

  useEffect(() => {
    try {
      if (!localStorage.getItem(ONBOARDING_STORAGE_KEY)) setShowOnboarding(true)
    } catch {
      setShowOnboarding(true)
    }
  }, [])

  useEffect(() => {
    if (!showMobileNav) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowMobileNav(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showMobileNav])

  const closeOnboarding = () => {
    setShowOnboarding(false)
    try { localStorage.setItem(ONBOARDING_STORAGE_KEY, '1') } catch { /* storage unavailable */ }
  }

  /* ── Fetch dashboard data ─────────────────────────────────────── */
  const fetchDashboard = useCallback(async (sid?: string) => {
    const id = sid || siteId
    try {
      const r = await fetch(`${API}/api/sites/${id}/status`)
      if (!r.ok) throw new Error('API unavailable')
      const payload: Dashboard = await r.json()
      setData(payload)
      setIsLive(true)
      if (payload.site?.siteId) setSiteId(payload.site.siteId)
    } catch {
      try {
        const r2 = await fetch(`${API}/api/dashboard`)
        if (!r2.ok) throw new Error('fallback failed')
        const payload2: Dashboard = await r2.json()
        setData(payload2)
        setIsLive(true)
        if (payload2.site?.siteId) setSiteId(payload2.site.siteId)
      } catch {
        setData(DEMO_DATA)
        setIsLive(false)
        setMessage(i.demoDataMsg)
      }
    }
  }, [siteId, i.demoDataMsg])

  useEffect(() => {
    fetchDashboard().finally(() => setLoading(false))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Auto-refresh polling ─────────────────────────────────────── */
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(() => fetchDashboard(), POLL_INTERVAL_MS)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [fetchDashboard])

  /* ── Derived values ───────────────────────────────────────────── */
  if (loading) return <div className="loading"><ShieldCheck size={42} />{i.loading}</div>
  if (!data) return <div className="loading"><ShieldCheck size={42} />{i.demoReady}</div>

  const activePlan = replayPlan?.view || data.plan
  const planStatus = replayPlan ? replayPlan.status : (data.canonicalPlan?.status || 'DRAFT')
  const planDate = replayPlan ? replayPlan.date : data.canonicalPlan?.date
  // Rest and water rules for the hottest hour of the plan on screen, as computed by the server.
  const controls = replayPlan ? replayPlan.controls : data.controls
  const waterEvery = controls?.waterEveryMinutes
  const restMinutes = controls?.restMinutesPerHour ?? 0
  const hourly = data.forecast?.hourly || []
  const nowIndex = currentIndex(hourly, data.current.time)
  const trend = temperatureTrend(hourly, data.current.time)
  const openAlerts = data.alerts?.filter(a => a.status === 'OPEN').length || 0
  const alertsLabel = `${i.safetyAlerts}: ${openAlerts} ${i.open}`
  const telegramBotUsername = (data.site?.telegramBotUsername || import.meta.env.VITE_TELEGRAM_BOT || 'HeatShieldBot').replace(/[^A-Za-z0-9_]/g, '')
  const telegramJoinUrl = `https://t.me/${telegramBotUsername}?start=${siteId}`
  const discordInviteBase = [data.site?.discordInviteUrl, import.meta.env.VITE_DISCORD_INVITE].find(isHttpsUrl) || 'https://discord.gg/heatshield'
  const discordJoinUrl = discordInviteBase.includes('?') ? `${discordInviteBase}&site=${siteId}` : `${discordInviteBase}?site=${siteId}`
  const joinUrl = onboardChannel === 'telegram' ? telegramJoinUrl : discordJoinUrl

  /* ── Actions ──────────────────────────────────────────────────── */
  const request = async (path: string, body: unknown, method = 'POST'): Promise<ApiResult> => {
    const token = await getToken()
    const response = await fetch(`${API}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
    })
    let payload: Record<string, any> = {}
    try { payload = await response.json() } catch { /* no JSON body */ }
    return { ok: response.ok, status: response.status, payload }
  }

  // Every action reports what the server actually said; nothing is marked done on a failed request.
  const failureMessage = (result: ApiResult) => {
    if (result.status === 401) return i.signInRequired
    if (result.status === 503) return i.weatherUnavailable
    if ((result.status === 400 || result.status === 409) && typeof result.payload.error === 'string') return result.payload.error
    return i.actionFailed
  }

  const generate = async () => {
    setMessage(i.generating)
    try {
      const result = await request(`/api/sites/${siteId}/plans/generate`, { mode: replay ? 'REPLAY' : 'LIVE', replayDate: replay ? '2026-05-20' : undefined })
      if (!result.ok) { setMessage(failureMessage(result)); return }
      if (replay) {
        setReplayPlan({ view: result.payload.planView, date: result.payload.plan.date, status: 'DRAFT', controls: result.payload.controls })
      } else {
        setReplayPlan(null)
        await fetchDashboard()
      }
      setMessage(replay ? i.historicalGenerated : i.liveGenerated)
    } catch {
      setMessage(i.serverUnreachable)
    }
  }

  const approve = async () => {
    if (!planDate) { setMessage(i.serverUnreachable); return }
    try {
      const result = await request(`/api/sites/${siteId}/plans/${planDate}/approve`, user?.fullName ? { approvedBy: user.fullName } : {})
      if (!result.ok) { setMessage(failureMessage(result)); return }
      if (replayPlan) setReplayPlan({ ...replayPlan, status: 'APPROVED' })
      else await fetchDashboard()
      setMessage(i.approvalRecorded)
    } catch {
      setMessage(i.serverUnreachable)
    }
  }

  const send = async () => {
    if (planStatus !== 'APPROVED') { setMessage(i.approveFirst); return }
    if (!planDate) { setMessage(i.serverUnreachable); return }
    setMessage(i.sendingToCrew)
    try {
      const result = await request(`/api/sites/${siteId}/plans/${planDate}/send`, { customEmailMessage: customEmailMessage || undefined })
      if (!result.ok) { setMessage(failureMessage(result)); return }
      await fetchDashboard()
      setMessage(i.delivered(result.payload.delivered ?? 0))
      setShowEmailEditor(false)
      setCustomEmailMessage('')
    } catch {
      setMessage(i.sendFailed)
    }
  }

  const resolveAlert = async (alertId: string) => {
    try {
      const result = await request(`/api/alerts/${encodeURIComponent(alertId)}`, { siteId })
      if (!result.ok) { setMessage(failureMessage(result)); return }
      await fetchDashboard()
    } catch {
      setMessage(i.serverUnreachable)
    }
  }

  const showAlerts = () => {
    setShowSiteForm(false)
    setShowMobileNav(false)
    // Wait for the dashboard to be back on screen before scrolling to the check-ins panel.
    setTimeout(() => alertPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 0)
  }

  const openSiteForm = () => {
    if (data.site) {
      setSiteForm({
        name: data.site.name,
        workload: data.site.workload,
        acclimatization: data.site.acclimatization,
        clothing: data.site.clothing || 'NORMAL',
        defaultLanguage: data.site.defaultLanguage || 'te',
        latitude: data.site.latitude ?? siteForm.latitude,
        longitude: data.site.longitude ?? siteForm.longitude,
        telegramBotUsername: data.site.telegramBotUsername || siteForm.telegramBotUsername,
        discordInviteUrl: data.site.discordInviteUrl || siteForm.discordInviteUrl,
      })
    }
    setSiteFormError('')
    setShowSiteForm(true)
  }

  // Editing an already-loaded live site updates it in place (PUT) so the
  // existing site keeps its workers/plans/history; only a first-time setup
  // (no live site yet) creates a new one (POST).
  const editingExistingSite = isLive && Boolean(data.site)

  const createSite = async (event: React.FormEvent) => {
    event.preventDefault()
    setSiteFormError('')
    try {
      const result = editingExistingSite
        ? await request(`/api/sites/${siteId}`, siteForm, 'PUT')
        : await request('/api/sites', siteForm)
      if (!result.ok) { setSiteFormError(failureMessage(result)); return }
      const newId = result.payload.site.siteId
      setSiteId(newId)
      setReplayPlan(null)
      await fetchDashboard(newId)
      setShowSiteForm(false)
      setMessage(editingExistingSite ? i.siteFormSaved : i.siteCreated)
    } catch {
      setSiteFormError(i.serverUnreachable)
    }
  }

  /* ── Render ───────────────────────────────────────────────────── */
  return (
    <div className="app">
      {/* Header */}
      <header>
        <div className="brand">
          <div className="brand-mark"><ShieldCheck size={22} /></div>
          <div><strong>HeatShield</strong><span>{i.brandTagline}</span></div>
        </div>
        <div className="header-right">
          <ThemeToggle />
          <button className="icon-button" onClick={() => setShowOnboarding(true)} title={i.onboardingReopen} aria-label={i.onboardingReopen}><HelpCircle size={18} /></button>
          <button className="icon-button" onClick={showAlerts} title={alertsLabel} aria-label={alertsLabel}>
            <BellRing size={18} />
            {openAlerts > 0 && <span className="icon-badge" aria-hidden="true">{openAlerts}</span>}
          </button>
          <AuthHeaderControl lang={language} telegramBotUsername={telegramBotUsername} discordInviteUrl={discordInviteBase} siteName={data.site?.name} siteId={data.site?.siteId} />
        </div>
        <button
          className="mobile-menu-toggle"
          onClick={() => setShowMobileNav(true)}
          aria-label="Open menu"
          aria-expanded={showMobileNav}
        >
          <Menu size={22} />
        </button>
      </header>

      {showMobileNav && (
        <div className="mobile-nav-backdrop" onClick={() => setShowMobileNav(false)}>
          <nav className="mobile-nav" onClick={e => e.stopPropagation()} aria-label="Mobile menu">
            <div className="mobile-nav-head">
              <div className="brand">
                <div className="brand-mark"><ShieldCheck size={20} /></div>
                <div><strong>HeatShield</strong><span>{i.brandTagline}</span></div>
              </div>
              <button className="icon-button" onClick={() => setShowMobileNav(false)} aria-label="Close menu"><X size={20} /></button>
            </div>
            <div className="mobile-nav-actions">
              <ThemeToggle variant="labeled" />
              <button type="button" onClick={() => { setShowOnboarding(true); setShowMobileNav(false) }}>
                <HelpCircle size={18} /> {i.onboardingReopen}
              </button>
              <button type="button" onClick={showAlerts}>
                <BellRing size={18} /> {i.safetyAlerts}{openAlerts > 0 ? ` (${openAlerts})` : ''}
              </button>
            </div>
            <div className="mobile-nav-auth">
              <AuthHeaderControl lang={language} telegramBotUsername={telegramBotUsername} discordInviteUrl={discordInviteBase} siteName={data.site?.name} siteId={data.site?.siteId} />
            </div>
          </nav>
        </div>
      )}

      <OnboardingGuide open={showOnboarding} language={language} onClose={closeOnboarding} />

      {showSiteForm ? (
        <main>
          {/* Site settings page */}
          <section className="site-page">
            <button type="button" className="back-link" onClick={() => setShowSiteForm(false)}>
              <ChevronLeft size={16} /> {i.closeSiteForm}
            </button>
            <h1>{i.createOrEditSite}</h1>
            <p className="subtitle">{i.subtitle}</p>
            <form className="site-settings" onSubmit={createSite}>
              <nav className="site-settings-nav" aria-label="Site settings sections">
                <button
                  type="button"
                  className={siteSection === 'details' ? 'active' : ''}
                  onClick={() => setSiteSection('details')}
                >
                  <Settings2 size={16} /> {i.navDetails}
                </button>
                <button
                  type="button"
                  className={siteSection === 'location' ? 'active' : ''}
                  onClick={() => setSiteSection('location')}
                >
                  <MapPin size={16} /> {i.navLocation}
                </button>
                <button
                  type="button"
                  className={siteSection === 'channels' ? 'active' : ''}
                  onClick={() => setSiteSection('channels')}
                >
                  <MessageSquare size={16} /> {i.navChannels}
                </button>
              </nav>

              <div className="site-settings-content">
                {siteSection === 'details' && (
                  <div className="site-settings-section">
                    <h2>{i.navDetails}</h2>
                    <p>{i.detailsIntro}</p>
                    <div className="site-form-fields">
                      <label>
                        <span>{i.siteName}</span>
                        <input value={siteForm.name} onChange={e => setSiteForm({ ...siteForm, name: e.target.value })} placeholder={i.siteName} required />
                      </label>
                      <label>
                        <span>{i.labelWorkload}</span>
                        <select value={siteForm.workload} onChange={e => setSiteForm({ ...siteForm, workload: e.target.value })}>
                          <option value="LIGHT">{i.workloadLight}</option>
                          <option value="MODERATE">{i.workloadMedium}</option>
                          <option value="HEAVY">{i.workloadHeavy}</option>
                        </select>
                      </label>
                      <label>
                        <span>{i.labelExperience}</span>
                        <select value={siteForm.acclimatization} onChange={e => setSiteForm({ ...siteForm, acclimatization: e.target.value })}>
                          <option value="ACCLIMATIZED">{i.experienceAcclimatized}</option>
                          <option value="NEW_OR_UNKNOWN">{i.experienceNew}</option>
                        </select>
                      </label>
                      <label>
                        <span>{i.labelClothing}</span>
                        <select value={siteForm.clothing} onChange={e => setSiteForm({ ...siteForm, clothing: e.target.value })}>
                          <option value="NORMAL">{i.clothingRegular}</option>
                          <option value="HEAVY_OR_PPE">{i.clothingPpe}</option>
                        </select>
                      </label>
                    </div>
                  </div>
                )}

                {siteSection === 'location' && (
                  <div className="site-settings-section">
                    <h2>{i.navLocation}</h2>
                    <p>{i.locationIntro}</p>
                    <LocationPicker
                      lang={language}
                      latitude={siteForm.latitude}
                      longitude={siteForm.longitude}
                      onChange={(lat, lon) => setSiteForm({ ...siteForm, latitude: lat, longitude: lon })}
                    />
                  </div>
                )}

                {siteSection === 'channels' && (
                  <div className="site-settings-section">
                    <h2>{i.navChannels}</h2>
                    <p>{i.channelsIntro}</p>

                    <div className="channel-setup-note">
                      <Info size={16} />
                      <p>{i.channelsNote}</p>
                    </div>

                    <div className="site-form-fields stacked">
                      <label>
                        <span>{i.labelTelegramBot}</span>
                        <div className="input-prefix">
                          <span>t.me/</span>
                          <input
                            value={siteForm.telegramBotUsername}
                            onChange={e => setSiteForm({ ...siteForm, telegramBotUsername: e.target.value.replace(/^@/, '') })}
                            placeholder="HeatShieldBot"
                            pattern="[A-Za-z0-9_]{3,32}"
                          />
                        </div>
                        <div className="channel-setup-steps">
                          <strong>{i.telegramStepsTitle}</strong>
                          <ol>
                            {[i.telegramStep1, i.telegramStep2, i.telegramStep3, i.telegramStep4].map(step => (
                              <li key={step}><Inline text={step} /></li>
                            ))}
                          </ol>
                        </div>
                      </label>
                      <label>
                        <span>{i.labelDiscordInvite}</span>
                        <input
                          value={siteForm.discordInviteUrl}
                          onChange={e => setSiteForm({ ...siteForm, discordInviteUrl: e.target.value.trim() })}
                          placeholder="https://discord.gg/heatshield"
                          type="url"
                          pattern="https://(discord\.gg|(www\.)?discord\.com|discordapp\.com)/.+"
                          title={i.discordInviteHint}
                        />
                        <div className="channel-setup-steps">
                          <strong>{i.discordStepsTitle}</strong>
                          <ol>
                            {[i.discordStep1, i.discordStep2, i.discordStep3, i.discordStep4].map(step => (
                              <li key={step}><Inline text={step} /></li>
                            ))}
                          </ol>
                          <a href="https://discord.com/developers/applications" target="_blank" rel="noopener noreferrer">
                            {i.discordPortalLink} <ExternalLink size={11} />
                          </a>
                        </div>
                      </label>
                    </div>
                  </div>
                )}
              </div>

              <div className="site-settings-footer">
                {siteFormError && <span className="site-settings-error" role="alert">{siteFormError}</span>}
                <button type="submit" className="primary">{editingExistingSite ? i.saveSite : i.createSite}</button>
              </div>
            </form>
          </section>
        </main>
      ) : (
      <main>
        {/* Intro */}
        <section className="intro">
          <div>
            <p className="eyebrow">{todayString()} · 06:00 AM PLAN</p>
            <h1>{user?.fullName ? `Good morning, ${user.fullName.split(' ')[0]}.` : i.greeting}</h1>
            <p className="subtitle">{i.subtitle}</p>
          </div>
          <button type="button" className="location" onClick={openSiteForm}>
            <MapPin size={17} /> {data.forecast?.location || 'Hyderabad, Telangana'} <ChevronRight size={16} />
          </button>
        </section>

        {/* Hero grid */}
        <section className="hero-grid">
          <div className={`risk-card ${data.level}`}>
            <div className="card-label">
              <span>{i.overallHeatRisk}</span>
              <span className="status-pill"><i /> {data.level.toUpperCase()}</span>
            </div>
            <div className="risk-body">
              <div className="gauge" role="img" aria-label={`${i.overallHeatRisk}: ${data.peak.risk} of 100, ${data.level}`}>
                <ResponsiveContainer width="100%" height="100%">
                  <RadialBarChart
                    cx="50%" cy="50%"
                    innerRadius="74%" outerRadius="100%"
                    startAngle={225} endAngle={-45}
                    data={[{ value: data.peak.risk, fill: BAND_COLOR[data.level] || '#e2a53a' }]}
                    barSize={11}
                  >
                    <PolarAngleAxis type="number" domain={[0, 100]} angleAxisId={0} tick={false} />
                    <RadialBar background={{ fill: 'rgba(255,255,255,.1)' }} dataKey="value" cornerRadius={6} isAnimationActive={false} />
                  </RadialBarChart>
                </ResponsiveContainer>
                <div className="gauge-value">{data.peak.risk}<small>/100</small></div>
              </div>
              <div className="risk-copy">
                <p>{i.peakRiskAt(data.peak.time)}</p>
                <div className="scale">
                  <div
                    className="scale-track"
                    role="progressbar"
                    aria-valuenow={data.peak.risk}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={i.overallHeatRisk}
                  >
                    <span className="scale-dot" style={{ left: `${data.peak.risk}%` }} />
                  </div>
                  <div className="scale-labels"><span>LOW</span><span>MOD</span><span>HIGH</span><span>EXT</span></div>
                </div>
              </div>
            </div>
          </div>

          <div className="metric-card temp">
            <div className="metric-icon orange"><ThermometerSun size={22} /></div>
            <span>{i.forecastFor(data.current.time)}</span>
            <strong>{data.current.temperature}°C</strong>
            <p>{data.current.apparentTemperature != null ? `${i.feelsLike(data.current.apparentTemperature)} · ` : ''}{i.humidity(data.current.humidity)}</p>
            <div className="mini-trend">
              {trend && (
                <span>{`${trend.delta >= 0 ? '+' : ''}${trend.delta.toFixed(1)}°C in ${trend.hours}h`}{trend.delta > 0 ? ` (${i.rising})` : ''}</span>
              )}
              <div>
                {hourly.slice(nowIndex, nowIndex + 6).map((h, idx) => {
                  const height = Math.max(6, Math.min(24, Math.round(((h.temperature - 20) / 25) * 24)))
                  return <i key={h.time || idx} style={{ height: `${height}px` }} title={`${h.time}: ${h.temperature}°C (Risk: ${h.risk})`} />
                })}
              </div>
            </div>
          </div>

          <div className="metric-card hydration">
            <div className="metric-icon blue"><Droplets size={22} /></div>
            <span>{i.hydrationTarget}</span>
            <strong>{waterEvery != null ? i.everyMinutes(waterEvery) : '—'}</strong>
            <p>{restMinutes > 0 ? i.restPerHour(restMinutes) : waterEvery != null ? i.hydrationInterval(waterEvery) : ''}</p>
            <div className="water-dots" aria-hidden="true">
              {Array.from({ length: 8 }).map((_, idx) => (
                <i
                  key={idx}
                  style={{ background: idx < Math.min(8, Math.max(3, Math.round(120 / (waterEvery || 30)))) ? '#51b9e8' : '#d7eaf3' }}
                />
              ))}
            </div>
          </div>
        </section>

        {/* Content grid */}
        <section className="content-grid">
          {/* Forecast panel */}
          <div className="panel forecast">
            <div className="panel-head">
              <div><h2>{i.hourlyForecast}</h2><p>{i.hourlySubtitle}</p></div>
              <div className="legend"><i /> {i.heatRisk} <i className="temp" /> {i.temperature}</div>
            </div>
            {replay && <div className="replay-banner">{i.replayBanner}</div>}
            <div className="chart">
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={data.forecast.hourly}>
                  <defs>
                    <linearGradient id="risk" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#bb4a1f" stopOpacity={0.3} />
                      <stop offset="100%" stopColor="#bb4a1f" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" tickLine={false} axisLine={false} tick={{ fill: '#8c8576', fontSize: 11, fontFamily: 'JetBrains Mono' }} />
                  <YAxis yAxisId="risk" domain={[0, 100]} hide />
                  <YAxis yAxisId="temp" orientation="right" domain={[15, 48]} hide />
                  <Tooltip contentStyle={{ background: 'var(--panel)', border: '1px solid var(--panel-border)', borderRadius: 6, boxShadow: '0 8px 24px #12151b1a', fontFamily: 'DM Sans' }} labelStyle={{ color: 'var(--text)' }} />
                  <Area yAxisId="risk" type="monotone" dataKey="risk" stroke="#bb4a1f" strokeWidth={3} fill="url(#risk)" name={i.heatRisk} />
                  <Line yAxisId="temp" type="monotone" dataKey="temperature" stroke="#5b8bd6" strokeWidth={2.5} dot={{ r: 3, fill: '#5b8bd6' }} name={`${i.temperature} (°C)`} />
                </AreaChart>
              </ResponsiveContainer>
            </div>

            <div className="hour-row">
              {data.forecast.hourly.filter((_, idx) => idx % 2 === 0).map(h => (
                <div key={h.time}>
                  <b>{h.time}</b>
                  <span>{h.temperature}°</span>
                  <small className={h.risk > 80 ? 'danger' : h.risk > 55 ? 'warn' : ''}>
                    {h.risk > 80 ? i.extreme : h.risk > 55 ? i.high : i.safe}
                  </small>
                </div>
              ))}
            </div>
            <div className="source"><Activity size={14} /> {i.source}: {data.forecast.source} · {i.updatedJustNow}</div>
          </div>

          {/* Plan panel */}
          <div className="panel plan">
            <div className="panel-head">
              <div><h2>{activePlan.title}</h2><p>{i.personalizedForOutdoor}</p></div>
              <div className="ai-badge"><Sun size={14} /> {planStatus}</div>
            </div>
            <div className="plan-callout">
              <div className="sun-disc"><Sun size={24} /></div>
              <div>
                <strong>{activePlan.message}</strong>
                <span>{i.disclaimer}</span>
              </div>
            </div>
            <div className="checklist">
              {activePlan.checklist.map(item => (
                <div key={item}><CheckCircle2 size={19} /><span>{item}</span></div>
              ))}
            </div>
            <div className="language-row">
              <span>{i.messageLanguage}</span>
              <div className="languages">
                {([['en', 'English'], ['hi', 'हिन्दी'], ['te', 'తెలుగు']] as const).map(([code, label]) => (
                  <button className={language === code ? 'selected' : ''} onClick={() => setLanguage(code as Lang)} key={code}>{label}</button>
                ))}
              </div>
            </div>
            <div className="workflow-actions">
              <button className="secondary" onClick={() => { setReplay(!replay); setReplayPlan(null) }}>
                {replay ? i.useLiveForecast : i.runHistoricalReplay}
              </button>
              <button className="primary" onClick={generate}>{i.generatePlan} <ChevronRight size={17} /></button>
              <button className="secondary" onClick={approve}>{i.approveDraft}</button>
              <button className="secondary" onClick={() => setShowEmailEditor(!showEmailEditor)}>
                {showEmailEditor ? i.emailHide : i.emailCustomize}
              </button>
              <button className="primary" onClick={send}>{i.sendToCrew}</button>
            </div>
            {showEmailEditor && (
              <div className="email-editor">
                <label htmlFor="custom-email-message">{i.emailLabel}</label>
                <textarea
                  id="custom-email-message"
                  value={customEmailMessage}
                  onChange={(e) => setCustomEmailMessage(e.target.value)}
                  placeholder={i.emailPlaceholder}
                />
                <small>{i.emailHelp}</small>
              </div>
            )}
            {message && <small className="feedback">{message}</small>}
          </div>
        </section>

        {/* Crew + Alerts grid */}
        <section className="crew-grid">
          <div className="panel crew-panel">
            <div className="panel-head">
              <div>
                <h2>{i.crewStatus}</h2>
                <p>{onboardChannel === 'telegram' ? i.crewSubtitle : i.crewSubtitleDiscord}</p>
              </div>
              <div className="channel-switch" role="tablist" aria-label="Onboarding Channel">
                <button
                  type="button"
                  className={onboardChannel === 'telegram' ? 'selected' : ''}
                  onClick={() => setOnboardChannel('telegram')}
                  role="tab"
                  aria-selected={onboardChannel === 'telegram'}
                >
                  {i.channelTelegram}
                </button>
                <button
                  type="button"
                  className={onboardChannel === 'discord' ? 'selected discord' : 'discord'}
                  onClick={() => setOnboardChannel('discord')}
                  role="tab"
                  aria-selected={onboardChannel === 'discord'}
                >
                  {i.channelDiscord}
                </button>
              </div>
              <span className="count-badge">{data.workers?.length || 0} {i.joined}</span>
            </div>
            <div className="join-link">
              <a href={joinUrl} target="_blank" rel="noopener noreferrer">{joinUrl}</a>
            </div>
            <div className="qr-wrapper"><QrCode value={joinUrl} size={120} /></div>
            <div className="crew-note">{onboardChannel === 'telegram' ? i.crewNote : i.crewNoteDiscord}</div>
            <div className="crew-live-hint">
              <Info size={13} />
              <span>{i.liveBotHint}</span>
            </div>
          </div>

          <div className="panel alert-panel" ref={alertPanelRef}>
            <div className="panel-head">
              <div><h2>{i.safetyAlerts}</h2><p>{i.alertSubtitle}</p></div>
              <span className="count-badge alert-count">{data.alerts?.filter(a => a.status === 'OPEN').length || 0} {i.open}</span>
            </div>
            <div className="empty-alert">
              {data.alerts?.length ? data.alerts.map(a => (
                <div key={a.alertId} className="alert-row">
                  <div className="alert-info">
                    <span className={`channel-pill ${a.channel || 'telegram'}`}>{a.channel === 'discord' ? 'Discord' : a.channel === 'email' ? 'Email' : 'Telegram'}</span>
                    <span>{i.worker} {a.chatId} · {a.status}</span>
                  </div>
                  {a.status === 'OPEN' && (
                    <button className="resolve-btn" onClick={() => resolveAlert(a.alertId)}>{i.resolve}</button>
                  )}
                </div>
              )) : i.noAlerts}
            </div>
          </div>
        </section>

        {/* Waterlogging watch: needs the live API, so it is not shown over offline demo data */}
        {isLive && data.site?.latitude != null && data.site?.longitude != null && (
          <WaterloggingPanel
            api={API}
            lang={language}
            latitude={data.site.latitude}
            longitude={data.site.longitude}
            canReport={Boolean(user)}
            getToken={getToken}
          />
        )}

        {/* Bottom strip */}
        <section className="bottom-strip">
          <div className="bottom-strip-badge"><ShieldCheck size={20} /></div>
          <div>
            <strong>{i.builtForSafer}</strong>
            <span>{i.turnWeatherInto}</span>
          </div>
          <div className="bottom-strip-divider" />
          <div className="aws-proof">
            <span>{i.poweredBy}</span>
            <b>AWS</b>
            <small>Lambda · Bedrock · DynamoDB</small>
          </div>
        </section>
      </main>
      )}

      <footer>
        <span>{i.footerLeft}</span>
        <span>{i.footerRight}</span>
      </footer>
    </div>
  )
}

const container = document.getElementById('root')!
const w = window as unknown as { __root?: ReturnType<typeof createRoot> }
const root = w.__root ?? createRoot(container)
w.__root = root
root.render(<React.StrictMode><ThemeProvider><AuthProvider><App /></AuthProvider></ThemeProvider></React.StrictMode>)
