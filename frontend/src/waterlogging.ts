/** Types and pure helpers for the waterlogging watch, kept free of React so they can be tested directly. */

export type WaterloggingReport = { id: string; at: string; lat: number; lon: number; cellId: string; note: string; reportedBy: string; verified: boolean }
export type FloodAlert = { id: string; name: string; alertLevel: string; lat: number; lon: number; distanceKm: number; url: string | null }
export type MmRange = { min: number; max: number }
/** What the nearest airport weather station reported in the last hour or so. */
export type StationObservation = { stationId: string; stationName: string; distanceKm: number; observedAt: string; reports: number; rainReported: boolean; weather: string | null }
export type LevelThresholds = { MODERATE: number; HIGH: number; VERY_HIGH: number }

export type WaterloggingCell = {
  id: string
  lat: number
  lon: number
  bounds: [[number, number], [number, number]]
  kind: 'ESTIMATE' | 'CONFIRMED'
  risk: string
  forecastRisk: string
  rainfall: null | {
    asOf: string
    lastHourMm: number
    peakLast3hMmPerHour: number
    last6hMm: number
    last24hMm: number
    wetHoursLast24h: number
    forecastNext6hMm: number | null
    forecastNext24hMm: number | null
    /** Whether the station agrees with the modelled last hour. */
    lastHourCheck?: 'SUPPORTED' | 'CONTRADICTED' | 'UNCHECKED'
    modelSpread?: null | { models: number; lastHourMm: MmRange; last24hMm: MmRange; modelsWithNoRainLastHour: number }
  }
  observation?: StationObservation | null
  reference: null | { days: number; accumulation24hMm: LevelThresholds; intensityMmPerHour: LevelThresholds }
  confidence: { score: number; label: 'none' | 'low' | 'medium' | 'high' }
  evidence: string[]
  limitations: string[]
  confirmedReports: WaterloggingReport[]
  unverifiedReports: WaterloggingReport[]
  nearbyAlerts: FloodAlert[]
}

export type WaterloggingData = {
  cells: WaterloggingCell[]
  updatedAt: string
  refreshSeconds: number
  gridKm: number
  focusCellId: string
  reports: WaterloggingReport[]
  sources: Array<{ id: string; name: string; observes: string; licence: string; status: null | { ok: boolean; checkedAt: string; detail: string } }>
}

const COLOURS: Record<string, string> = {
  LOW: '#13805f',
  MODERATE: '#d9a21b',
  HIGH: '#e2711d',
  VERY_HIGH: '#c0271f',
  CONFIRMED: '#6d3fd1',
  INSUFFICIENT_DATA: '#8c8576',
}

const LEVEL_KEYS = {
  LOW: 'wlLow',
  MODERATE: 'wlModerate',
  HIGH: 'wlHigh',
  VERY_HIGH: 'wlVeryHigh',
  CONFIRMED: 'wlConfirmed',
  INSUFFICIENT_DATA: 'wlInsufficient',
} as const

/** The translation key for a risk level; anything unrecognised reads as "Insufficient data", never as a risk. */
export function levelKey(risk: string): (typeof LEVEL_KEYS)[keyof typeof LEVEL_KEYS] {
  return LEVEL_KEYS[risk as keyof typeof LEVEL_KEYS] ?? 'wlInsufficient'
}

/** Map style for a grid cell. Cells without enough data are drawn dashed and faint so they cannot be read as "safe". */
export function cellStyle(risk: string, selected: boolean) {
  const known = risk in COLOURS && risk !== 'INSUFFICIENT_DATA'
  const colour = COLOURS[risk] ?? COLOURS.INSUFFICIENT_DATA
  return {
    color: selected ? '#12151b' : colour,
    weight: selected ? 3 : 1.5,
    fillColor: colour,
    fillOpacity: known ? (risk === 'LOW' ? 0.18 : 0.42) : 0.08,
    dashArray: known ? undefined : '5 5',
  }
}

/** Rainfall for display. A missing value is shown as a dash, never as 0 mm. */
export function formatMm(value: number | null | undefined): string {
  return value == null ? '—' : `${value.toFixed(1)} mm`
}

/** What to show for "last hour": a station's observation outranks the model whenever the two disagree. */
export function lastHourReading(cell: Pick<WaterloggingCell, 'rainfall' | 'observation'>): 'NO_RAIN_OBSERVED' | 'RAIN_OBSERVED' | 'MODEL' {
  if (cell.rainfall?.lastHourCheck !== 'CONTRADICTED' || !cell.observation) return 'MODEL'
  return cell.observation.rainReported ? 'RAIN_OBSERVED' : 'NO_RAIN_OBSERVED'
}

/** A model range worth showing: only when the models actually differ. */
export function spreadText(range: MmRange | undefined): string | null {
  return range && range.min !== range.max ? `${range.min.toFixed(1)}–${range.max.toFixed(1)} mm` : null
}

/** How many cells deserve the warning banner: high or very high estimates, and confirmed reports. */
export function warningCount(cells: WaterloggingCell[]): number {
  return cells.filter(c => c.risk === 'HIGH' || c.risk === 'VERY_HIGH' || c.risk === 'CONFIRMED').length
}
