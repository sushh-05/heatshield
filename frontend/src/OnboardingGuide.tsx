import React, { useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, MapPin, Send, ShieldCheck, Sparkles, Users, X } from 'lucide-react'
import { t, type Lang } from './i18n'

type Props = {
  open: boolean
  language: Lang
  onClose: () => void
}

const STEP_ICONS = [ShieldCheck, MapPin, Sparkles, Send, Users, AlertTriangle]

export function OnboardingGuide({ open, language, onClose }: Props) {
  const i = t(language)
  const [step, setStep] = useState(0)

  if (!open) return null

  const steps = [
    { title: i.onboardingWelcomeTitle, body: i.onboardingWelcomeBody },
    { title: i.onboardingStep1Title, body: i.onboardingStep1Body },
    { title: i.onboardingStep2Title, body: i.onboardingStep2Body },
    { title: i.onboardingStep3Title, body: i.onboardingStep3Body },
    { title: i.onboardingStep4Title, body: i.onboardingStep4Body },
    { title: i.onboardingStep5Title, body: i.onboardingStep5Body },
  ]
  const total = steps.length
  const isLast = step === total - 1
  const Icon = STEP_ICONS[step] || ShieldCheck

  const next = () => { if (isLast) onClose(); else setStep(s => Math.min(total - 1, s + 1)) }
  const back = () => setStep(s => Math.max(0, s - 1))

  return (
    <div
      className="onboarding-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={steps[step].title}
      onKeyDown={e => { if (e.key === 'Escape') onClose() }}
    >
      <div className="onboarding-card">
        <button className="onboarding-close" onClick={onClose} aria-label="Close guide"><X size={16} /></button>
        <div className="onboarding-icon"><Icon size={22} /></div>
        <span className="onboarding-step-count">{i.onboardingStepCounter(step + 1, total)}</span>
        <h2>{steps[step].title}</h2>
        <p>{steps[step].body}</p>
        <div className="onboarding-dots" aria-hidden="true">
          {steps.map((_, idx) => <i key={idx} className={idx === step ? 'active' : ''} />)}
        </div>
        <div className="onboarding-actions">
          {step > 0 ? (
            <button className="onboarding-btn ghost" onClick={back}><ChevronLeft size={15} /> {i.onboardingBack}</button>
          ) : (
            <button className="onboarding-btn ghost" onClick={onClose}>{i.onboardingSkip}</button>
          )}
          <button className="onboarding-btn primary" onClick={next}>
            {isLast ? i.onboardingDone : i.onboardingNext} {!isLast && <ChevronRight size={15} />}
          </button>
        </div>
      </div>
    </div>
  )
}
