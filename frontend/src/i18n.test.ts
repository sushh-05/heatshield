import { describe, expect, it } from 'vitest'
import { LANGUAGES, rawStrings, t } from './i18n'

describe('translations', () => {
  const english = rawStrings('en')

  for (const lang of LANGUAGES.filter(l => l !== 'en')) {
    it(`${lang} has every English string, of the same kind`, () => {
      const table = rawStrings(lang)
      const missing = Object.keys(english).filter(key => typeof table[key] !== typeof english[key])
      expect(missing).toEqual([])
    })

    it(`${lang} has no keys English lacks`, () => {
      expect(Object.keys(rawStrings(lang)).filter(key => !(key in english))).toEqual([])
    })
  }

  it('falls back to English rather than rendering undefined', () => {
    for (const lang of LANGUAGES) {
      const strings = t(lang) as unknown as Record<string, unknown>
      for (const key of Object.keys(english)) expect(strings[key], `${lang}.${key}`).toBeDefined()
    }
  })
})
