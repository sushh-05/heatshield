import React, { createContext, useContext, useEffect, useState } from 'react'

export type ThemePref = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'heatshield_theme'
const ORDER: ThemePref[] = ['system', 'light', 'dark']

type ThemeContextValue = {
  theme: ThemePref
  resolvedTheme: 'light' | 'dark'
  setTheme: (t: ThemePref) => void
  cycleTheme: () => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within a ThemeProvider')
  return ctx
}

function readStoredTheme(): ThemePref {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  } catch { /* storage unavailable */ }
  return 'system'
}

function systemPrefersDark(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<ThemePref>(readStoredTheme)
  const [systemDark, setSystemDark] = useState(systemPrefersDark)

  useEffect(() => {
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setSystemDark(mql.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  const resolvedTheme: 'light' | 'dark' = theme === 'system' ? (systemDark ? 'dark' : 'light') : theme

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    root.style.colorScheme = resolvedTheme
  }, [theme, resolvedTheme])

  const setTheme = (t: ThemePref) => {
    setThemeState(t)
    try { localStorage.setItem(STORAGE_KEY, t) } catch { /* storage unavailable */ }
  }

  const cycleTheme = () => setTheme(ORDER[(ORDER.indexOf(theme) + 1) % ORDER.length])

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme, cycleTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}
