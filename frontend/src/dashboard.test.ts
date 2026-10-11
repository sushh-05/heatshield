import { describe, expect, it } from 'vitest'
import { codeSegments, currentIndex, isHttpsUrl, temperatureTrend } from './dashboard'

const hourly = [
  { time: '06:00', temperature: 25 },
  { time: '07:00', temperature: 26.5 },
  { time: '08:00', temperature: 28 },
  { time: '09:00', temperature: 30 },
]

describe('currentIndex', () => {
  it('finds the current hour', () => expect(currentIndex(hourly, '08:00')).toBe(2))
  it('falls back to the first hour', () => expect(currentIndex(hourly, '23:00')).toBe(0))
})

describe('temperatureTrend', () => {
  it('compares against the hour two hours after the current one, not a fixed position', () => {
    expect(temperatureTrend(hourly, '07:00')).toEqual({ delta: 3.5, hours: 2 })
  })
  it('reports the real gap when the forecast is two-hourly', () => {
    const sparse = [{ time: '06:00', temperature: 27 }, { time: '08:00', temperature: 30 }, { time: '10:00', temperature: 34 }]
    expect(temperatureTrend(sparse, '06:00')).toEqual({ delta: 3, hours: 2 })
  })
  it('gives nothing when the forecast does not reach two hours ahead', () => {
    expect(temperatureTrend(hourly, '08:00')).toBeNull()
    expect(temperatureTrend([], '08:00')).toBeNull()
  })
})

describe('isHttpsUrl', () => {
  it('accepts https links', () => expect(isHttpsUrl('https://discord.gg/crew')).toBe(true))
  it('rejects script, http and malformed values', () => {
    for (const bad of ['javascript:alert(1)', 'http://discord.gg/crew', 'discord.gg/crew', '', undefined, null]) {
      expect(isHttpsUrl(bad)).toBe(false)
    }
  })
})

describe('codeSegments', () => {
  it('puts backticked text at odd positions', () => {
    expect(codeSegments('Set `KEY` and redeploy')).toEqual(['Set ', 'KEY', ' and redeploy'])
  })
})
