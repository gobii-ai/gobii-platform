import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Brain, Check, CheckCircle2, Copy, Mail, MessageSquare, Pencil, Phone, Rocket, Sparkles, TrendingDown, Zap } from 'lucide-react'

import type { AgentSetupMetadata, AgentSetupPanel, AgentSetupPhone, InsightEvent } from '../../../types/insight'
import {
  addUserPhone,
  cancelUserPhoneVerification,
  disableAgentSms,
  enableAgentSms,
  resendEmailVerification,
  verifyUserPhone,
} from '../../../api/agentSetup'
import { HttpError } from '../../../api/http'
import { track, AnalyticsEvent } from '../../../util/analytics'
import { getReturnToPath } from '../../../util/returnTo'
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

  const [verifiedPhone, setVerifiedPhone] = useState<AgentSetupPhone | null>(metadata.sms.userPhone ?? null)
  const [pendingPhone, setPendingPhone] = useState<AgentSetupPhone | null>(metadata.sms.pendingUserPhone ?? null)
  const [smsEnabled, setSmsEnabled] = useState(metadata.sms.enabled)
  const [agentNumber, setAgentNumber] = useState<string | null>(metadata.sms.agentNumber ?? null)
  const [replacingPhone, setReplacingPhone] = useState(false)
  const [phoneRegion, setPhoneRegion] = useState(DEFAULT_PHONE_REGION)
  const [phoneInput, setPhoneInput] = useState('')
  const [codeInput, setCodeInput] = useState('')
  const [smsAction, setSmsAction] = useState<string | null>(null)
  const [smsError, setSmsError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(pendingPhone?.cooldownRemaining ?? 0)
  const [copied, setCopied] = useState(false)
  const [emailResendState, setEmailResendState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const [emailResendError, setEmailResendError] = useState<string | null>(null)

  useEffect(() => {
    setVerifiedPhone(metadata.sms.userPhone ?? null)
    setPendingPhone(metadata.sms.pendingUserPhone ?? null)
    setSmsEnabled(metadata.sms.enabled)
    setAgentNumber(metadata.sms.agentNumber ?? null)
    setReplacingPhone(false)
  }, [metadata.sms.userPhone, metadata.sms.pendingUserPhone, metadata.sms.enabled, metadata.sms.agentNumber])

  useEffect(() => {
    setCooldown(pendingPhone?.cooldownRemaining ?? 0)
  }, [pendingPhone?.cooldownRemaining])

  useEffect(() => {
    if (cooldown <= 0) {
      return undefined
    }
    const timer = window.setTimeout(() => {
      setCooldown((prev) => Math.max(prev - 1, 0))
    }, 1000)
    return () => window.clearTimeout(timer)
  }, [cooldown])

  const phoneDisplay = verifiedPhone?.number ? formatPhoneNational(verifiedPhone.number, phoneRegion) : ''
  const agentNumberDisplay = agentNumber ? formatPhoneNational(agentNumber, phoneRegion) : ''
  const agentDisplayName = metadata.agentName?.trim() || 'Agent'
  const agentEmail = metadata.agentEmail ?? null
  const phoneVerified = Boolean(verifiedPhone)
  const hasPendingPhone = Boolean(pendingPhone)
  const showPhoneEntry = !hasPendingPhone && (!phoneVerified || replacingPhone)
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

  const handleCopyEmail = useCallback(async () => {
    if (!agentEmail || typeof navigator === 'undefined') {
      return
    }
    try {
      await navigator.clipboard.writeText(agentEmail)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      // Ignore clipboard failures.
    }
  }, [agentEmail])

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
      setVerifiedPhone(response.phone ?? null)
      setPendingPhone(response.pendingPhone ?? null)
      setReplacingPhone(false)
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
      setVerifiedPhone(response.phone ?? null)
      setPendingPhone(response.pendingPhone ?? null)
      setReplacingPhone(false)
      setCodeInput('')
      track(AnalyticsEvent.AGENT_SETUP_SMS_VERIFIED, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [codeInput, metadata.agentId])

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

  const handleCancelPhoneVerification = useCallback(async () => {
    if (cooldown > 0) {
      return
    }
    setSmsError(null)
    setSmsAction('cancel')
    try {
      const response = await cancelUserPhoneVerification()
      setVerifiedPhone(response.phone ?? null)
      setPendingPhone(response.pendingPhone ?? null)
      setCodeInput('')
      setReplacingPhone(false)
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [cooldown])

  const handleEnableSms = useCallback(async () => {
    setSmsError(null)
    setSmsAction('enable')
    try {
      const response = await enableAgentSms(metadata.agentId)
      setSmsEnabled(true)
      setAgentNumber(response.agentSms?.number ?? null)
      setVerifiedPhone(response.userPhone ?? null)
      setPendingPhone(response.pendingPhone ?? null)
      track(AnalyticsEvent.AGENT_SETUP_SMS_ENABLED, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [metadata.agentId])

  const handleDisableSms = useCallback(async () => {
    setSmsError(null)
    setSmsAction('disable')
    try {
      const response = await disableAgentSms(metadata.agentId)
      setSmsEnabled(false)
      setAgentNumber(response.agentSms?.number ?? agentNumber)
      setVerifiedPhone(response.userPhone ?? null)
      setPendingPhone(response.pendingPhone ?? null)
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [agentNumber, metadata.agentId])

  const renderAlwaysOn = () => {
    return (
      <motion.div
        className="always-panel"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div className="always-panel__icon" variants={visualVariants}>
          <Sparkles size={22} strokeWidth={2} />
        </motion.div>
        <motion.div className="always-panel__content" variants={itemVariants}>
          <div className="always-panel__header">
            <span className="always-panel__label">24/7 background mode</span>
            <span className="always-panel__status">
              <span className="always-panel__status-dot" />
              Active
            </span>
          </div>
          <span className="always-panel__title">This agent keeps working after you leave.</span>
          <span className="always-panel__subtitle">You can close the chat and come back when there are updates.</span>
        </motion.div>
      </motion.div>
    )
  }

  const renderEmail = () => {
    if (!agentEmail) {
      return null
    }
    return (
      <motion.div
        className="email-panel"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        <motion.div className="email-panel__icon" variants={visualVariants}>
          <Mail size={22} strokeWidth={2} />
        </motion.div>
        <motion.div className="email-panel__content" variants={itemVariants}>
          <span className="email-panel__label">Agent email address</span>
          <div className="email-panel__address-row">
            <a href={`mailto:${agentEmail}`} className="email-panel__address">
              {agentEmail}
            </a>
            <div className="email-panel__actions">
              <button type="button" className="email-panel__button email-panel__button--secondary" onClick={handleCopyEmail}>
                <Copy size={16} strokeWidth={2} />
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>
              <a href={`mailto:${agentEmail}`} className="email-panel__button email-panel__button--primary">
                <Mail size={18} strokeWidth={2} />
                <span>Compose</span>
              </a>
            </div>
          </div>
          <span className="email-panel__subtitle">Messages sent here go directly to {agentDisplayName}.</span>
        </motion.div>
      </motion.div>
    )
  }

  const renderSms = () => {
    const emailVerified = metadata.sms.emailVerified !== false
    const isComplete = smsEnabled && agentNumber

    const getTitle = () => {
      if (!emailVerified) return 'Email Verification Required'
      if (hasPendingPhone) return 'Verify Your Phone'
      if (isComplete) return 'SMS Connected'
      if (replacingPhone) return 'Update SMS Number'
      if (phoneVerified) return 'Enable SMS'
      return 'Connect via SMS'
    }

    const getSubtitle = () => {
      if (smsError) return smsError
      if (!emailVerified) return 'Please verify your email address to enable SMS messaging.'
      if (hasPendingPhone) return 'Enter the verification code we sent to your phone.'
      if (isComplete) return 'Text this number anytime to chat with your agent.'
      if (replacingPhone) return 'Your current number stays connected until the new number is verified.'
      if (phoneVerified) return 'Your phone is verified. Enable SMS to start chatting.'
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
          ) : showPhoneEntry ? (
            <form
              className="sms-hero__form sms-hero__form--phone"
              onSubmit={(event) => {
                event.preventDefault()
                void handleAddPhone()
              }}
            >
              <div className="sms-hero__phone-submit-group">
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
                  type="submit"
                  className="sms-hero__button sms-hero__button--input-action"
                  disabled={smsBusy}
                >
                  {smsAction === 'add' ? 'Verifying...' : 'Verify'}
                </button>
              </div>
            </form>
          ) : hasPendingPhone ? (
            <form
              className="sms-hero__form sms-hero__form--phone"
              onSubmit={(event) => {
                event.preventDefault()
                void handleVerify()
              }}
            >
              <div className="sms-hero__phone-submit-group sms-hero__phone-submit-group--code">
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
                  type="submit"
                  className="sms-hero__button sms-hero__button--input-action sms-hero__button--input-middle"
                  disabled={smsBusy}
                >
                  {smsAction === 'verify' ? 'Verifying...' : 'Verify'}
                </button>
                <button
                  type="button"
                  className="sms-hero__button sms-hero__button--input-action sms-hero__button--input-cancel"
                  onClick={handleCancelPhoneVerification}
                  disabled={smsBusy || cooldown > 0}
                >
                  {cooldown > 0 ? `Cancel in ${cooldown}s` : 'Cancel'}
                </button>
              </div>
            </form>
          ) : !smsEnabled ? (
            <div className="sms-hero__form">
              <div className="sms-hero__verified">
                <CheckCircle2 size={16} strokeWidth={2.2} />
                <span>{phoneDisplay}</span>
                <button
                  type="button"
                  className="sms-hero__edit-number"
                  onClick={() => {
                    setReplacingPhone(true)
                    setPhoneInput('')
                    setSmsError(null)
                  }}
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
                onClick={handleDisableSms}
                disabled={smsBusy}
              >
                {smsAction === 'disable' ? 'Disconnecting...' : 'Disconnect'}
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
