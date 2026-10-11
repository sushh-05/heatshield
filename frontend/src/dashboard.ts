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
