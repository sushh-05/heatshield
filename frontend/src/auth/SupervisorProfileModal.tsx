import React, { useState } from 'react'
import { Award, CheckCircle2, KeyRound, LogIn, LogOut, Radio, Shield, User, UserPlus, X } from 'lucide-react'
import { useClerk } from '@clerk/react'
import { useAppAuth } from './AuthContext'
import { t, type Lang } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  lang: Lang
  telegramBotUsername?: string
  discordInviteUrl?: string
  siteName?: string
  siteId?: string
}

/** Only ever mounted inside the ClerkProvider tree (isClerkEnabled === true), so
 *  calling useClerk() here is unconditional — unlike calling it directly in the
 *  parent behind a ternary, which would violate the Rules of Hooks. */
function ClerkManageAccountButton({ label }: { label: string }) {
  const clerk = useClerk()
  return (
    <button type="button" className="btn-secondary profile-footer-btn" onClick={() => clerk.openUserProfile()}>
      <User size={15} /> {label}
    </button>
  )
}

export function SupervisorProfileModal({ isOpen, onClose, lang, telegramBotUsername, discordInviteUrl, siteName, siteId }: Props) {
  const { user, isSignedIn, isClerkEnabled, signOut, signIn, signUp, updateDemoProfile } = useAppAuth()
  const i = t(lang)

  const [isEditing, setIsEditing] = useState(false)
  const [name, setName] = useState(user?.fullName || '')
  const [email, setEmail] = useState(user?.email || '')
  const [org, setOrg] = useState(user?.organization || '')
  const [phone, setPhone] = useState(user?.phone || '')
  const [loginName, setLoginName] = useState('')
  const [loginEmail, setLoginEmail] = useState('')

  if (!isOpen) return null

  const initials = user?.fullName
    ? user.fullName
        .split(' ')
        .map(w => w[0])
        .join('')
        .slice(0, 2)
        .toUpperCase()
    : '?'

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

            {/* Editing mode for demo — every field auto-saves (to localStorage via
                updateDemoProfile) as soon as it changes, so there's nothing to lose
                by navigating away without pressing a "Save" button. */}
            {isEditing ? (
              <div className="profile-edit-form">
                <div className="form-group">
                  <label htmlFor="sup-name">{i.fullName}</label>
                  <input
                    id="sup-name"
                    value={name}
                    onChange={e => { const v = e.target.value; setName(v); updateDemoProfile({ fullName: v }) }}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-email">{i.emailAddress}</label>
                  <input
                    id="sup-email"
                    type="email"
                    value={email}
                    onChange={e => { const v = e.target.value; setEmail(v); updateDemoProfile({ email: v }) }}
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-org">{i.organization}</label>
                  <input
                    id="sup-org"
                    value={org}
                    onChange={e => { const v = e.target.value; setOrg(v); updateDemoProfile({ organization: v }) }}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="sup-phone">{i.phone}</label>
                  <input
                    id="sup-phone"
                    value={phone}
                    onChange={e => { const v = e.target.value; setPhone(v); updateDemoProfile({ phone: v }) }}
                  />
                </div>
                <div className="profile-form-actions">
                  <button type="button" className="btn-primary" onClick={() => setIsEditing(false)}>
                    {i.doneEditing}
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* Certifications are listed only when the account actually carries some; nothing is marked verified here. */}
                {user.certifications.length > 0 && (
                  <div className="profile-section">
                    <h4>
                      <Award size={15} /> {i.credentialsCertifications}
                    </h4>
                    <ul className="profile-cert-list">
                      {user.certifications.map(cert => (
                        <li key={cert} className="profile-cert-item">
                          <CheckCircle2 size={14} className="cert-check-icon" />
                          <span>{cert}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}

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
                      <span className="channel-status-pill online">@{telegramBotUsername || 'HeatShieldBot'}</span>
                    </div>
                    <div className="channel-status-card">
                      <div className="channel-status-header">
                        <span className="channel-icon-bullet discord" />
                        <strong>Discord Server</strong>
                      </div>
                      <span className="channel-status-pill online">{(discordInviteUrl || 'https://discord.gg/heatshield').replace(/^https?:\/\//, '')}</span>
                    </div>
                  </div>
                </div>

                {/* The site currently open on the dashboard */}
                {siteName && (
                  <div className="profile-section">
                    <h4>
                      <User size={15} /> {i.assignedSites}
                    </h4>
                    <div className="profile-sites-list">
                      <div className="profile-site-pill">{siteName}{siteId ? ` (ID: ${siteId})` : ''}</div>
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Actions */}
            <div className="profile-modal-footer">
              {isClerkEnabled ? (
                <ClerkManageAccountButton label={i.manageAccount} />
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
            <p>{isClerkEnabled
              ? 'Sign in with your Clerk supervisor credentials to approve plans and dispatch briefings.'
              : 'Enter your details to sign in as the on-site supervisor.'}</p>

            {isClerkEnabled ? (
              <div className="signed-out-actions">
                <button
                  type="button"
                  className="btn-primary sign-in-action-btn"
                  onClick={() => { signIn(); onClose() }}
                >
                  <LogIn size={16} /> {i.signIn}
                </button>
                <button
                  type="button"
                  className="btn-secondary sign-in-action-btn"
                  onClick={() => { signUp(); onClose() }}
                >
                  <UserPlus size={16} /> {i.signUp}
                </button>
              </div>
            ) : (
              <form
                className="demo-login-form"
                onSubmit={e => {
                  e.preventDefault()
                  signIn({ fullName: loginName, email: loginEmail })
                  onClose()
                }}
              >
                <div className="form-group">
                  <label htmlFor="login-name">{i.fullName}</label>
                  <input
                    id="login-name"
                    value={loginName}
                    onChange={e => setLoginName(e.target.value)}
                    placeholder="Rakesh Kumar"
                    required
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="login-email">{i.emailAddress}</label>
                  <input
                    id="login-email"
                    type="email"
                    value={loginEmail}
                    onChange={e => setLoginEmail(e.target.value)}
                    placeholder="rakesh@site.in"
                    required
                  />
                </div>
                <button type="submit" className="btn-primary sign-in-action-btn demo-login-submit">
                  <LogIn size={16} /> {i.signIn}
                </button>
              </form>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
