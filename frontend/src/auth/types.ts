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

export type AuthContextValue = {
  user: SupervisorUser | null
  isSignedIn: boolean
  isLoaded: boolean
  isClerkEnabled: boolean
  signOut: () => Promise<void> | void
  signIn: () => void
  updateDemoProfile: (updates: Partial<SupervisorUser>) => void
  getToken: () => Promise<string | null>
}
