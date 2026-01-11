import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Building2, CheckCircle2, Copy, MessageSquare, Phone, Sparkles, Zap } from 'lucide-react'

import type { AgentSetupMetadata, AgentSetupPanel, AgentSetupPhone, InsightEvent } from '../../../types/insight'
import {
  addUserPhone,
  deleteUserPhone,
  enableAgentSms,
  reassignAgentOrg,
  resendUserPhone,
  verifyUserPhone,
} from '../../../api/agentSetup'
import { HttpError } from '../../../api/http'
import '../../../styles/insights.css'

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

export function AgentSetupInsight({ insight }: AgentSetupInsightProps) {
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
    } catch {
      // Ignore clipboard failures.
    }
  }, [])

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
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [phoneInput, region])

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
    } catch (error) {
      setSmsError(describeError(error))
    } finally {
      setSmsAction(null)
    }
  }, [codeInput])

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
    } catch (error) {
      setOrgError(describeError(error))
    } finally {
      setOrgBusy(false)
    }
  }, [metadata.agentId, selectedOrgId])

  const renderAlwaysOn = () => (
    <div className="always-on-hero">
      {/* Left visual - animated rings */}
      <div className="always-on-hero__visual">
        <div className="always-on-hero__ring always-on-hero__ring--outer" />
        <div className="always-on-hero__ring always-on-hero__ring--middle" />
        <div className="always-on-hero__ring always-on-hero__ring--inner" />
        <div className="always-on-hero__icon">
          <Sparkles size={28} strokeWidth={2} />
        </div>
      </div>

      {/* Center content */}
      <div className="always-on-hero__content">
        <h3 className="always-on-hero__title">{metadata.alwaysOn.title}</h3>
        <p className="always-on-hero__body">{metadata.alwaysOn.body}</p>
      </div>

      {/* Right badge */}
      <div className="always-on-hero__badge">
        <span className="always-on-hero__badge-dot" />
        <span>Always On</span>
      </div>
    </div>
  )

  const renderSms = () => {
    const isComplete = smsEnabled && agentNumber

    const getTitle = () => {
      if (isComplete) return 'SMS Connected'
      if (phoneVerified) return 'Enable SMS'
      if (phone) return 'Verify Your Phone'
      return 'Connect via SMS'
    }

    const getSubtitle = () => {
      if (smsError) return smsError
      if (isComplete) return 'Text this number anytime to chat with your agent.'
      if (phoneVerified) return 'Your phone is verified. Enable SMS to start chatting.'
      if (phone) return 'Enter the verification code we sent to your phone.'
      return 'Get updates and chat with your agent via text message.'
    }

    return (
      <div className="sms-hero">
        {/* Left visual */}
        <div className="sms-hero__visual">
          <div className={`sms-hero__bubble sms-hero__bubble--1${isComplete ? ' sms-hero__bubble--active' : ''}`} />
          <div className={`sms-hero__bubble sms-hero__bubble--2${isComplete ? ' sms-hero__bubble--active' : ''}`} />
          <div className="sms-hero__icon">
            {isComplete ? <MessageSquare size={26} strokeWidth={2} /> : <Phone size={26} strokeWidth={2} />}
          </div>
        </div>

        {/* Center content */}
        <div className="sms-hero__content">
          <h3 className="sms-hero__title">{getTitle()}</h3>
          <p className={`sms-hero__body${smsError ? ' sms-hero__body--error' : ''}`}>{getSubtitle()}</p>

          {!phone ? (
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
            </div>
          )}
        </div>
      </div>
    )
  }

  const renderOrgTransfer = () => {
    const statusText = orgError || `Current: ${orgCurrent?.name || 'Personal'}`
    const statusClass = orgError ? 'agent-setup-panel__status agent-setup-panel__status--error' : 'agent-setup-panel__subtitle'

    return (
      <div className="agent-setup-panel agent-setup-panel--org">
        <div className="agent-setup-panel__icon">
          <Building2 size={18} strokeWidth={2} />
        </div>
        <div className="agent-setup-panel__content">
          <div className="agent-setup-panel__title">Organization ownership</div>
          <div className={statusClass}>{statusText}</div>
          <div className="agent-setup-panel__row">
            <select
              className="agent-setup-panel__select"
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
              className="agent-setup-panel__button agent-setup-panel__button--primary"
              onClick={handleOrgMove}
              disabled={!orgHasChange || orgBusy}
            >
              {orgBusy ? 'Moving...' : 'Move'}
            </button>
          </div>
        </div>
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

    return (
      <div className={`upsell-hero ${accentClass}`}>
        {/* Left visual */}
        <div className="upsell-hero__visual">
          <div className="upsell-hero__glow" />
          <div className="upsell-hero__icon">
            <Zap size={28} strokeWidth={2} />
          </div>
        </div>

        {/* Center content */}
        <div className="upsell-hero__content">
          <div className="upsell-hero__header">
            <h3 className="upsell-hero__title">{upsellItem.title}</h3>
            {upsellItem.price && <span className="upsell-hero__price">{upsellItem.price}</span>}
          </div>
          <p className="upsell-hero__body">{upsellItem.body}</p>
        </div>

        {/* Right CTA */}
        <a className="upsell-hero__cta" href={checkoutUrl}>
          <span>Upgrade</span>
          <ArrowRight size={16} strokeWidth={2.5} />
        </a>
      </div>
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
      {panel === 'sms' && renderSms()}
      {panel === 'org_transfer' && renderOrgTransfer()}
      {(panel === 'upsell_pro' || panel === 'upsell_scale') && renderUpsell()}
    </motion.div>
  )
}
