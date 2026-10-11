import { describe, expect, it } from 'vitest'
import { cellStyle, formatMm, lastHourReading, levelKey, spreadText, warningCount, type WaterloggingCell } from './waterlogging'

const cell = (risk: string) => ({ risk }) as WaterloggingCell

describe('levelKey', () => {
  it('maps every level', () => {
    expect(levelKey('VERY_HIGH')).toBe('wlVeryHigh')
    expect(levelKey('CONFIRMED')).toBe('wlConfirmed')
  })
  it('reads anything unknown as insufficient data, never as a risk level', () => {
    expect(levelKey('SOMETHING_NEW')).toBe('wlInsufficient')
    expect(levelKey('')).toBe('wlInsufficient')
  })
})

describe('cellStyle', () => {
  it('draws cells without data dashed and faint, unlike a low-risk cell', () => {
    const unknown = cellStyle('INSUFFICIENT_DATA', false)
    const low = cellStyle('LOW', false)
    expect(unknown.dashArray).toBeDefined()
    expect(low.dashArray).toBeUndefined()
    expect(unknown.fillOpacity).toBeLessThan(low.fillOpacity)
    expect(unknown.fillColor).not.toBe(low.fillColor)
  })
  it('gives confirmed reports their own colour', () => {
    expect(cellStyle('CONFIRMED', false).fillColor).not.toBe(cellStyle('VERY_HIGH', false).fillColor)
  })
})

describe('formatMm', () => {
  it('shows a dash for missing values instead of zero', () => {
    expect(formatMm(null)).toBe('—')
    expect(formatMm(undefined)).toBe('—')
    expect(formatMm(0)).toBe('0.0 mm')
    expect(formatMm(12.34)).toBe('12.3 mm')
  })
})

describe('warningCount', () => {
  it('counts high, very high and confirmed cells only', () => {
    expect(warningCount(['LOW', 'MODERATE', 'HIGH', 'VERY_HIGH', 'CONFIRMED', 'INSUFFICIENT_DATA'].map(cell))).toBe(3)
  })
})

describe('lastHourReading', () => {
  const station = { stationId: 'VECC', stationName: 'Kolkata', distanceKm: 12, observedAt: '2026-10-11T09:00:00Z', reports: 2, weather: 'HZ' }
  const rain = (lastHourCheck: 'SUPPORTED' | 'CONTRADICTED' | 'UNCHECKED') => ({ lastHourMm: 0.2, lastHourCheck }) as NonNullable<WaterloggingCell['rainfall']>

  it('shows the station, not the model, when the station contradicts it', () => {
    expect(lastHourReading({ rainfall: rain('CONTRADICTED'), observation: { ...station, rainReported: false } })).toBe('NO_RAIN_OBSERVED')
    expect(lastHourReading({ rainfall: rain('CONTRADICTED'), observation: { ...station, rainReported: true } })).toBe('RAIN_OBSERVED')
  })
  it('shows the model when the station agrees or there is no station', () => {
    expect(lastHourReading({ rainfall: rain('SUPPORTED'), observation: { ...station, rainReported: true } })).toBe('MODEL')
    expect(lastHourReading({ rainfall: rain('UNCHECKED'), observation: null })).toBe('MODEL')
  })
})

describe('spreadText', () => {
  it('is shown only when the models differ', () => {
    expect(spreadText({ min: 0, max: 0.7 })).toBe('0.0–0.7 mm')
    expect(spreadText({ min: 0.2, max: 0.2 })).toBeNull()
    expect(spreadText(undefined)).toBeNull()
  })
})
