import React, { useState } from 'react'
import { ChevronDown, LogIn, Shield, User } from 'lucide-react'
import { useAppAuth } from './AuthContext'
import { SupervisorProfileModal } from './SupervisorProfileModal'
import { t, type Lang } from '../i18n'

type Props = {
  lang: Lang
}

export function AuthHeaderControl({ lang }: Props) {
  const { user, isSignedIn, signIn, isClerkEnabled } = useAppAuth()
  const [modalOpen, setModalOpen] = useState(false)
  const i = t(lang)

  const initials = user?.fullName
    ? user.fullName
        .split(' ')
        .map(w => w[0])
        .join('')
        .slice(0, 2)
        .toUpperCase()
    : 'JD'

  return (
    <>
      {isSignedIn && user ? (
        <button
          className="auth-profile-pill"
          onClick={() => setModalOpen(true)}
          title={i.supervisorProfile}
          aria-label={i.supervisorProfile}
        >
          <div className="auth-avatar-thumb">
            {user.avatarUrl ? (
              <img src={user.avatarUrl} alt={user.fullName} className="auth-avatar-img" />
            ) : (
              <span>{initials}</span>
            )}
            <span className="auth-online-dot" />
          </div>
          <div className="auth-user-labels">
            <span className="auth-user-name">{user.fullName}</span>
            <span className="auth-user-role">{isClerkEnabled ? 'Supervisor · Clerk' : 'Supervisor · Demo'}</span>
          </div>
          <ChevronDown size={14} className="auth-chevron" />
        </button>
      ) : (
        <div className="auth-buttons-group">
          <button
            className="auth-btn-signin"
            onClick={() => {
              if (isClerkEnabled) {
                signIn()
              } else {
                setModalOpen(true)
              }
            }}
          >
            <LogIn size={15} />
            <span>{i.signIn}</span>
          </button>
        </div>
      )}

      <SupervisorProfileModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        lang={lang}
      />
    </>
  )
}
