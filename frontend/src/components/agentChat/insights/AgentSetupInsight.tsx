import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Brain, Building2, Check, CheckCircle2, Copy, Download, ExternalLink, Globe, Link2, Loader2, Mail, MessageSquare, Phone, Rocket, Sparkles, TrendingDown, UserPlus, Zap } from 'lucide-react'

import type { AgentSetupMetadata, AgentSetupPanel, AgentSetupPhone, InsightEvent } from '../../../types/insight'
import {
  addUserPhone,
  deleteUserPhone,
  enableAgentSms,
  reassignAgentOrg,
  resendUserPhone,
  resendEmailVerification,
  verifyUserPhone,
} from '../../../api/agentSetup'
import { cloneAgentTemplate } from '../../../api/agentTemplates'
import { HttpError } from '../../../api/http'
import { track, AnalyticsEvent } from '../../../util/analytics'
import { downloadVCard } from '../../../util/vcard'
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

declare global {
  interface Window {
    libphonenumber?: {
      AsYouType: new (region: string) => { input: (value: string) => string }
      parsePhoneNumber: (value: string, region?: string) => { number: string; formatNational: () => string }
    }
  }
}

type AgentSetupInsightProps = {
  insight: InsightEvent
  onCollaborate?: () => void
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

function getDefaultRegion(): string {
  if (typeof navigator === 'undefined') {
    return 'US'
  }
  const parts = (navigator.language || 'en-US').split('-')
  return (parts[1] || 'US').toUpperCase()
}

function formatPhoneDisplay(number: string, region: string): string {
  const trimmed = number.trim()
  if (!trimmed || typeof window === 'undefined') {
    return number
  }
  const lib = window.libphonenumber
  if (!lib?.parsePhoneNumber) {
    return number
  }
  try {
    return lib.parsePhoneNumber(trimmed, region).formatNational()
  } catch {
    return number
  }
}

function formatPhoneE164(raw: string, region: string): string {
  const trimmed = raw.trim()
  if (!trimmed || typeof window === 'undefined') {
    return trimmed
  }
  const lib = window.libphonenumber
  if (!lib?.parsePhoneNumber) {
    return trimmed
  }
  try {
    return lib.parsePhoneNumber(trimmed, region).number || trimmed
  } catch {
    return trimmed
  }
}

export function AgentSetupInsight({ insight, onCollaborate }: AgentSetupInsightProps) {
  const metadata = insight.metadata as AgentSetupMetadata
  const panel = (metadata.panel ?? 'always_on') as AgentSetupPanel
  const region = useMemo(() => getDefaultRegion(), [])

  const [phone, setPhone] = useState<AgentSetupPhone | null>(metadata.sms.userPhone ?? null)
  const [smsEnabled, setSmsEnabled] = useState(metadata.sms.enabled)
  const [agentNumber, setAgentNumber] = useState<string | null>(metadata.sms.agentNumber ?? null)
  const [phoneInput, setPhoneInput] = useState('')
  const [codeInput, setCodeInput] = useState('')
  const [smsAction, setSmsAction] = useState<string | null>(null)
  const [smsError, setSmsError] = useState<string | null>(null)
  const [cooldown, setCooldown] = useState(phone?.cooldownRemaining ?? 0)
  const [copied, setCopied] = useState(false)
  const [emailResendState, setEmailResendState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle')
  const [emailResendError, setEmailResendError] = useState<string | null>(null)

  const [templateUrl, setTemplateUrl] = useState(metadata.template?.url ?? null)
  const [templateHandle, setTemplateHandle] = useState(
    metadata.publicProfile?.handle ?? metadata.publicProfile?.suggestedHandle ?? ''
  )
  const [templateHandleDirty, setTemplateHandleDirty] = useState(false)
  const [templatePanelOpen, setTemplatePanelOpen] = useState(false)
  const [templateBusy, setTemplateBusy] = useState(false)
  const [templateError, setTemplateError] = useState<string | null>(null)
  const [templateCopied, setTemplateCopied] = useState(false)

  const [orgCurrent, setOrgCurrent] = useState(metadata.organization.currentOrg ?? null)
  const [selectedOrgId, setSelectedOrgId] = useState<string | null>(metadata.organization.currentOrg?.id ?? null)
  const [orgError, setOrgError] = useState<string | null>(null)
  const [orgBusy, setOrgBusy] = useState(false)

  useEffect(() => {
    setPhone(metadata.sms.userPhone ?? null)
    setSmsEnabled(metadata.sms.enabled)
    setAgentNumber(metadata.sms.agentNumber ?? null)
  }, [metadata.sms.userPhone, metadata.sms.enabled, metadata.sms.agentNumber])

  useEffect(() => {
    setOrgCurrent(metadata.organization.currentOrg ?? null)
    setSelectedOrgId(metadata.organization.currentOrg?.id ?? null)
  }, [metadata.organization.currentOrg])

  useEffect(() => {
    setTemplateUrl(metadata.template?.url ?? null)
  }, [metadata.template?.url])

  useEffect(() => {
    const existingHandle = metadata.publicProfile?.handle ?? ''
    if (existingHandle) {
      setTemplateHandle(existingHandle)
      setTemplateHandleDirty(false)
      return
    }
    if (templateHandleDirty) {
      return
    }
    const suggested = metadata.publicProfile?.suggestedHandle ?? ''
    if (suggested) {
      setTemplateHandle(suggested)
    }
  }, [metadata.publicProfile?.handle, metadata.publicProfile?.suggestedHandle, templateHandleDirty])

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

  const phoneDisplay = phone?.number ? formatPhoneDisplay(phone.number, region) : ''
  const agentNumberDisplay = agentNumber ? formatPhoneDisplay(agentNumber, region) : ''
  const agentDisplayName = metadata.agentName?.trim() || 'Agent'
  const agentEmail = metadata.agentEmail ?? null
  const phoneVerified = Boolean(phone?.isVerified)
  const smsBusy = smsAction !== null

  const orgCurrentId = orgCurrent?.id ?? null
  const orgHasChange = selectedOrgId !== orgCurrentId

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
    const returnTo = `${window.location.pathname}${window.location.search}${window.location.hash}` || '/'
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
    const orgId = pageParams.get('org_id') || orgCurrent?.id
    if (orgId && !params.has('org_id')) {
      params.set('org_id', orgId)
    }

    url.search = params.toString()
    return url.toString()
  }, [metadata.utmQuerystring, orgCurrent?.id])

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

  const handleTemplateCopy = useCallback(async (value: string) => {
    if (!value) return

    let success = false

    // Try modern Clipboard API first (requires HTTPS)
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value)
        success = true
      } catch {
        // Fall through to legacy method
      }
    }

    // Fallback for HTTP or older browsers
    if (!success) {
      try {
        const textarea = document.createElement('textarea')
        textarea.value = value
        textarea.style.position = 'fixed'
        textarea.style.left = '-9999px'
        textarea.style.top = '-9999px'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        success = document.execCommand('copy')
        document.body.removeChild(textarea)
      } catch {
        // Copy failed
      }
    }

    if (success) {
      setTemplateCopied(true)
      window.setTimeout(() => setTemplateCopied(false), 1800)
    }
  }, [])

  const handleCreateTemplate = useCallback(async () => {
    const hasProfile = Boolean(metadata.publicProfile?.handle)
    if (!hasProfile && !templatePanelOpen) {
      setTemplatePanelOpen(true)
      setTemplateError(null)
      return
    }
    if (!hasProfile && !templateHandle.trim()) {
      setTemplateError('Choose a public handle to continue.')
      return
    }
    // Note: Charter requirement validation should be handled by the backend API.
    // The backend should return an appropriate error if the agent has no charter set.
    setTemplateBusy(true)
    setTemplateError(null)
    try {
      const response = await cloneAgentTemplate(
        metadata.agentId,
        hasProfile ? null : templateHandle.trim()
      )
      setTemplateUrl(response.templateUrl)
      setTemplateHandle(response.publicProfileHandle)
      setTemplateHandleDirty(false)
      setTemplatePanelOpen(false)
    } catch (error) {
      setTemplateError(describeError(error))
    } finally {
      setTemplateBusy(false)
    }
  }, [metadata.agentId, metadata.publicProfile?.handle, templatePanelOpen, templateHandle])

  const handleAddPhone = useCallback(async () => {
    const trimmed = phoneInput.trim()
    if (!trimmed) {
      setSmsError('Phone number is required.')
      return
    }
    setSmsError(null)
    setSmsAction('add')
    try {
      const formatted = formatPhoneE164(trimmed, region)
      const response = await addUserPhone(formatted)
      setPhone(response.phone ?? null)
      setPhoneInput('')
      track(AnalyticsEvent.AGENT_SETUP_SMS_CODE_SENT, { agentId: metadata.agentId })
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [phoneInput, region, metadata.agentId])

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

  const handleOrgMove = useCallback(async () => {
    setOrgError(null)
    setOrgBusy(true)
    try {
      const response = await reassignAgentOrg(metadata.agentId, selectedOrgId)
      const nextOrg = response.organization ?? null
      setOrgCurrent(nextOrg)
      setSelectedOrgId(nextOrg?.id ?? null)
      track(AnalyticsEvent.AGENT_SETUP_ORG_MOVED, {
        agentId: metadata.agentId,
        toOrgId: nextOrg?.id ?? 'personal',
        toOrgName: nextOrg?.name ?? 'Personal workspace',
      })
    } catch (error) {
      setOrgError(describeError(error))
    } finally {
      setOrgBusy(false)
    }
  }, [metadata.agentId, selectedOrgId])

  const renderAlwaysOn = () => {
    const hasContact = agentEmail || agentNumber
    return (
      <motion.div
        className="always-on-hero"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Left visual - animated rings */}
        <motion.div className="always-on-hero__visual" variants={visualVariants}>
          <div className="always-on-hero__ring always-on-hero__ring--outer" />
          <div className="always-on-hero__ring always-on-hero__ring--middle" />
          <div className="always-on-hero__ring always-on-hero__ring--inner" />
          <div className="always-on-hero__icon">
            <Sparkles size={28} strokeWidth={2} />
          </div>
        </motion.div>

        {/* Center content */}
        <motion.div className="always-on-hero__content" variants={itemVariants}>
          <h3 className="always-on-hero__title">{metadata.alwaysOn.title}</h3>
          <p className="always-on-hero__body">{metadata.alwaysOn.body}</p>
          {hasContact ? (
            <div className="always-on-hero__contact">
              <span className="always-on-hero__contact-label">Reach {agentDisplayName}:</span>
              {agentEmail ? (
                <a href={`mailto:${agentEmail}`} className="always-on-hero__contact-link">
                  <Mail size={13} strokeWidth={2} />
                  <span>{agentEmail}</span>
                </a>
              ) : null}
              {agentEmail && agentNumber ? <span className="always-on-hero__contact-sep">·</span> : null}
              {agentNumber ? (
                <a href={`sms:${agentNumber}`} className="always-on-hero__contact-link">
                  <MessageSquare size={13} strokeWidth={2} />
                  <span>{agentNumberDisplay}</span>
                </a>
              ) : null}
            </div>
          ) : null}
        </motion.div>

        {/* Right badge */}
        <motion.div className="always-on-hero__badge" variants={badgeVariants}>
          <span className="always-on-hero__badge-dot" />
          <span>Always On</span>
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
              <input
                className="sms-hero__input"
                type="tel"
                autoComplete="tel"
                placeholder="+1 415 555 0133"
                value={phoneInput}
                onChange={(event) => setPhoneInput(event.target.value)}
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
              </div>
              <button
                type="button"
                className="sms-hero__button"
                onClick={handleEnableSms}
                disabled={smsBusy}
              >
                {smsAction === 'enable' ? 'Enabling...' : 'Enable SMS'}
              </button>
              <div className="sms-hero__links">
                <button type="button" className="sms-hero__link" onClick={handleDeletePhone} disabled={smsBusy}>
                  Change number
                </button>
              </div>
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

  const renderOrgTransfer = () => {
    const statusText = orgError || `Currently owned by ${orgCurrent?.name || 'your personal workspace'}`

    return (
      <motion.div
        className="org-hero"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Left visual */}
        <motion.div className="org-hero__visual" variants={visualVariants}>
          <div className="org-hero__ring org-hero__ring--outer" />
          <div className="org-hero__ring org-hero__ring--inner" />
          <div className="org-hero__icon">
            <Building2 size={24} strokeWidth={2} />
          </div>
        </motion.div>

        {/* Center content */}
        <motion.div className="org-hero__content" variants={itemVariants}>
          <h3 className="org-hero__title">Organization</h3>
          <p className={`org-hero__body${orgError ? ' org-hero__body--error' : ''}`}>{statusText}</p>
        </motion.div>

        {/* Right controls */}
        <motion.div className="org-hero__controls" variants={badgeVariants}>
          <select
            className="org-hero__select"
            value={selectedOrgId ?? 'personal'}
            onChange={(event) => {
              const value = event.target.value
              setSelectedOrgId(value === 'personal' ? null : value)
            }}
          >
            <option value="personal">Personal workspace</option>
            {metadata.organization.options.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="org-hero__button"
            onClick={handleOrgMove}
            disabled={!orgHasChange || orgBusy}
          >
            {orgBusy ? 'Moving...' : 'Move'}
          </button>
        </motion.div>
      </motion.div>
    )
  }

  const renderTemplate = () => {
    const hasProfile = Boolean(metadata.publicProfile?.handle)
    const handleValue = metadata.publicProfile?.handle ?? templateHandle
    const hasTemplate = Boolean(templateUrl)
    const showHandleForm = !hasProfile && templatePanelOpen

    // Extract display URL - show full URL or construct from handle
    const displayUrl = templateUrl || (handleValue ? `gobii.ai/${handleValue}` : '')
    const shortDisplayUrl = displayUrl.replace(/^https?:\/\//, '')


    const getTitle = () => {
      if (hasTemplate) return 'Your Agent is Live!'
      if (showHandleForm) return 'Choose Your Handle'
      return 'Share Your Agent'
    }

    const getSubtitle = () => {
      if (templateError) return templateError
      if (hasTemplate) return 'Anyone with this link can spawn their own copy of your agent.'
      if (showHandleForm) return 'This will be your public URL that anyone can visit.'
      return 'Create a public template that others can clone and customize.'
    }

    const renderCollaborate = () => {
      if (!onCollaborate) {
        return null
      }

      return (
        <motion.div
          className="share-hero"
          variants={containerVariants}
          initial="hidden"
          animate="visible"
        >
          <motion.div className="share-hero__visual" variants={visualVariants}>
            <div className="share-hero__ring share-hero__ring--1" />
            <div className="share-hero__ring share-hero__ring--2" />
            <div className="share-hero__icon share-hero__icon--collab">
              <UserPlus size={20} strokeWidth={2} />
            </div>
          </motion.div>

          <motion.div className="share-hero__content" variants={itemVariants}>
            <div className="share-hero__header">
              <h3 className="share-hero__title">Collaborate</h3>
            </div>
            <p className="share-hero__body">
              Invite teammates to view and message with this agent.
            </p>
          </motion.div>

          <motion.div className="share-hero__action" variants={badgeVariants}>
            <button
              type="button"
              className="share-hero__button share-hero__button--collab"
              onClick={onCollaborate}
              aria-label="Invite collaborators"
            >
              <UserPlus size={16} />
              <span>Invite</span>
            </button>
          </motion.div>
        </motion.div>
      )
    }

    return (
      <div className="share-hero-grid">
        <motion.div
          className={`share-hero${hasTemplate ? ' share-hero--live' : ''}`}
          variants={containerVariants}
          initial="hidden"
          animate="visible"
        >
          {/* Left visual */}
          <motion.div className="share-hero__visual" variants={visualVariants}>
            <div className={`share-hero__ring share-hero__ring--1${hasTemplate ? ' share-hero__ring--active' : ''}`} />
            <div className={`share-hero__ring share-hero__ring--2${hasTemplate ? ' share-hero__ring--active' : ''}`} />
            <div className={`share-hero__icon${hasTemplate ? ' share-hero__icon--live' : ''}`}>
              {hasTemplate ? <CheckCircle2 size={20} strokeWidth={2} /> : <Globe size={20} strokeWidth={2} />}
            </div>
          </motion.div>

          {/* Center content */}
          <motion.div className="share-hero__content" variants={itemVariants}>
            <div className="share-hero__header">
              <h3 className="share-hero__title">{getTitle()}</h3>
              {hasTemplate && (
                <motion.span
                  className="share-hero__badge"
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: 'spring', stiffness: 500, damping: 25 }}
                >
                  LIVE
                </motion.span>
              )}
            </div>
            <p className={`share-hero__body${templateError ? ' share-hero__body--error' : ''}`}>
              {getSubtitle()}
            </p>

            {showHandleForm ? (
              <div className="share-hero__form share-hero__form--input">
                <div className="share-hero__input-wrapper">
                  <div className="share-hero__input-group">
                    <div className="share-hero__input-icon">
                      <Link2 size={14} strokeWidth={2.5} />
                    </div>
                    <span className="share-hero__input-domain">gobii.ai/</span>
                    <input
                      className="share-hero__input"
                      type="text"
                      value={templateHandle}
                      onChange={(event) => {
                        setTemplateHandle(event.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))
                        setTemplateHandleDirty(true)
                      }}
                      placeholder="your-handle"
                      autoFocus
                    />
                  </div>
                  <span className="share-hero__input-hint">Lowercase letters, numbers, and hyphens</span>
                </div>
                <div className="share-hero__form-actions">
                  <button
                    type="button"
                    className={`share-hero__button share-hero__button--primary${templateBusy ? ' share-hero__button--loading' : ''}`}
                    onClick={handleCreateTemplate}
                    disabled={templateBusy || !templateHandle.trim()}
                  >
                    {templateBusy ? (
                      <>
                        <Loader2 size={14} className="share-hero__spinner" />
                        <span>Creating</span>
                      </>
                    ) : (
                      <>
                        <Globe size={14} />
                        <span>Go Live</span>
                      </>
                    )}
                  </button>
                  <button
                    type="button"
                    className="share-hero__cancel"
                    onClick={() => setTemplatePanelOpen(false)}
                    disabled={templateBusy}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : hasTemplate ? (
              <motion.div
                className={`share-hero__live-url${templateCopied ? ' share-hero__live-url--copied' : ''}`}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 }}
              >
                <div className="share-hero__url-display">
                  <Link2 size={14} className="share-hero__url-icon" />
                  <span className="share-hero__url-text">{shortDisplayUrl}</span>
                </div>
                <button
                  type="button"
                  className={`share-hero__copy-btn${templateCopied ? ' share-hero__copy-btn--copied' : ''}`}
                  onClick={() => handleTemplateCopy(templateUrl ?? '')}
                >
                  {templateCopied ? (
                    <>
                      <CheckCircle2 size={14} />
                      <span>Copied!</span>
                    </>
                  ) : (
                    <>
                      <Copy size={14} />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </motion.div>
            ) : null}
          </motion.div>

          {/* Right action */}
          {!showHandleForm && (
            <motion.div className="share-hero__action" variants={badgeVariants}>
              {hasTemplate && templateUrl ? (
                <a
                  className="share-hero__cta share-hero__cta--live"
                  href={templateUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  <span>Open</span>
                  <ExternalLink size={14} strokeWidth={2.2} />
                </a>
              ) : (
                <button
                  type="button"
                  className={`share-hero__button share-hero__button--primary share-hero__button--large${templateBusy ? ' share-hero__button--loading' : ''}`}
                  onClick={handleCreateTemplate}
                  disabled={templateBusy}
                >
                  {templateBusy ? (
                    <>
                      <Loader2 size={16} className="share-hero__spinner" />
                      <span>Creating</span>
                    </>
                  ) : (
                    <>
                      <Globe size={16} />
                      <span>Share Agent</span>
                    </>
                  )}
                </button>
              )}
            </motion.div>
          )}
        </motion.div>
        {renderCollaborate()}
      </div>
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

  if (panel === 'org_transfer' && metadata.organization.options.length === 0) {
    return null
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
      {panel === 'template' && renderTemplate()}
      {panel === 'sms' && renderSms()}
      {panel === 'org_transfer' && renderOrgTransfer()}
      {(panel === 'upsell_pro' || panel === 'upsell_scale') && renderUpsell()}
    </motion.div>
  )
}
