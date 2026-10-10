import React, { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { ClerkProvider, useAuth, useClerk, useUser } from '@clerk/react'
import type { AuthContextValue, SupervisorUser } from './types'

export const CLERK_PUBLISHABLE_KEY = (import.meta.env.VITE_CLERK_PUBLISHABLE_KEY || '').trim()
const IS_CLERK_CONFIGURED = Boolean(CLERK_PUBLISHABLE_KEY && CLERK_PUBLISHABLE_KEY.startsWith('pk_'))

const DEFAULT_DEMO_SUPERVISOR: SupervisorUser = {
  id: 'demo_sup_01',
  fullName: 'John Doe',
  email: 'john.doe@heatshield.gov.in',
  role: 'Field Safety Supervisor',
  organization: 'Apex Infra & Field Operations',
  phone: '+91 98765 43210',
  certifications: [
    'OSHA 30-Hour Construction Safety',
    'National Heat Action Plan Certified',
    'First Aid & Heat Stroke Triage',
  ],
  assignedSites: ['demo', 'hyd-metro-p4'],
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
  const { signOut, openSignIn } = useClerk()
  const { getToken, isSignedIn } = useAuth()

  const supervisorUser = useMemo<SupervisorUser | null>(() => {
    if (!clerkUser) return null
    return {
      id: clerkUser.id,
      fullName: clerkUser.fullName || clerkUser.username || 'Supervisor',
      email: clerkUser.primaryEmailAddress?.emailAddress || `${clerkUser.id}@clerk.user`,
      role: 'Field Safety Supervisor',
      organization: 'Apex Infra & Field Operations',
      avatarUrl: clerkUser.imageUrl,
      certifications: [
        'OSHA 30-Hour Construction Safety',
        'Clerk Authenticated Supervisor',
        'First Aid & Heat Stroke Triage',
      ],
      assignedSites: ['demo'],
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
    signIn: () => {
      openSignIn()
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
  }), [supervisorUser, isSignedIn, isLoaded, signOut, openSignIn, getToken])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

/* ── Demo Fallback Provider (Active when no Clerk key is set) ── */
function DemoAuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<SupervisorUser>(() => {
    const saved = localStorage.getItem('heatshield_demo_supervisor')
    if (saved) {
      try {
        return { ...DEFAULT_DEMO_SUPERVISOR, ...JSON.parse(saved) }
      } catch {
        // ignore json parse error
      }
    }
    return DEFAULT_DEMO_SUPERVISOR
  })
  const [isSignedIn, setIsSignedIn] = useState(true)

  useEffect(() => {
    try {
      localStorage.setItem('heatshield_demo_supervisor', JSON.stringify(user))
    } catch {
      // storage unavailable
    }
  }, [user])

  const value: AuthContextValue = useMemo(() => ({
    user: isSignedIn ? user : null,
    isSignedIn,
    isLoaded: true,
    isClerkEnabled: false,
    signOut: () => {
      setIsSignedIn(false)
    },
    signIn: () => {
      setIsSignedIn(true)
    },
    updateDemoProfile: (updates: Partial<SupervisorUser>) => {
      setUser(prev => ({ ...prev, ...updates }))
    },
    getToken: async () => {
      return 'demo_supervisor_jwt_token'
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
