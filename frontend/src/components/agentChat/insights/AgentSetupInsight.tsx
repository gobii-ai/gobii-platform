import { useCallback, useEffect, useMemo, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, Building2, CheckCircle2, Copy, MessageSquare, Phone, Sparkles } from 'lucide-react'

import type { AgentSetupMetadata, AgentSetupPhone, InsightEvent } from '../../../types/insight'
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

function formatPlanLabel(planId: string): string {
  const normalized = planId.toLowerCase()
  if (normalized === 'free') return 'Free'
  if (normalized === 'startup') return 'Pro'
  if (normalized === 'scale') return 'Scale'
  return planId.toUpperCase()
}

export function AgentSetupInsight({ insight }: AgentSetupInsightProps) {
  const metadata = insight.metadata as AgentSetupMetadata
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

  const showOrgSection = metadata.organization.options.length > 0
  const orgCurrentId = orgCurrent?.id ?? null
  const orgHasChange = selectedOrgId !== orgCurrentId

  const upsellItems = metadata.upsell?.items ?? []
  const planBadge = metadata.upsell?.planId ? `Current plan: ${formatPlanLabel(metadata.upsell.planId)}` : null

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

  return (
    <motion.div
      className="insight-card-v2 insight-card-v2--agent-setup"
      style={{ background: 'transparent', borderRadius: 0 }}
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35 }}
    >
      <div className="agent-setup-header">
        <div className="agent-setup-header-text">
          <span className="agent-setup-eyebrow">Always-on setup</span>
          <h3 className="agent-setup-title">{metadata.alwaysOn.title}</h3>
          <p className="agent-setup-body">{metadata.alwaysOn.body}</p>
          {metadata.alwaysOn.note ? (
            <div className="agent-setup-note">
              <Sparkles size={14} strokeWidth={2} />
              <span>{metadata.alwaysOn.note}</span>
            </div>
          ) : null}
        </div>
        <div className="agent-setup-pulse">
          <span>24/7</span>
        </div>
      </div>

      <div className="agent-setup-sections">
        <section className="agent-setup-section">
          <div className="agent-setup-section-heading">
            <Phone size={18} strokeWidth={2} />
            <div>
              <div className="agent-setup-section-title">SMS updates</div>
              <div className="agent-setup-section-subtitle">
                Optional real-time updates by text.
              </div>
            </div>
          </div>

          {smsError ? <div className="agent-setup-error">{smsError}</div> : null}

          {!phone ? (
            <>
              <div className="agent-setup-form-row">
                <input
                  className="agent-setup-input"
                  type="tel"
                  autoComplete="tel"
                  placeholder="+1 415 555 0133"
                  value={phoneInput}
                  onChange={(event) => setPhoneInput(event.target.value)}
                />
                <button
                  type="button"
                  className="agent-setup-button agent-setup-button--primary"
                  onClick={handleAddPhone}
                  disabled={smsBusy}
                >
                  {smsAction === 'add' ? 'Sending...' : 'Send code'}
                </button>
              </div>
              <div className="agent-setup-hint">Include country code for verification.</div>
            </>
          ) : !phoneVerified ? (
            <>
              <div className="agent-setup-phone-pill">{phoneDisplay}</div>
              <div className="agent-setup-form-row">
                <input
                  className="agent-setup-input"
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  placeholder="Verification code"
                  value={codeInput}
                  onChange={(event) => setCodeInput(event.target.value)}
                />
                <button
                  type="button"
                  className="agent-setup-button agent-setup-button--primary"
                  onClick={handleVerify}
                  disabled={smsBusy}
                >
                  {smsAction === 'verify' ? 'Verifying...' : 'Verify'}
                </button>
              </div>
              <div className="agent-setup-inline-actions">
                <button
                  type="button"
                  className="agent-setup-button agent-setup-button--ghost"
                  onClick={handleResend}
                  disabled={smsBusy || cooldown > 0}
                >
                  {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
                </button>
                <button
                  type="button"
                  className="agent-setup-button agent-setup-button--ghost"
                  onClick={handleDeletePhone}
                  disabled={smsBusy}
                >
                  Change number
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="agent-setup-phone-verified">
                <CheckCircle2 size={16} strokeWidth={2.2} />
                <span>{phoneDisplay}</span>
              </div>
              <div className="agent-setup-inline-actions">
                <button
                  type="button"
                  className="agent-setup-button agent-setup-button--ghost"
                  onClick={handleDeletePhone}
                  disabled={smsBusy}
                >
                  Change number
                </button>
                {!smsEnabled ? (
                  <button
                    type="button"
                    className="agent-setup-button agent-setup-button--primary"
                    onClick={handleEnableSms}
                    disabled={smsBusy}
                  >
                    {smsAction === 'enable' ? 'Enabling...' : 'Enable SMS'}
                  </button>
                ) : null}
              </div>
            </>
          )}

          {smsEnabled ? (
            <div className="agent-setup-sms-live">
              <div className="agent-setup-sms-live-title">
                <MessageSquare size={16} strokeWidth={2} />
                <span>SMS live</span>
              </div>
              {agentNumberDisplay ? (
                <div className="agent-setup-agent-number">
                  <code>{agentNumberDisplay}</code>
                  <button
                    type="button"
                    className="agent-setup-copy"
                    onClick={() => handleCopy(agentNumber ?? '')}
                  >
                    <Copy size={14} />
                    {copied ? 'Copied' : 'Copy'}
                  </button>
                </div>
              ) : null}
              <div className="agent-setup-hint">Text this number to start a conversation.</div>
            </div>
          ) : null}
        </section>

        {showOrgSection ? (
          <section className="agent-setup-section">
            <div className="agent-setup-section-heading">
              <Building2 size={18} strokeWidth={2} />
              <div>
                <div className="agent-setup-section-title">Organization ownership</div>
                <div className="agent-setup-section-subtitle">
                  Move this agent into a workspace you manage.
                </div>
              </div>
            </div>

            {orgError ? <div className="agent-setup-error">{orgError}</div> : null}

            <div className="agent-setup-form-row">
              <select
                className="agent-setup-select"
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
                className="agent-setup-button agent-setup-button--primary"
                onClick={handleOrgMove}
                disabled={!orgHasChange || orgBusy}
              >
                {orgBusy ? 'Moving...' : 'Move'}
              </button>
            </div>
          </section>
        ) : null}

        {upsellItems.length > 0 ? (
          <section className="agent-setup-section">
            <div className="agent-setup-section-heading agent-setup-section-heading--upsell">
              <div>
                <div className="agent-setup-section-title">Upgrade this agent</div>
                <div className="agent-setup-section-subtitle">
                  Unlock higher limits and faster routing.
                </div>
              </div>
              {planBadge ? <span className="agent-setup-plan-badge">{planBadge}</span> : null}
            </div>

            <div className="agent-setup-upsell-grid">
              {upsellItems.map((item) => {
                const checkoutUrl = item.plan === 'pro' ? checkoutUrls.pro : checkoutUrls.scale
                return (
                  <div key={item.plan} className={`agent-setup-upsell agent-setup-upsell--${item.accent}`}>
                    <div className="agent-setup-upsell-header">
                      <div>
                        <div className="agent-setup-upsell-title">{item.title}</div>
                        <div className="agent-setup-upsell-subtitle">{item.subtitle}</div>
                      </div>
                      {item.price ? <div className="agent-setup-upsell-price">{item.price}</div> : null}
                    </div>
                    <div className="agent-setup-upsell-body">{item.body}</div>
                    <ul className="agent-setup-upsell-list">
                      {item.bullets.map((bullet, idx) => (
                        <li key={`${item.plan}-bullet-${idx}`}>
                          <span className="agent-setup-upsell-dot" />
                          {bullet}
                        </li>
                      ))}
                    </ul>
                    <a className={`agent-setup-upsell-cta agent-setup-upsell-cta--${item.accent}`} href={checkoutUrl}>
                      {item.ctaLabel}
                      <ArrowRight size={16} />
                    </a>
                  </div>
                )
              })}
            </div>
          </section>
        ) : null}
      </div>
    </motion.div>
  )
}
