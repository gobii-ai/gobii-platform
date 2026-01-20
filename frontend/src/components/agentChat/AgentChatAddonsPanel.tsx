import { useCallback, useEffect, useState } from 'react'
import { PlusSquare } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import type { ContactCapInfo, ContactPackOption } from '../../types/agentQuickSettings'

type AgentChatAddonsPanelProps = {
  open: boolean
  contactCap?: ContactCapInfo | null
  contactPackOptions?: ContactPackOption[]
  contactPackUpdating?: boolean
  onUpdateContactPacks?: (quantities: Record<string, number>) => Promise<void>
  onClose: () => void
}

export function AgentChatAddonsPanel({
  open,
  contactCap,
  contactPackOptions = [],
  contactPackUpdating = false,
  onUpdateContactPacks,
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
    } catch (err) {
      setContactPackError('Unable to update contact packs. Try again.')
    }
  }, [contactPackQuantities, onUpdateContactPacks])

  const contactPackHasChanges = contactPackOptions.some((option) => {
    const nextQty = contactPackQuantities[option.priceId] ?? 0
    return nextQty !== option.quantity
  })
  const contactPackDelta = contactPackOptions.reduce((total, option) => {
    const qty = contactPackQuantities[option.priceId] ?? 0
    return total + option.delta * qty
  }, 0)
  const contactCapLimitLabel = contactCap?.unlimited
    ? 'Unlimited'
    : contactCap?.limit ?? 'Unlimited'

  const body = (
    <div className="agent-settings-panel">
      <div className="agent-settings-section">
        <div className="agent-settings-section-header">
          <div>
            <h3 className="agent-settings-title">Add-ons</h3>
            <p className="agent-settings-helper">Increase per-agent contacts for this cycle.</p>
          </div>
        </div>
        {contactCap ? (
          <div className="agent-settings-metrics">
            <div>
              <span className="agent-settings-metric-label">Used contacts</span>
              <span className="agent-settings-metric-value">
                {contactCap.used} / {contactCapLimitLabel}
              </span>
            </div>
            {contactCap.remaining !== null ? (
              <div>
                <span className="agent-settings-metric-label">Remaining</span>
                <span className="agent-settings-metric-value">{contactCap.remaining}</span>
              </div>
            ) : null}
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
        <div className="agent-settings-actions">
          <button
            type="button"
            className="agent-settings-save"
            onClick={handleContactPackSave}
            disabled={!onUpdateContactPacks || !contactPackHasChanges || contactPackUpdating}
          >
            {contactPackUpdating ? 'Updating...' : 'Update packs'}
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
      icon={PlusSquare}
      ariaLabel="Add-ons"
    >
      {body}
    </AgentChatMobileSheet>
  )
}
