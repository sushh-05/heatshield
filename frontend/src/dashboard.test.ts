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

import { clockIn, riskBand, slackState } from './dashboard'

describe('riskBand', () => {
  it('follows the level the server sends', () => {
    expect(['LOW', 'MODERATE', 'HIGH', 'EXTREME'].map(riskBand)).toEqual(['low', 'moderate', 'high', 'extreme'])
  })

  it('never reads a missing or unrecognised level as low', () => {
    for (const level of [undefined, null, '', 'SAFE', 'NaN']) expect(riskBand(level)).toBe('unknown')
  })
})

describe('slackState', () => {
  const sent = { planId: 'site-2026-10-11', slack: 1, slackFailed: 0 }

  it('reports a delivery only for the plan it belongs to', () => {
    expect(slackState(true, sent, 'site-2026-10-11')).toBe('sent')
    expect(slackState(true, sent, 'site-2026-10-12')).toBe('configured')
    expect(slackState(true, sent, 'other-2026-10-11')).toBe('configured')
  })

  it('shows a failed send as failed, not as ready', () => {
    expect(slackState(true, { planId: 'p', slack: 0, slackFailed: 1 }, 'p')).toBe('failed')
  })

  it('does not claim Slack is missing when the server could not be asked', () => {
    expect(slackState(undefined, null, undefined)).toBe('unknown')
    expect(slackState(false, null, 'p')).toBe('missing')
  })
})

describe('clockIn', () => {
  it('shows the time where the site is, and a dash for a bad timestamp', () => {
    expect(clockIn('2026-10-11T00:30:00Z', 'en', 'Asia/Kolkata')).toContain('06:00')
    expect(clockIn('not a date', 'en', 'Asia/Kolkata')).toBe('—')
    expect(clockIn('2026-10-11T00:30:00Z', 'en', 'Not/AZone')).not.toBe('—')
  })
})

import { guessLanguage } from './dashboard'

describe('guessLanguage', () => {
  it('defaults Telugu-speaking areas to Telugu', () => {
    expect(guessLanguage(17.385, 78.487)).toBe('te') // Hyderabad
    expect(guessLanguage(16.5, 80.6)).toBe('te') // Vijayawada
  })

  it('defaults the rest of India to Hindi', () => {
    expect(guessLanguage(28.6, 77.2)).toBe('hi') // Delhi
    expect(guessLanguage(26.8, 80.9)).toBe('hi') // Lucknow
  })

  it('defaults outside India to English', () => {
    expect(guessLanguage(40.7, -74.0)).toBe('en') // New York
    expect(guessLanguage(51.5, -0.1)).toBe('en') // London
  })
})
