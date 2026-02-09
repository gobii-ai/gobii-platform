import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ExternalLink, PlusSquare, ShieldAlert } from 'lucide-react'

import { Modal } from '../common/Modal'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { ConfirmDialog } from '../../screens/billing/ConfirmDialog'
import type { AddonPackOption, ContactCapInfo, TrialInfo } from '../../types/agentAddons'

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
  trial?: TrialInfo | null
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
  trial = null,
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
  const [trialConfirmOpen, setTrialConfirmOpen] = useState(false)
  const [trialConfirmBusy, setTrialConfirmBusy] = useState(false)
  const mountedRef = useRef(true)
  const resolvedMode = mode ?? 'contacts'
  const isTaskMode = resolvedMode === 'tasks'

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  const trialEndsLabel = useMemo(() => {
    const iso = trial?.trialEndsAtIso
    if (!iso) return null
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(d)
  }, [trial?.trialEndsAtIso])

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

  useEffect(() => {
    if (!open) {
      setTrialConfirmOpen(false)
      setTrialConfirmBusy(false)
    }
  }, [open])

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

  const activeOptions = isTaskMode ? taskPackOptions : contactPackOptions
  const isTrialAddonPurchase = useMemo(() => {
    if (!trial?.isTrialing) return false
    return activeOptions.some((option) => {
      const nextQty = packQuantities[option.priceId] ?? 0
      const currentQty = option.quantity ?? 0
      return nextQty > currentQty
    })
  }, [activeOptions, packQuantities, trial?.isTrialing])

  const performPackUpdate = useCallback(async (): Promise<boolean> => {
    const update = isTaskMode ? onUpdateTaskPacks : onUpdateContactPacks
    if (!update) return false
    setPackError(null)
    try {
      await update(packQuantities)
      return true
    } catch (err) {
      setPackError(`Unable to update ${isTaskMode ? 'task' : 'contact'} packs. Try again.`)
      return false
    }
  }, [isTaskMode, onClose, onUpdateContactPacks, onUpdateTaskPacks, packQuantities])

  const handlePackSave = useCallback(async () => {
    if (isTrialAddonPurchase) {
      setTrialConfirmOpen(true)
      return
    }
    const ok = await performPackUpdate()
    if (ok) {
      onClose()
    }
  }, [isTrialAddonPurchase, onClose, performPackUpdate])

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
      <>
        <ConfirmDialog
          open={trialConfirmOpen}
          title="End free trial and charge now?"
          description={
            <>
              You are currently in a free trial{trialEndsLabel ? ` (scheduled to end ${trialEndsLabel})` : ''}. Purchasing
              add-ons ends your trial immediately and you will be charged today.
            </>
          }
          confirmLabel="Confirm"
          icon={<ShieldAlert className="h-5 w-5" />}
          busy={trialConfirmBusy || packUpdating}
          onConfirm={async () => {
            if (trialConfirmBusy || packUpdating) return
            setTrialConfirmBusy(true)
            try {
              const ok = await performPackUpdate()
              if (mountedRef.current) {
                setTrialConfirmOpen(false)
              }
              if (ok) {
                onClose()
              }
            } finally {
              if (mountedRef.current) {
                setTrialConfirmBusy(false)
              }
            }
          }}
          onClose={() => {
            if (trialConfirmBusy || packUpdating) return
            setTrialConfirmOpen(false)
          }}
        />
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
      </>
    )
  }

  return (
    <>
      <ConfirmDialog
        open={trialConfirmOpen}
        title="End free trial and charge now?"
        description={
          <>
            You are currently in a free trial{trialEndsLabel ? ` (scheduled to end ${trialEndsLabel})` : ''}. Purchasing
            add-ons ends your trial immediately and you will be charged today.
          </>
        }
        confirmLabel="Confirm"
        icon={<ShieldAlert className="h-5 w-5" />}
        busy={trialConfirmBusy || packUpdating}
        onConfirm={async () => {
          if (trialConfirmBusy || packUpdating) return
          setTrialConfirmBusy(true)
          try {
            const ok = await performPackUpdate()
            if (mountedRef.current) {
              setTrialConfirmOpen(false)
            }
            if (ok) {
              onClose()
            }
          } finally {
            if (mountedRef.current) {
              setTrialConfirmBusy(false)
            }
          }
        }}
        onClose={() => {
          if (trialConfirmBusy || packUpdating) return
          setTrialConfirmOpen(false)
        }}
      />
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
    </>
  )
}
