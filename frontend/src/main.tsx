import React, { useCallback, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { Activity, BellRing, CheckCircle2, ChevronRight, Droplets, MapPin, RefreshCw, ShieldCheck, Sun, ThermometerSun } from 'lucide-react'
import { t, type Lang } from './i18n'
import './styles.css'
import './overrides.css'

/* ── Types ─────────────────────────────────────────────────────── */
type Hour = { time: string; temperature: number; humidity: number; risk: number; level?: string; apparentTemperature?: number }
type PlanView = { title: string; message: string; checklist: string[]; generated_by: string }
type Worker = { chatId: string; name: string; status: string }
type Alert = { alertId: string; status: string; chatId: string }
type Site = { siteId: string; name: string; workload: string; acclimatization: string }
type CanonicalPlan = { status?: string; blocks?: Array<{ rest_minutes_per_hour: number; water_every_minutes: number }> }
type Dashboard = {
  forecast: { location: string; hourly: Hour[]; source: string }
  current: Hour
  peak: Hour
  level: string
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
  plan: {
    title: 'HeatShield morning plan',
    message: 'Peak risk is extreme at 14:00 (91/100).',
    checklist: [
      'Move heavy work before 10:00 or after 17:00.',
      'Drink water every 20 minutes; do not wait until thirsty.',
      'Use a buddy check and stop immediately for dizziness, confusion, or nausea.',
    ],
    generated_by: 'offline demo',
  },
  updated_at: new Date().toISOString(),
}

/* ── QR Code generator (SVG, no deps) ──────────────────────────── */
function QrCode({ value, size = 120 }: { value: string; size?: number }) {
  // Minimal QR-like visual using a Google Charts fallback
  const url = `https://chart.googleapis.com/chart?cht=qr&chs=${size}x${size}&chl=${encodeURIComponent(value)}&choe=UTF-8`
  return <img src={url} alt="QR code" width={size} height={size} style={{ borderRadius: 8, border: '1px solid #e5eaf0' }} />
}

/* ── Helpers ────────────────────────────────────────────────────── */
function todayString() {
  const d = new Date()
  const days = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY', 'SATURDAY']
  const months = ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER', 'DECEMBER']
  return `${days[d.getDay()]}, ${String(d.getDate()).padStart(2, '0')} ${months[d.getMonth()]} ${d.getFullYear()}`
}

function dateSlug() {
  return new Date().toISOString().slice(0, 10)
}

/* ── App ───────────────────────────────────────────────────────── */
function App() {
  const [data, setData] = useState<Dashboard | null>(null)
  const [language, setLanguage] = useState<Lang>('en')
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [plan, setPlan] = useState<PlanView | null>(null)
  const [planStatus, setPlanStatus] = useState('DRAFT')
  const [replay, setReplay] = useState(false)
  const [showSiteForm, setShowSiteForm] = useState(false)
  const [siteForm, setSiteForm] = useState({ name: 'Hyderabad Construction Site', workload: 'MODERATE', acclimatization: 'ACCLIMATIZED', clothing: 'NORMAL', defaultLanguage: 'te' })
  const [siteId, setSiteId] = useState('demo')
  const [isLive, setIsLive] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const i = t(language)

  /* ── Fetch dashboard data ─────────────────────────────────────── */
  const fetchDashboard = useCallback(async (sid?: string) => {
    const id = sid || siteId
    try {
      const r = await fetch(`${API}/api/sites/${id}/status`)
      if (!r.ok) throw new Error('API unavailable')
      const payload: Dashboard = await r.json()
      setData(payload)
      if (payload.canonicalPlan?.status) setPlanStatus(payload.canonicalPlan.status)
      setIsLive(true)
      if (payload.site?.siteId) setSiteId(payload.site.siteId)
    } catch {
      try {
        const r2 = await fetch(`${API}/api/dashboard`)
        if (!r2.ok) throw new Error('fallback failed')
        const payload2: Dashboard = await r2.json()
        setData(payload2)
        if (payload2.canonicalPlan?.status) setPlanStatus(payload2.canonicalPlan.status)
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

  const activePlan = plan || data.plan
  const waterEvery = data.canonicalPlan?.blocks?.[0]?.water_every_minutes ?? 20
  const restMinutes = data.canonicalPlan?.blocks?.[0]?.rest_minutes_per_hour ?? 0
  const apparentTemp = data.current.temperature + 3 // backend doesn't expose apparent_temperature_c to dashboard yet
  const joinUrl = `https://t.me/${import.meta.env.VITE_TELEGRAM_BOT || 'HeatShieldBot'}?start=${siteId}`

  /* ── Actions ──────────────────────────────────────────────────── */
  const generate = async () => {
    setMessage(i.generating)
    try {
      const result = await fetch(`${API}/api/sites/${siteId}/plans/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: replay ? 'REPLAY' : 'LIVE', replayDate: replay ? '2026-05-20' : undefined }),
      })
      const payload = await result.json()
      setPlan(payload.planView)
      setPlanStatus('DRAFT')
      setMessage(replay ? i.historicalGenerated : i.liveGenerated)
    } catch {
      setMessage(i.fallbackPlan)
    }
  }

  const approve = async () => {
    setPlanStatus('APPROVED')
    setMessage(i.approvalRecorded)
    try {
      await fetch(`${API}/api/sites/${siteId}/plans/${dateSlug()}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approvedBy: 'Rakesh' }),
      })
    } catch { /* local state already updated */ }
  }

  const send = async () => {
    if (planStatus !== 'APPROVED') { setMessage(i.approveFirst); return }
    setMessage(i.sendingToCrew)
    try {
      const result = await fetch(`${API}/api/sites/${siteId}/plans/${dateSlug()}/send`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      const payload = await result.json()
      if (result.ok) {
        setPlanStatus('SENT')
        setMessage(i.delivered(payload.delivered ?? 0))
      } else {
        setMessage(payload.error || i.sendFailed)
      }
    } catch {
      setPlanStatus('SENT')
      setMessage(i.sendFailed)
    }
  }

  const resolveAlert = async (alertId: string) => {
    try {
      await fetch(`${API}/api/alerts/${alertId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      })
      await fetchDashboard()
    } catch { /* silent */ }
  }

  const createSite = async (event: React.FormEvent) => {
    event.preventDefault()
    try {
      const result = await fetch(`${API}/api/sites`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(siteForm),
      })
      const payload = await result.json()
      const newId = payload.site.siteId
      setSiteId(newId)
      await fetchDashboard(newId)
      setShowSiteForm(false)
      setMessage(i.siteCreated)
    } catch {
      setMessage(i.siteFormSaved)
      setShowSiteForm(false)
    }
  }

  const manualRefresh = () => { fetchDashboard() }

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
          <span className="live"><i />{isLive ? i.systemOnline : 'OFFLINE'}</span>
          <button className="icon-button" onClick={manualRefresh} title="Refresh"><RefreshCw size={18} /></button>
          <button className="icon-button"><BellRing size={18} /></button>
          <div className="avatar">RK</div>
        </div>
      </header>

      <main>
        {/* Intro */}
        <section className="intro">
          <div>
            <p className="eyebrow">{todayString()} · 06:00 AM PLAN</p>
            <h1>{i.greeting}</h1>
            <p className="subtitle">{i.subtitle}</p>
          </div>
          <div className="location"><MapPin size={17} /> Hyderabad, Telangana <ChevronRight size={16} /></div>
        </section>

        {/* Site form */}
        {showSiteForm && (
          <form className="site-form" onSubmit={createSite}>
            <input value={siteForm.name} onChange={e => setSiteForm({ ...siteForm, name: e.target.value })} placeholder={i.siteName} />
            <select value={siteForm.workload} onChange={e => setSiteForm({ ...siteForm, workload: e.target.value })}>
              <option>LIGHT</option><option>MODERATE</option><option>HEAVY</option>
            </select>
            <select value={siteForm.acclimatization} onChange={e => setSiteForm({ ...siteForm, acclimatization: e.target.value })}>
              <option>ACCLIMATIZED</option><option>NEW_OR_UNKNOWN</option>
            </select>
            <select value={siteForm.clothing} onChange={e => setSiteForm({ ...siteForm, clothing: e.target.value })}>
              <option>NORMAL</option><option>HEAVY_OR_PPE</option>
            </select>
            <button className="primary" type="submit">{i.createSite}</button>
          </form>
        )}
        <button className="site-toggle" onClick={() => setShowSiteForm(!showSiteForm)}>
          {showSiteForm ? i.closeSiteForm : i.createOrEditSite}
        </button>

        {/* Hero grid */}
        <section className="hero-grid">
          <div className={`risk-card ${data.level}`}>
            <div className="card-label">
              <span>{i.overallHeatRisk}</span>
              <span className="status-pill"><i /> {data.level.toUpperCase()}</span>
            </div>
            <div className="risk-number">{data.peak.risk}<small>/100</small></div>
            <p>{i.peakRiskAt(data.peak.time)}</p>
            <div className="meter"><span style={{ width: `${data.peak.risk}%` }} /></div>
            <div className="meter-labels"><span>LOW</span><span>MODERATE</span><span>HIGH</span><span>EXTREME</span></div>
          </div>

          <div className="metric-card">
            <div className="metric-icon orange"><ThermometerSun size={22} /></div>
            <span>{i.now}</span>
            <strong>{data.current.temperature}°C</strong>
            <p>{i.feelsLike(apparentTemp)} · {i.humidity(data.current.humidity)}</p>
            <div className="mini-trend"><span>{i.rising}</span><div><i /><i /><i /><i /><i /><i /></div></div>
          </div>

          <div className="metric-card">
            <div className="metric-icon blue"><Droplets size={22} /></div>
            <span>{i.hydrationTarget}</span>
            <strong>250 ml</strong>
            <p>{i.hydrationInterval(waterEvery)}{restMinutes > 0 ? ` · ${restMinutes}m rest/hr` : ''}</p>
            <div className="water-dots"><i /><i /><i /><i /><i /><i /><i /><i /></div>
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
                      <stop offset="0%" stopColor="#ff8a4c" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#ff8a4c" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="time" tickLine={false} axisLine={false} tick={{ fill: '#87909f', fontSize: 12 }} />
                  <YAxis domain={[0, 100]} hide />
                  <Tooltip contentStyle={{ border: '0', borderRadius: 12, boxShadow: '0 8px 24px #18223520' }} />
                  <Area type="monotone" dataKey="risk" stroke="#f4774e" strokeWidth={3} fill="url(#risk)" />
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
              <button className="secondary" onClick={() => setReplay(!replay)}>
                {replay ? i.useLiveForecast : i.runHistoricalReplay}
              </button>
              <button className="primary" onClick={generate}>{i.generatePlan} <ChevronRight size={17} /></button>
              <button className="secondary" onClick={approve}>{i.approveDraft}</button>
              <button className="primary" onClick={send}>{i.sendToCrew}</button>
            </div>
            {message && <small className="feedback">{message}</small>}
          </div>
        </section>

        {/* Crew + Alerts grid */}
        <section className="crew-grid">
          <div className="panel crew-panel">
            <div className="panel-head">
              <div><h2>{i.crewStatus}</h2><p>{i.crewSubtitle}</p></div>
              <span className="count-badge">{data.workers?.length || 0} {i.joined}</span>
            </div>
            <div className="join-link">{joinUrl}</div>
            <div className="qr-wrapper"><QrCode value={joinUrl} size={120} /></div>
            <div className="crew-note">{i.crewNote}</div>
          </div>

          <div className="panel alert-panel">
            <div className="panel-head">
              <div><h2>{i.safetyAlerts}</h2><p>{i.alertSubtitle}</p></div>
              <span className="count-badge alert-count">{data.alerts?.filter(a => a.status === 'OPEN').length || 0} {i.open}</span>
            </div>
            <div className="empty-alert">
              {data.alerts?.length ? data.alerts.map(a => (
                <div key={a.alertId} className="alert-row">
                  <span>{i.worker} {a.chatId} · {a.status}</span>
                  {a.status === 'OPEN' && (
                    <button className="resolve-btn" onClick={() => resolveAlert(a.alertId)}>{i.resolve}</button>
                  )}
                </div>
              )) : i.noAlerts}
            </div>
          </div>
        </section>

        {/* Bottom strip */}
        <section className="bottom-strip">
          <div>
            <strong><ShieldCheck size={18} /> {i.builtForSafer}</strong>
            <span>{i.turnWeatherInto}</span>
          </div>
          <div className="aws-proof">
            <span>{i.poweredBy}</span>
            <b>AWS</b>
            <small>Lambda · Bedrock · DynamoDB</small>
          </div>
        </section>
      </main>

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
root.render(<React.StrictMode><App /></React.StrictMode>)
