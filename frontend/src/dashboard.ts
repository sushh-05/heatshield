/** Pure helpers for the dashboard, kept free of React so they can be tested directly. */

export type ForecastHour = { time: string; temperature: number }

function hourOf(time: string): number {
  return Number.parseInt(time.slice(0, 2), 10)
}

/** Position of the "current" hour within the day's forecast; 0 when it cannot be found. */
export function currentIndex(hourly: ForecastHour[], currentTime: string): number {
  const index = hourly.findIndex(h => h.time === currentTime)
  return index >= 0 ? index : 0
}

/**
 * Temperature change from the current hour to the first forecast hour at least
 * two hours later. Null when the forecast does not reach that far, so the UI
 * never labels a made-up interval.
 */
export function temperatureTrend(hourly: ForecastHour[], currentTime: string): { delta: number; hours: number } | null {
  const start = currentIndex(hourly, currentTime)
  const now = hourly[start]
  if (!now) return null
  const later = hourly.slice(start + 1).find(h => hourOf(h.time) - hourOf(now.time) >= 2)
  if (!later) return null
  return { delta: later.temperature - now.temperature, hours: hourOf(later.time) - hourOf(now.time) }
}

/** True only for well-formed https:// links, so stored text is never rendered as a script URL. */
export function isHttpsUrl(value: string | undefined | null): value is string {
  if (!value) return false
  try {
    return new URL(value).protocol === 'https:'
  } catch {
    return false
  }
}

/** Splits text on `backticks` into alternating plain and code segments (odd indexes are code). */
export function codeSegments(text: string): string[] {
  return text.split('`')
}

export type RiskBand = 'low' | 'moderate' | 'high' | 'extreme' | 'unknown'

/** The server's risk level as a display band. Anything missing or unrecognised is "unknown", never "low". */
export function riskBand(level: string | undefined | null): RiskBand {
  const value = String(level || '').toLowerCase()
  return value === 'low' || value === 'moderate' || value === 'high' || value === 'extreme' ? value : 'unknown'
}

export type SlackState = 'sent' | 'failed' | 'configured' | 'missing' | 'unknown'

/**
 * What to say about Slack for the plan on screen. A delivery result only counts for the plan it
 * belongs to, and an unreachable server means "unknown", not "not set up".
 */
export function slackState(hasWebhook: boolean | undefined, delivery: { planId: string; slack: number; slackFailed: number } | null, planId: string | undefined): SlackState {
  if (delivery && planId && delivery.planId === planId) {
    if (delivery.slack > 0) return 'sent'
    if (delivery.slackFailed > 0) return 'failed'
  }
  if (hasWebhook === undefined) return 'unknown'
  return hasWebhook ? 'configured' : 'missing'
}

function validZone(timeZone: string | undefined): string | undefined {
  if (!timeZone) return undefined
  try { new Intl.DateTimeFormat('en', { timeZone }); return timeZone } catch { return undefined }
}

/** Clock time of an ISO timestamp in the site's timezone, in the reader's language; "—" for a bad timestamp. */
export function clockIn(iso: string | undefined, language: string, timeZone?: string): string {
  const date = new Date(iso || '')
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit', timeZone: validZone(timeZone), timeZoneName: 'short' }).format(date)
}

/** Today's date where the site is, written out in the reader's language. */
export function longDate(date: Date, language: string, timeZone?: string): string {
  return new Intl.DateTimeFormat(language, { weekday: 'long', day: '2-digit', month: 'long', year: 'numeric', timeZone: validZone(timeZone) }).format(date)
}

/**
 * A sensible default message language for a site, from its coordinates alone — a starting point
 * the supervisor can always override, not a claim about who lives there. Telangana and Andhra
 * Pradesh (roughly) default to Telugu; the rest of India defaults to Hindi; anywhere else, English,
 * since those are the only three languages HeatShield's messages are translated into.
 */
export function guessLanguage(lat: number, lon: number): 'en' | 'hi' | 'te' {
  const inBox = (box: { lat: [number, number]; lon: [number, number] }) =>
    lat >= box.lat[0] && lat <= box.lat[1] && lon >= box.lon[0] && lon <= box.lon[1]
  if (inBox({ lat: [12.5, 19.95], lon: [76.5, 84.8] })) return 'te'
  if (inBox({ lat: [6, 37.5], lon: [68, 97.5] })) return 'hi'
  return 'en'
}
