import { useCallback, useEffect, useState } from 'react'
import { PlusSquare } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import type { ContactCapInfo, ContactPackOption } from '../../types/agentAddons'

type AgentChatAddonsPanelProps = {
  open: boolean
  contactCap?: ContactCapInfo | null
  contactPackOptions?: ContactPackOption[]
  contactPackUpdating?: boolean
  onUpdateContactPacks?: (quantities: Record<string, number>) => Promise<void>
  subscriptionPrice?: number | null
  subscriptionCurrency?: string | null
  onClose: () => void
}

export function AgentChatAddonsPanel({
  open,
  contactCap,
  contactPackOptions = [],
  contactPackUpdating = false,
  onUpdateContactPacks,
  subscriptionPrice = null,
  subscriptionCurrency = null,
  onClose,
}: AgentChatAddonsPanelProps) {
  const [isMobile, setIsMobile] = useState(false)
  const [contactPackQuantities, setContactPackQuantities] = useState<Record<string, number>>({})
  const [contactPackError, setContactPackError] = useState<string | null>(null)

  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    if (!open) return
    const nextQuantities: Record<string, number> = {}
    contactPackOptions.forEach((option) => {
      nextQuantities[option.priceId] = option.quantity ?? 0
    })
    setContactPackQuantities(nextQuantities)
    setContactPackError(null)
  }, [open, contactPackOptions])

  const handlePackAdjust = useCallback((priceId: string, delta: number) => {
    setContactPackQuantities((prev) => {
      const current = prev[priceId] ?? 0
      const next = Math.max(0, Math.min(999, current + delta))
      if (next === current) {
        return prev
      }
      return {
        ...prev,
        [priceId]: next,
      }
    })
  }, [])

  const handleContactPackSave = useCallback(async () => {
    if (!onUpdateContactPacks) return
    setContactPackError(null)
    try {
      await onUpdateContactPacks(contactPackQuantities)
      onClose()
    } catch (err) {
      setContactPackError('Unable to update contact packs. Try again.')
    }
  }, [contactPackQuantities, onClose, onUpdateContactPacks])

  const contactPackHasChanges = contactPackOptions.some((option) => {
    const nextQty = contactPackQuantities[option.priceId] ?? 0
    return nextQty !== option.quantity
  })
  const contactPackDelta = contactPackOptions.reduce((total, option) => {
    const qty = contactPackQuantities[option.priceId] ?? 0
    return total + option.delta * qty
  }, 0)
  const contactPackCostCents = contactPackOptions.reduce((total, option) => {
    const qty = contactPackQuantities[option.priceId] ?? 0
    const unitAmount = typeof option.unitAmount === 'number' ? option.unitAmount : 0
    return total + unitAmount * qty
  }, 0)
  const contactCapLimitLabel = contactCap?.unlimited
    ? 'Unlimited'
    : contactCap?.limit ?? 'Unlimited'
  const subscriptionCents = typeof subscriptionPrice === 'number'
    ? Math.round(subscriptionPrice * 100)
    : null
  const inferredCurrency = (
    contactPackOptions.find((option) => option.currency)?.currency
    || subscriptionCurrency
    || 'USD'
  ).toUpperCase()
  const totalSubscriptionCents = subscriptionCents !== null
    ? subscriptionCents + contactPackCostCents
    : null
  const formatCents = (amountCents: number | null) => {
    if (amountCents === null) {
      return '—'
    }
    const amount = amountCents / 100
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: inferredCurrency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(amount)
    } catch {
      return `${inferredCurrency} ${amount.toFixed(2)}`
    }
  }

  const body = (
    <div className="agent-settings-panel">
      <div className="agent-settings-section">
        {contactCap ? (
          <div className="agent-settings-metrics">
            <div>
              <span className="agent-settings-metric-label">Used contacts</span>
              <span className="agent-settings-metric-value">
                {contactCap.used} / {contactCapLimitLabel}
              </span>
            </div>
            <div>
              <span className="agent-settings-metric-label">Pack uplift</span>
              <span className="agent-settings-metric-value">+{contactPackDelta}</span>
            </div>
          </div>
        ) : null}
        <div className="agent-settings-pack-list">
          {contactPackOptions.map((option) => {
            const label = contactPackOptions.length > 1 ? `${option.delta} contacts` : 'Contact pack'
            const quantity = contactPackQuantities[option.priceId] ?? 0
            return (
              <div key={option.priceId} className="agent-settings-pack-item">
                <div className="agent-settings-pack-details">
                  <p className="agent-settings-pack-title">{label}</p>
                  {option.priceDisplay ? (
                    <p className="agent-settings-pack-price">{option.priceDisplay}</p>
                  ) : null}
                </div>
                <div className="agent-settings-pack-controls">
                  <button
                    type="button"
                    className="agent-settings-pack-button"
                    onClick={() => handlePackAdjust(option.priceId, -1)}
                    disabled={contactPackUpdating || quantity <= 0}
                    aria-label="Decrease contact pack quantity"
                  >
                    -
                  </button>
                  <span className="agent-settings-pack-qty" aria-live="polite">
                    {quantity}
                  </span>
                  <button
                    type="button"
                    className="agent-settings-pack-button"
                    onClick={() => handlePackAdjust(option.priceId, 1)}
                    disabled={contactPackUpdating || quantity >= 999}
                    aria-label="Increase contact pack quantity"
                  >
                    +
                  </button>
                </div>
              </div>
            )
          })}
        </div>
        {contactPackError ? <p className="agent-settings-error">{contactPackError}</p> : null}
        <div className="agent-settings-metrics">
          <div>
            <span className="agent-settings-metric-label">Current subscription</span>
            <span className="agent-settings-metric-value">{formatCents(subscriptionCents)}</span>
          </div>
          <div>
            <span className="agent-settings-metric-label">Contact packs</span>
            <span className="agent-settings-metric-value">{formatCents(contactPackCostCents)}</span>
          </div>
          <div>
            <span className="agent-settings-metric-label">Total subscription</span>
            <span className="agent-settings-metric-value">{formatCents(totalSubscriptionCents)}</span>
          </div>
        </div>
        <div className="agent-settings-actions">
          <button
            type="button"
            className="agent-settings-save"
            onClick={handleContactPackSave}
            disabled={!onUpdateContactPacks || !contactPackHasChanges || contactPackUpdating}
          >
            {contactPackUpdating ? 'Updating...' : 'Update Subscription'}
          </button>
        </div>
      </div>
    </div>
  )

  if (!open) {
    return null
  }

  if (!isMobile) {
    return (
      <Modal
        title="Add-ons"
        subtitle="Increase contact limits for all agents."
        onClose={onClose}
        icon={PlusSquare}
        iconBgClass="bg-blue-100"
        iconColorClass="text-blue-600"
        bodyClassName="agent-settings-modal-body"
      >
        {body}
      </Modal>
    )
  }

  return (
    <AgentChatMobileSheet
      open={open}
      onClose={onClose}
      title="Add-ons"
      subtitle="Increase contact limits for all agents."
      icon={PlusSquare}
      ariaLabel="Add-ons"
    >
      {body}
    </AgentChatMobileSheet>
  )
}
