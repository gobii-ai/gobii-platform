import { useCallback, useEffect, useMemo, useState } from 'react'
import { ExternalLink, PlusSquare } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import type { AddonPackOption, ContactCapInfo } from '../../types/agentAddons'

const MAX_ADDON_PACK_QUANTITY = 999

type AddonsMode = 'contacts' | 'tasks'
type TaskQuotaInfo = {
  available: number
  total: number
  used: number
  used_pct: number
}

type AgentChatAddonsPanelProps = {
  open: boolean
  mode?: AddonsMode | null
  contactCap?: ContactCapInfo | null
  contactPackOptions?: AddonPackOption[]
  contactPackUpdating?: boolean
  onUpdateContactPacks?: (quantities: Record<string, number>) => Promise<void>
  taskPackOptions?: AddonPackOption[]
  taskPackUpdating?: boolean
  onUpdateTaskPacks?: (quantities: Record<string, number>) => Promise<void>
  taskQuota?: TaskQuotaInfo | null
  manageBillingUrl?: string | null
  onClose: () => void
}

export function AgentChatAddonsPanel({
  open,
  mode = 'contacts',
  contactCap,
  contactPackOptions = [],
  contactPackUpdating = false,
  onUpdateContactPacks,
  taskPackOptions = [],
  taskPackUpdating = false,
  onUpdateTaskPacks,
  taskQuota,
  manageBillingUrl = null,
  onClose,
}: AgentChatAddonsPanelProps) {
  const [isMobile, setIsMobile] = useState(false)
  const [packQuantities, setPackQuantities] = useState<Record<string, number>>({})
  const [packError, setPackError] = useState<string | null>(null)
  const resolvedMode = mode ?? 'contacts'
  const isTaskMode = resolvedMode === 'tasks'

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
    const activeOptions = isTaskMode ? taskPackOptions : contactPackOptions
    activeOptions.forEach((option) => {
      nextQuantities[option.priceId] = option.quantity ?? 0
    })
    setPackQuantities(nextQuantities)
    setPackError(null)
  }, [contactPackOptions, isTaskMode, open, taskPackOptions])

  const handlePackAdjust = useCallback((priceId: string, delta: number) => {
    setPackQuantities((prev) => {
      const current = prev[priceId] ?? 0
      const next = Math.max(0, Math.min(MAX_ADDON_PACK_QUANTITY, current + delta))
      if (next === current) {
        return prev
      }
      return {
        ...prev,
        [priceId]: next,
      }
    })
  }, [])

  const handlePackSave = useCallback(async () => {
    const update = isTaskMode ? onUpdateTaskPacks : onUpdateContactPacks
    if (!update) return
    setPackError(null)
    try {
      await update(packQuantities)
      onClose()
    } catch (err) {
      setPackError(`Unable to update ${isTaskMode ? 'task' : 'contact'} packs. Try again.`)
    }
  }, [isTaskMode, onClose, onUpdateContactPacks, onUpdateTaskPacks, packQuantities])

  const activeOptions = isTaskMode ? taskPackOptions : contactPackOptions
  const packUpdating = isTaskMode ? taskPackUpdating : contactPackUpdating
  const canUpdatePacks = isTaskMode ? Boolean(onUpdateTaskPacks) : Boolean(onUpdateContactPacks)
  const packHasChanges = activeOptions.some((option) => {
    const nextQty = packQuantities[option.priceId] ?? 0
    return nextQty !== option.quantity
  })
  const packDelta = activeOptions.reduce((total, option) => {
    const qty = packQuantities[option.priceId] ?? 0
    return total + option.delta * qty
  }, 0)
  const packCostCents = activeOptions.reduce((total, option) => {
    const qty = packQuantities[option.priceId] ?? 0
    const unitAmount = typeof option.unitAmount === 'number' ? option.unitAmount : 0
    return total + unitAmount * qty
  }, 0)
  const hasPricing = activeOptions.some((option) => typeof option.unitAmount === 'number')
  const contactCapLimitLabel = contactCap?.unlimited
    ? 'Unlimited'
    : contactCap?.limit ?? 'Unlimited'
  const taskQuotaLabel = useMemo(() => {
    if (!taskQuota) {
      return '—'
    }
    if (taskQuota.total < 0 || taskQuota.available < 0) {
      return 'Unlimited'
    }
    const formatter = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 })
    const remaining = Math.max(0, taskQuota.available)
    return formatter.format(remaining)
  }, [taskQuota])
  const inferredCurrency = (
    activeOptions.find((option) => option.currency)?.currency
    || 'USD'
  ).toUpperCase()
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
        {!isTaskMode && contactCap ? (
          <div className="agent-settings-metrics">
            <div>
              <span className="agent-settings-metric-label">Used contacts</span>
              <span className="agent-settings-metric-value">
                {contactCap.used} / {contactCapLimitLabel}
              </span>
            </div>
            <div>
              <span className="agent-settings-metric-label">Pack uplift</span>
              <span className="agent-settings-metric-value">+{packDelta}</span>
            </div>
          </div>
        ) : null}
        {isTaskMode ? (
          <div className="agent-settings-metrics">
            <div>
              <span className="agent-settings-metric-label">Remaining credits</span>
              <span className="agent-settings-metric-value">{taskQuotaLabel}</span>
            </div>
            <div>
              <span className="agent-settings-metric-label">Pack uplift</span>
              <span className="agent-settings-metric-value">+{packDelta}</span>
            </div>
          </div>
        ) : null}
        <div className="agent-settings-pack-list">
          {activeOptions.map((option) => {
            const label = activeOptions.length > 1
              ? `${option.delta} ${isTaskMode ? 'credits' : 'contacts'}`
              : `${isTaskMode ? 'Task' : 'Contact'} pack`
            const quantity = packQuantities[option.priceId] ?? 0
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
                    disabled={packUpdating || quantity <= 0}
                    aria-label={`Decrease ${isTaskMode ? 'task' : 'contact'} pack quantity`}
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
                    disabled={packUpdating || quantity >= MAX_ADDON_PACK_QUANTITY}
                    aria-label={`Increase ${isTaskMode ? 'task' : 'contact'} pack quantity`}
                  >
                    +
                  </button>
                </div>
              </div>
            )
          })}
        </div>
        {packError ? <p className="agent-settings-error">{packError}</p> : null}
        <div className="agent-settings-metrics">
          <div>
            <span className="agent-settings-metric-label">Pack price</span>
            <span className="agent-settings-metric-value">
              {hasPricing ? formatCents(packCostCents) : '—'}
            </span>
          </div>
        </div>
        <div className="agent-settings-actions">
          <button
            type="button"
            className="agent-settings-save"
            onClick={handlePackSave}
            disabled={!canUpdatePacks || !packHasChanges || packUpdating}
          >
            {packUpdating ? 'Updating...' : 'Update Subscription'}
          </button>
          {manageBillingUrl ? (
            <a
              className="agent-settings-link"
              href={manageBillingUrl}
              target="_blank"
              rel="noreferrer"
            >
              Manage
              <ExternalLink size={14} />
            </a>
          ) : null}
        </div>
      </div>
    </div>
  )

  if (!open) {
    return null
  }

  const subtitle = isTaskMode
    ? 'Add task credits for this billing period.'
    : 'Increase contact limits for all agents.'

  if (!isMobile) {
    return (
      <Modal
        title="Add-ons"
        subtitle={subtitle}
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
      subtitle={subtitle}
      icon={PlusSquare}
      ariaLabel="Add-ons"
    >
      {body}
    </AgentChatMobileSheet>
  )
}
