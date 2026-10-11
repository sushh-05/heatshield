import React, { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { ClerkProvider, useAuth, useClerk, useUser } from '@clerk/react'
import type { AuthContextValue, LoginDetails, SupervisorUser } from './types'

export const CLERK_PUBLISHABLE_KEY = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || '').trim()
const IS_CLERK_CONFIGURED = Boolean(CLERK_PUBLISHABLE_KEY && CLERK_PUBLISHABLE_KEY.startsWith('pk_'))

const DEMO_SIGNED_IN_KEY = 'heatshield_demo_signed_in'

const DEFAULT_DEMO_SUPERVISOR: SupervisorUser = {
  id: 'demo_sup_01',
  fullName: 'Field Supervisor',
  email: 'supervisor@heatshield.local',
  role: 'Field Safety Supervisor',
  organization: '',
  certifications: [],
  assignedSites: [],
  isDemo: true,
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useAppAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAppAuth must be used within an AuthProvider')
  }
  return ctx
}

/* ── Clerk Bridge (Active when Clerk key is configured) ───────── */
function ClerkBridge({ children }: { children: React.ReactNode }) {
  const { user: clerkUser, isLoaded } = useUser()
  const { signOut, openSignIn, openSignUp } = useClerk()
  const { getToken, isSignedIn } = useAuth()

  const supervisorUser = useMemo<SupervisorUser | null>(() => {
    if (!clerkUser) return null
    return {
      id: clerkUser.id,
      fullName: clerkUser.fullName || clerkUser.username || 'Supervisor',
      email: clerkUser.primaryEmailAddress?.emailAddress || `${clerkUser.id}@clerk.user`,
      role: 'Field Safety Supervisor',
      organization: '',
      avatarUrl: clerkUser.imageUrl,
      certifications: [],
      assignedSites: [],
      isDemo: false,
    }
  }, [clerkUser])

  const value: AuthContextValue = useMemo(() => ({
    user: supervisorUser,
    isSignedIn: Boolean(isSignedIn),
    isLoaded,
    isClerkEnabled: true,
    signOut: async () => {
      await signOut()
    },
    // Demo credentials are ignored here — Clerk owns its own sign-in form.
    signIn: () => {
      openSignIn()
    },
    signUp: () => {
      openSignUp()
    },
    updateDemoProfile: () => {
      // No-op in live Clerk mode; users edit via Clerk modal
    },
    getToken: async () => {
      try {
        return await getToken()
      } catch {
        return null
      }
    },
  }), [supervisorUser, isSignedIn, isLoaded, signOut, openSignIn, openSignUp, getToken])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/* ── Demo Fallback Provider (Active when no Clerk key is set) ── */
function DemoAuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SupervisorUser>(() => {
    const saved = localStorage.getItem('heatshield_demo_supervisor')
    if (saved) {
      try {
        // Profiles saved by older versions carried placeholder credentials and sites; never restore those.
        return { ...DEFAULT_DEMO_SUPERVISOR, ...JSON.parse(saved), certifications: [], assignedSites: [] }
      } catch {
        // ignore json parse error
      }
    }
    return DEFAULT_DEMO_SUPERVISOR
  })
  // Defaults to signed OUT: the demo mode should require an actual login step,
  // not drop the visitor straight into an authenticated supervisor session.
  const [isSignedIn, setIsSignedIn] = useState(() => {
    try {
      return localStorage.getItem(DEMO_SIGNED_IN_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    try {
      localStorage.setItem('heatshield_demo_supervisor', JSON.stringify(user))
    } catch {
      // storage unavailable
    }
  }, [user])

  useEffect(() => {
    try {
      localStorage.setItem(DEMO_SIGNED_IN_KEY, isSignedIn ? '1' : '0')
    } catch {
      // storage unavailable
    }
  }, [isSignedIn])

  const value: AuthContextValue = useMemo(() => ({
    user: isSignedIn ? user : null,
    isSignedIn,
    isLoaded: true,
    isClerkEnabled: false,
    signOut: () => {
      setIsSignedIn(false)
    },
    signIn: (details?: LoginDetails) => {
      if (details?.fullName || details?.email) {
        setUser(prev => ({
          ...prev,
          fullName: details.fullName?.trim() || prev.fullName,
          email: details.email?.trim() || prev.email,
        }))
      }
      setIsSignedIn(true)
    },
    // Demo mode has no account system to register against; signing up and
    // signing in resolve to the same local session.
    signUp: () => {
      setIsSignedIn(true)
    },
    updateDemoProfile: (updates: Partial<SupervisorUser>) => {
      setUser(prev => ({ ...prev, ...updates }))
    },
    getToken: async () => {
      // Never hand out a token while signed out — callers gate on this to
      // decide whether to attach an Authorization header.
      return isSignedIn ? 'demo_supervisor_jwt_token' : null
    },
  }), [user, isSignedIn])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/* ── Unified AuthProvider Root ───────────────────────────────── */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  if (IS_CLERK_CONFIGURED) {
    return (
      <ClerkProvider publishableKey={CLERK_PUBLISHABLE_KEY}>
        <ClerkBridge>{children}</ClerkBridge>
      </ClerkProvider>
    )
  }

  return <DemoAuthProvider>{children}</DemoAuthProvider>
}
