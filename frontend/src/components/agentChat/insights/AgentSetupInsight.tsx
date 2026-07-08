import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Brain, Check, CheckCircle2, Copy, Download, Mail, MessageSquare, Pencil, Phone, Rocket, Sparkles, TrendingDown, Zap } from 'lucide-react'

import type { AgentSetupMetadata, AgentSetupPanel, AgentSetupPhone, InsightEvent } from '../../../types/insight'
import {
  addUserPhone,
  deleteUserPhone,
  enableAgentSms,
  resendUserPhone,
  resendEmailVerification,
  verifyUserPhone,
} from '../../../api/agentSetup'
import { HttpError } from '../../../api/http'
import { track, AnalyticsEvent } from '../../../util/analytics'
import { getReturnToPath } from '../../../util/returnTo'
import { downloadVCard } from '../../../util/vcard'
import {
  DEFAULT_PHONE_REGION,
  PhoneNumberInput,
  formatPhoneE164,
  formatPhoneNational,
} from '../../common/PhoneNumberInput'
import '../../../styles/insights.css'

// Staggered animation variants for insight panels
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.08,
      delayChildren: 0.05,
    },
  },
}

const itemVariants = {
  hidden: { opacity: 0, y: 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35 },
  },
}

const visualVariants = {
  hidden: { opacity: 0, scale: 0.85 },
  visible: {
    opacity: 1,
    scale: 1,
    transition: { duration: 0.4 },
  },
}

const badgeVariants = {
  hidden: { opacity: 0, x: 10 },
  visible: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.35, delay: 0.15 },
  },
}

type AgentSetupInsightProps = {
  insight: InsightEvent
}

function describeError(error: unknown): string {
  if (error instanceof HttpError) {
    if (typeof error.body === 'string' && error.body) {
      return error.body
    }
    if (error.body && typeof error.body === 'object' && 'error' in error.body) {
      const bodyError = (error.body as { error?: unknown }).error
      if (typeof bodyError === 'string' && bodyError) {
        return bodyError
      }
    }
    return `${error.status} ${error.statusText}`
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}

export function AgentSetupInsight({
  insight,
}: AgentSetupInsightProps) {
  const metadata = insight.metadata as AgentSetupMetadata
  const panel = (metadata.panel ?? 'always_on') as AgentSetupPanel

  const [phone, setPhone] = useState<AgentSetupPhone | null>(metadata.sms.userPhone ?? null)
  const [smsEnabled, setSmsEnabled] = useState(metadata.sms.enabled)
  const [agentNumber, setAgentNumber] = useState<string | null>(metadata.sms.agentNumber ?? null)
  const [phoneRegion, setPhoneRegion] = useState(DEFAULT_PHONE_REGION)
  const [phoneInput, setPhoneInput] = useState('')
  const [codeInput, setCodeInput] = useState('')
  const [smsAction, setSmsAction] = useState<string | null>(null)
  const [smsError, setSmsError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(phone?.cooldownRemaining ?? 0)
  const [copied, setCopied] = useState(false)
  const [emailResendState, setEmailResendState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const [emailResendError, setEmailResendError] = useState<string | null>(null)

  useEffect(() => {
    setPhone(metadata.sms.userPhone ?? null)
    setSmsEnabled(metadata.sms.enabled)
    setAgentNumber(metadata.sms.agentNumber ?? null)
  }, [metadata.sms.userPhone, metadata.sms.enabled, metadata.sms.agentNumber])

  useEffect(() => {
    setCooldown(phone?.cooldownRemaining ?? 0)
  }, [phone?.cooldownRemaining])

  useEffect(() => {
    if (cooldown <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => {
      setCooldown((prev) => Math.max(prev - 1, 0))
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])

  const phoneDisplay = phone?.number ? formatPhoneNational(phone.number, phoneRegion) : ''
  const agentNumberDisplay = agentNumber ? formatPhoneNational(agentNumber, phoneRegion) : ''
  const agentDisplayName = metadata.agentName?.trim() || 'Agent'
  const agentEmail = metadata.agentEmail ?? null
  const phoneVerified = Boolean(phone?.isVerified)
  const smsBusy = smsAction !== null

  const upsellItems = metadata.upsell?.items ?? []
  const upsellPlan = panel === 'upsell_pro' ? 'pro' : panel === 'upsell_scale' ? 'scale' : null
  const upsellItem = upsellPlan ? upsellItems.find((item) => item.plan === upsellPlan) : null

  const buildCheckoutUrl = useCallback((baseUrl?: string) => {
    if (!baseUrl) {
      return ''
    }
    if (typeof window === 'undefined') {
      return baseUrl
    }
    const url = new URL(baseUrl, window.location.origin)
    const params = new URLSearchParams(url.search)
    const returnTo = getReturnToPath()
    params.set('return_to', returnTo)

    if (metadata.utmQuerystring) {
      const utmParams = new URLSearchParams(metadata.utmQuerystring)
      utmParams.forEach((value, key) => {
        if (!params.has(key)) {
          params.set(key, value)
        }
      })
    }

    const pageParams = new URLSearchParams(window.location.search)
    const orgId = pageParams.get('org_id') || metadata.organization.currentOrg?.id
    if (orgId && !params.has('org_id')) {
      params.set('org_id', orgId)
    }

    url.search = params.toString()
    return url.toString()
  }, [metadata.organization.currentOrg?.id, metadata.utmQuerystring])

  const checkoutUrls = useMemo(() => {
    return {
      pro: buildCheckoutUrl(metadata.checkout.proUrl),
      scale: buildCheckoutUrl(metadata.checkout.scaleUrl),
    }
  }, [buildCheckoutUrl, metadata.checkout.proUrl, metadata.checkout.scaleUrl])

  const handleCopy = useCallback(async (value: string) => {
    if (!value || typeof navigator === 'undefined') {
      return
    }
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
      track(AnalyticsEvent.AGENT_SETUP_SMS_NUMBER_COPIED, { agentId: metadata.agentId })
    } catch {
      // Ignore clipboard failures.
    }
  }, [metadata.agentId])

  const handleDownloadContact = useCallback(() => {
    if (!agentNumber) {
      return
    }
    downloadVCard({
      name: agentDisplayName,
      email: agentEmail,
      phone: agentNumber,
    })
  }, [agentDisplayName, agentEmail, agentNumber])

  const handleAddPhone = useCallback(async () => {
    const trimmed = phoneInput.trim()
    if (!trimmed) {
      setSmsError('Phone number is required.')
      return
    }
    setSmsError(null)
    setSmsAction('add')
    try {
      const formatted = formatPhoneE164(trimmed, phoneRegion)
      const response = await addUserPhone(formatted)
      setPhone(response.phone ?? null)
      setPhoneInput('')
      track(AnalyticsEvent.AGENT_SETUP_SMS_CODE_SENT, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [phoneInput, phoneRegion, metadata.agentId])

  const handleVerify = useCallback(async () => {
    const trimmed = codeInput.trim()
    if (!trimmed) {
      setSmsError('Verification code is required.')
      return
    }
    setSmsError(null)
    setSmsAction('verify')
    try {
      const response = await verifyUserPhone(trimmed)
      setPhone(response.phone ?? null)
      setCodeInput('')
      track(AnalyticsEvent.AGENT_SETUP_SMS_VERIFIED, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [codeInput, metadata.agentId])

  const handleResend = useCallback(async () => {
    setSmsError(null)
    setSmsAction('resend')
    try {
      const response = await resendUserPhone()
      setPhone(response.phone ?? null)
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [])

  const handleResendEmailVerification = useCallback(async () => {
    setEmailResendState('sending')
    setEmailResendError(null)
    try {
      await resendEmailVerification()
      setEmailResendState('sent')
    } catch (error) {
      setEmailResendState('error')
      setEmailResendError(describeError(error))
    }
  }, [])

  const handleDeletePhone = useCallback(async () => {
    setSmsError(null)
    setSmsAction('delete')
    try {
      const response = await deleteUserPhone()
      setPhone(response.phone ?? null)
      setSmsEnabled(false)
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [])

  const handleEnableSms = useCallback(async () => {
    setSmsError(null)
    setSmsAction('enable')
    try {
      const response = await enableAgentSms(metadata.agentId)
      setSmsEnabled(true)
      setAgentNumber(response.agentSms?.number ?? null)
      setPhone(response.userPhone ?? null)
      track(AnalyticsEvent.AGENT_SETUP_SMS_ENABLED, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [metadata.agentId])

  const renderAlwaysOn = () => {
    return (
      <div className="collab-grid">
        <motion.div
          className="collab-row"
          variants={containerVariants}
          initial="hidden"
          animate="visible"
        >
          <motion.div className="collab-row__icon collab-row__icon--blue" variants={visualVariants}>
            <Sparkles size={18} strokeWidth={2} />
          </motion.div>
          <motion.div className="collab-row__main" variants={itemVariants}>
            <span className="collab-row__title">Always on — safe to close</span>
            <span className="collab-row__desc">Your agent keeps working in the background</span>
          </motion.div>
          <motion.div className="collab-row__actions" variants={badgeVariants}>
            <span className="collab-row__status collab-row__status--active">
              <span className="collab-row__status-dot" />
              Active
            </span>
          </motion.div>
        </motion.div>
      </div>
    )
  }

  const renderEmail = () => {
    if (!agentEmail) {
      return null
    }
    return (
      <motion.div
        className="collab-grid"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div className="collab-row" variants={itemVariants}>
          <motion.div className="collab-row__icon collab-row__icon--cyan" variants={visualVariants}>
            <Mail size={18} strokeWidth={2} />
          </motion.div>
          <motion.div className="collab-row__main" variants={itemVariants}>
            <span className="collab-row__title">Contact {agentDisplayName}</span>
            <span className="collab-row__desc">Reach this agent anytime</span>
          </motion.div>
          <motion.div className="collab-row__actions" variants={badgeVariants}>
            <a href={`mailto:${agentEmail}`} className="collab-row__btn collab-row__btn--cyan">
              <Mail size={14} />
              <span>Email</span>
            </a>
          </motion.div>
        </motion.div>
      </motion.div>
    )
  }

  const renderSms = () => {
    const emailVerified = metadata.sms.emailVerified !== false
    const isComplete = smsEnabled && agentNumber

    const getTitle = () => {
      if (!emailVerified) return 'Email Verification Required'
      if (isComplete) return 'SMS Connected'
      if (phoneVerified) return 'Enable SMS'
      if (phone) return 'Verify Your Phone'
      return 'Connect via SMS'
    }

    const getSubtitle = () => {
      if (smsError) return smsError
      if (!emailVerified) return 'Please verify your email address to enable SMS messaging.'
      if (isComplete) return 'Text this number anytime to chat with your agent.'
      if (phoneVerified) return 'Your phone is verified. Enable SMS to start chatting.'
      if (phone) return 'Enter the verification code we sent to your phone.'
      return 'Get updates and chat with your agent via text message.'
    }

    return (
      <motion.div
        className="sms-hero"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Left visual */}
        <motion.div className="sms-hero__visual" variants={visualVariants}>
          <div className={`sms-hero__bubble sms-hero__bubble--1${isComplete ? ' sms-hero__bubble--active' : ''}`} />
          <div className={`sms-hero__bubble sms-hero__bubble--2${isComplete ? ' sms-hero__bubble--active' : ''}`} />
          <div className="sms-hero__icon">
            {isComplete ? <MessageSquare size={20} strokeWidth={2} /> : <Phone size={20} strokeWidth={2} />}
          </div>
        </motion.div>

        {/* Center content */}
        <motion.div className="sms-hero__content" variants={itemVariants}>
          <h3 className="sms-hero__title">{getTitle()}</h3>
          <p className={`sms-hero__body${smsError ? ' sms-hero__body--error' : ''}`}>{getSubtitle()}</p>

          {!emailVerified ? (
            <div className="sms-hero__form">
              <div className="sms-hero__verified" style={{ color: '#b45309' }}>
                <Mail size={16} strokeWidth={2.2} />
                <span>Email not verified</span>
              </div>
              {emailResendState === 'sent' ? (
                <div className="sms-hero__verified" style={{ color: '#15803d' }}>
                  <Check size={16} strokeWidth={2.2} />
                  <span>Verification email sent!</span>
                </div>
              ) : (
                <button
                  type="button"
                  className="sms-hero__button"
                  onClick={handleResendEmailVerification}
                  disabled={emailResendState === 'sending'}
                >
                  {emailResendState === 'sending' ? 'Sending...' : 'Resend Verification Email'}
                </button>
              )}
              {emailResendState === 'error' && emailResendError && (
                <span style={{ color: '#dc2626', fontSize: '12px' }}>{emailResendError}</span>
              )}
            </div>
          ) : !phone ? (
            <div className="sms-hero__form">
              <PhoneNumberInput
                className="sms-hero__phone-input"
                inputClassName="sms-hero__input"
                selectClassName="sms-hero__country-select"
                value={phoneInput}
                region={phoneRegion}
                onValueChange={setPhoneInput}
                onRegionChange={setPhoneRegion}
                disabled={smsBusy}
              />
              <button
                type="button"
                className="sms-hero__button"
                onClick={handleAddPhone}
                disabled={smsBusy}
              >
                {smsAction === 'add' ? 'Sending...' : 'Send Code'}
              </button>
            </div>
          ) : !phoneVerified ? (
            <div className="sms-hero__form">
              <input
                className="sms-hero__input"
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="Enter code"
                value={codeInput}
                onChange={(event) => setCodeInput(event.target.value)}
              />
              <button
                type="button"
                className="sms-hero__button"
                onClick={handleVerify}
                disabled={smsBusy}
              >
                {smsAction === 'verify' ? 'Verifying...' : 'Verify'}
              </button>
              <div className="sms-hero__links">
                <button type="button" className="sms-hero__link" onClick={handleResend} disabled={smsBusy || cooldown > 0}>
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend'}
                </button>
                <span className="sms-hero__link-sep">·</span>
                <button type="button" className="sms-hero__link" onClick={handleDeletePhone} disabled={smsBusy}>
                  Change
                </button>
              </div>
            </div>
          ) : !smsEnabled ? (
            <div className="sms-hero__form">
              <div className="sms-hero__verified">
                <CheckCircle2 size={16} strokeWidth={2.2} />
                <span>{phoneDisplay}</span>
                <button
                  type="button"
                  className="sms-hero__edit-number"
                  onClick={handleDeletePhone}
                  disabled={smsBusy}
                  aria-label="Change number"
                  title="Change number"
                >
                  <Pencil size={13} strokeWidth={2.2} />
                </button>
              </div>
              <button
                type="button"
                className="sms-hero__button"
                onClick={handleEnableSms}
                disabled={smsBusy}
              >
                {smsAction === 'enable' ? 'Enabling...' : 'Enable SMS'}
              </button>
            </div>
          ) : (
            <div className="sms-hero__form">
              <div className="sms-hero__number">
                <span>{agentNumberDisplay}</span>
              </div>
              <button
                type="button"
                className="sms-hero__button sms-hero__button--secondary"
                onClick={() => handleCopy(agentNumber ?? '')}
              >
                <Copy size={14} />
                {copied ? 'Copied!' : 'Copy Number'}
              </button>
              <button
                type="button"
                className="sms-hero__button sms-hero__button--secondary"
                onClick={handleDownloadContact}
              >
                <Download size={14} />
                Download Contact
              </button>
            </div>
          )}
        </motion.div>
      </motion.div>
    )
  }

  const renderUpsell = () => {
    if (!upsellItem) {
      return null
    }

    const checkoutUrl = upsellItem.plan === 'pro' ? checkoutUrls.pro : checkoutUrls.scale
    const isPro = upsellItem.plan === 'pro'
    const accentClass = isPro ? 'upsell-hero--indigo' : 'upsell-hero--violet'

    // Fallback benefits if backend doesn't provide enough
    const proBenefits = [
      'More monthly tasks',
      'Priority support',
      'Advanced features',
      'Faster responses',
    ]
    const scaleBenefits = [
      'Highest task limits',
      'Lowest per-task rate',
      'Advanced intelligence',
      'Priority processing',
    ]

    const backendBullets = upsellItem.bullets ?? []
    const fallbackBullets = isPro ? proBenefits : scaleBenefits
    // Use backend bullets first, then fill with fallbacks up to 4 items
    const displayBullets = backendBullets.length >= 3
      ? backendBullets.slice(0, 4)
      : [...backendBullets, ...fallbackBullets.filter((b) => !backendBullets.includes(b))].slice(0, 4)

    // Plan-specific icons for benefits
    const getBenefitIcon = (index: number) => {
      if (isPro) {
        const icons = [Zap, Rocket, Sparkles, Check]
        const Icon = icons[index % icons.length]
        return <Icon size={14} strokeWidth={2.5} />
      }
      // Scale icons emphasize bulk/power
      const icons = [Rocket, TrendingDown, Brain, Zap]
      const Icon = icons[index % icons.length]
      return <Icon size={14} strokeWidth={2.5} />
    }

    return (
      <motion.div
        className={`upsell-hero ${accentClass}`}
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Left: Pricing card */}
        <motion.div className="upsell-hero__pricing" variants={visualVariants}>
          <div className="upsell-hero__plan-badge">{upsellItem.title}</div>
          {upsellItem.price && (
            <div className="upsell-hero__price">
              <span className="upsell-hero__price-amount">{upsellItem.price}</span>
              <span className="upsell-hero__price-period">/month</span>
            </div>
          )}
          <motion.a
            className="upsell-hero__cta"
            href={checkoutUrl}
            variants={badgeVariants}
            target="_top"
            onClick={() => {
              const planName = upsellItem?.plan?.replace(/\b\w/g, char => char.toUpperCase());

              track(AnalyticsEvent.AGENT_SETUP_UPGRADE_CLICKED + " - " + planName, {
                agentId: metadata.agentId,
                plan: upsellItem.plan,
              })
            }}
          >
            <span>{upsellItem.ctaLabel || 'Upgrade Now'}</span>
            <ArrowRight size={15} strokeWidth={2.5} />
          </motion.a>
        </motion.div>

        {/* Right: Benefits */}
        <motion.div className="upsell-hero__content" variants={itemVariants}>
          <div className="upsell-hero__header">
            <h3 className="upsell-hero__title">{isPro ? 'Unlock more power' : 'Built for power users'}</h3>
            <p className="upsell-hero__subtitle">{upsellItem.subtitle || upsellItem.body}</p>
          </div>
          <ul className="upsell-hero__benefits">
            {displayBullets.map((bullet, idx) => (
              <li key={idx} className="upsell-hero__benefit">
                {getBenefitIcon(idx)}
                <span>{bullet}</span>
              </li>
            ))}
          </ul>
        </motion.div>
      </motion.div>
    )
  }

  if ((panel === 'upsell_pro' || panel === 'upsell_scale') && !upsellItem) {
    return null
  }

  return (
    <motion.div
      className="insight-card-v2 insight-card-v2--agent-setup"
      style={{ background: 'transparent', borderRadius: 0 }}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      {panel === 'always_on' && renderAlwaysOn()}
      {panel === 'email' && renderEmail()}
      {panel === 'sms' && renderSms()}
      {(panel === 'upsell_pro' || panel === 'upsell_scale') && renderUpsell()}
    </motion.div>
  )
}
