export type SupervisorUser = {
  id: string
  fullName: string
  email: string
  role: string
  organization: string
  phone?: string
  avatarUrl?: string
  certifications: string[]
  assignedSites: string[]
  isDemo: boolean
}

export type LoginDetails = { fullName: string; email: string }

export type AuthContextValue = {
  user: SupervisorUser | null
  isSignedIn: boolean
  isLoaded: boolean
  isClerkEnabled: boolean
  signOut: () => Promise<void> | void
  /** Opens the sign-in flow. Demo mode accepts entered credentials directly; Clerk mode ignores them and opens its own form. */
  signIn: (details?: LoginDetails) => void
  signUp: () => void
  updateDemoProfile: (updates: Partial<SupervisorUser>) => void
  getToken: () => Promise<string | null>
}
