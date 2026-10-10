import React, { useState } from 'react'
import { Award, CheckCircle2, KeyRound, LogIn, LogOut, Radio, Shield, User, X } from 'lucide-react'
import { useClerk } from '@clerk/react'
import { useAppAuth } from './AuthContext'
import { t, type Lang } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  lang: Lang
}

export function SupervisorProfileModal({ isOpen, onClose, lang }: Props) {
  const { user, isSignedIn, isClerkEnabled, signOut, signIn, updateDemoProfile } = useAppAuth()
  const clerk = isClerkEnabled ? useClerk() : null
  const i = t(lang)

  const [isEditing, setIsEditing] = useState(false)
  const [name, setName] = useState(user?.fullName || '')
  const [email, setEmail] = useState(user?.email || '')
  const [org, setOrg] = useState(user?.organization || '')
  const [phone, setPhone] = useState(user?.phone || '')

  if (!isOpen) return null

  const handleSave = (e: React.FormEvent) => {
    e.preventDefault()
    updateDemoProfile({
      fullName: name,
      email,
      organization: org,
      phone,
    })
    setIsEditing(false)
  }

  const initials = user?.fullName
    ? user.fullName
        .split(' ')
        .map(w => w[0])
        .join('')
        .slice(0, 2)
        .toUpperCase()
    : 'JD'

  return (
    <div className="profile-modal-backdrop" onClick={onClose} role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div className="profile-modal-card" onClick={e => e.stopPropagation()}>
        <div className="profile-modal-header">
          <div className="profile-modal-title-group">
            <Shield size={20} className="profile-accent-icon" />
            <h2 id="modal-title">{i.supervisorProfile}</h2>
          </div>
          <button className="profile-close-btn" onClick={onClose} aria-label="Close modal">
            <X size={18} />
          </button>
        </div>

        {isSignedIn && user ? (
          <div className="profile-modal-body">
            {/* User identity card */}
            <div className="profile-identity-card">
              <div className="profile-avatar-wrap">
                {user.avatarUrl ? (
                  <img src={user.avatarUrl} alt={user.fullName} className="profile-avatar-img" />
                ) : (
                  <div className="profile-avatar-fallback">{initials}</div>
                )}
                <span className="profile-status-indicator" title="Active on shift" />
              </div>
              <div className="profile-identity-info">
                <h3>{user.fullName}</h3>
                <p className="profile-email">{user.email}</p>
                <div className="profile-badges-row">
                  <span className="profile-badge role">{user.role}</span>
                  <span className="profile-badge verified">
                    <CheckCircle2 size={12} /> {i.oshaCertified}
                  </span>
                </div>
              </div>
            </div>

            {/* Clerk status banner */}
            <div className={`profile-auth-status-banner ${isClerkEnabled ? 'clerk-active' : 'demo-active'}`}>
              <div className="auth-status-icon">
                <KeyRound size={16} />
              </div>
              <div className="auth-status-text">
                <strong>{isClerkEnabled ? i.clerkActive : i.demoAuthMode}</strong>
                <p>{isClerkEnabled ? 'Session authenticated via Clerk Identity.' : i.demoAuthHint}</p>
              </div>
            </div>

            {/* Editing mode for demo */}
            {isEditing ? (
              <form onSubmit={handleSave} className="profile-edit-form">
                <div className="form-group">
                  <label htmlFor="sup-name">{i.fullName}</label>
                  <input id="sup-name" value={name} onChange={e => setName(e.target.value)} required />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-email">{i.emailAddress}</label>
                  <input id="sup-email" type="email" value={email} onChange={e => setEmail(e.target.value)} required />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-org">{i.organization}</label>
                  <input id="sup-org" value={org} onChange={e => setOrg(e.target.value)} />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-phone">{i.phone}</label>
                  <input id="sup-phone" value={phone} onChange={e => setPhone(e.target.value)} />
                </div>
                <div className="profile-form-actions">
                  <button type="button" className="btn-secondary" onClick={() => setIsEditing(false)}>
                    {i.cancel}
                  </button>
                  <button type="submit" className="btn-primary">
                    {i.saveProfile}
                  </button>
                </div>
              </form>
            ) : (
              <>
                {/* Credentials & Certifications */}
                <div className="profile-section">
                  <h4>
                    <Award size={15} /> {i.credentialsCertifications}
                  </h4>
                  <ul className="profile-cert-list">
                    {user.certifications.map(cert => (
                      <li key={cert} className="profile-cert-item">
                        <CheckCircle2 size={14} className="cert-check-icon" />
                        <span>{cert}</span>
                        <span className="cert-status-tag">VERIFIED</span>
                      </li>
                    ))}
                  </ul>
                </div>

                {/* Dispatch channels */}
                <div className="profile-section">
                  <h4>
                    <Radio size={15} /> {i.connectedChannels}
                  </h4>
                  <div className="profile-channel-status-grid">
                    <div className="channel-status-card">
                      <div className="channel-status-header">
                        <span className="channel-icon-bullet telegram" />
                        <strong>Telegram Bot</strong>
                      </div>
                      <span className="channel-status-pill online">ONLINE · @HeatShieldBot</span>
                    </div>
                    <div className="channel-status-card">
                      <div className="channel-status-header">
                        <span className="channel-icon-bullet discord" />
                        <strong>Discord Webhook</strong>
                      </div>
                      <span className="channel-status-pill online">ONLINE · #safety-briefings</span>
                    </div>
                  </div>
                </div>

                {/* Assigned Sites */}
                <div className="profile-section">
                  <h4>
                    <User size={15} /> {i.assignedSites}
                  </h4>
                  <div className="profile-sites-list">
                    <div className="profile-site-pill">Hyderabad Construction Site (ID: demo)</div>
                    <div className="profile-site-pill">Metro P4 Elevated Viaduct (ID: hyd-metro-p4)</div>
                  </div>
                </div>
              </>
            )}

            {/* Actions */}
            <div className="profile-modal-footer">
              {isClerkEnabled ? (
                <button
                  type="button"
                  className="btn-secondary profile-footer-btn"
                  onClick={() => clerk?.openUserProfile()}
                >
                  <User size={15} /> {i.manageAccount}
                </button>
              ) : (
                !isEditing && (
                  <button
                    type="button"
                    className="btn-secondary profile-footer-btn"
                    onClick={() => {
                      setName(user.fullName)
                      setEmail(user.email)
                      setOrg(user.organization)
                      setPhone(user.phone || '')
                      setIsEditing(true)
                    }}
                  >
                    {i.editProfile}
                  </button>
                )
              )}

              <button
                type="button"
                className="btn-danger profile-footer-btn"
                onClick={() => {
                  signOut()
                  onClose()
                }}
              >
                <LogOut size={15} /> {i.signOut}
              </button>
            </div>
          </div>
        ) : (
          <div className="profile-signed-out-state">
            <div className="signed-out-icon-wrap">
              <Shield size={36} />
            </div>
            <h3>{i.signIn}</h3>
            <p>{isClerkEnabled ? 'Sign in with your Clerk supervisor credentials to approve plans and dispatch briefings.' : 'You are currently in signed-out demo mode.'}</p>
            <button
              className="btn-primary sign-in-action-btn"
              onClick={() => {
                signIn()
                if (!isClerkEnabled) onClose()
              }}
            >
              <LogIn size={16} /> {i.signIn}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
