import type { ReactNode } from 'react'
import { useCallback, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  BadgeCheck,
  Building2,
  Check,
  CreditCard,
  GlobeLock,
  Layers3,
  Loader2,
  Minus,
  Plus,
  ShieldAlert,
  Users,
  X,
} from 'lucide-react'

import { HttpError, jsonRequest } from '../api/http'
import { SaveBar } from '../components/common/SaveBar'
import { SubscriptionUpgradeModal } from '../components/common/SubscriptionUpgradeModal'
import { type PlanTier, useSubscriptionStore } from '../stores/subscriptionStore'
import { appendReturnTo } from '../util/returnTo'
import { track } from '../util/analytics'
import { AnalyticsEvent } from '../constants/analyticsEvents'

type BillingAddonOption = {
  priceId: string
  quantity: number
  delta: number
  unitAmount: number | null
  currency: string
  priceDisplay: string
}

type BillingAddonKindKey = 'taskPack' | 'contactPack' | 'browserTaskPack' | 'advancedCaptcha'

type BillingAddonContext = {
  kinds: Partial<Record<BillingAddonKindKey, { options: BillingAddonOption[] }>>
  totals: {
    amountCents: number
    currency: string
    amountDisplay: string
  }
}

type BillingPlan = Record<string, unknown> & {
  id?: string
  name?: string
  currency?: string
  price?: number
  monthly_price?: number
}

type DedicatedIpAssignedAgent = { id: string; name: string }

type DedicatedIpProxy = {
  id: string
  label: string
  name: string
  staticIp: string | null
  host: string
  assignedAgents: DedicatedIpAssignedAgent[]
}

type DedicatedIpContext = {
  allowed: boolean
  unitPrice: number
  currency: string
  multiAssign: boolean
  proxies: DedicatedIpProxy[]
}

type BillingEndpoints = {
  updateUrl: string
  cancelSubscriptionUrl?: string
}

type BillingPersonalData = {
  contextType: 'personal'
  canManageBilling: boolean
  paidSubscriber: boolean
  plan: BillingPlan
  periodStartDate?: string | null
  periodEndDate?: string | null
  cancelAt?: string | null
  cancelAtPeriodEnd: boolean
  addons: BillingAddonContext
  addonsDisabled: boolean
  dedicatedIps: DedicatedIpContext
  endpoints: BillingEndpoints
}

type BillingOrgData = {
  contextType: 'organization'
  organization: { id: string; name: string }
  canManageBilling: boolean
  plan: BillingPlan
  seats: {
    purchased: number
    reserved: number
    available: number
    pendingQuantity: number | null
    pendingEffectiveAtIso: string | null
    hasStripeSubscription: boolean
  }
  addons: BillingAddonContext
  addonsDisabled: boolean
  dedicatedIps: DedicatedIpContext
  endpoints: BillingEndpoints
}

type BillingInitialData = BillingPersonalData | BillingOrgData

export type BillingScreenProps = {
  initialData: BillingInitialData
}

type ConfirmDialogProps = {
  open: boolean
  title: string
  description?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  icon?: ReactNode
  busy?: boolean
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
  footerNote?: ReactNode
  children?: ReactNode
}

type ToggleSwitchProps = {
  checked: boolean
  disabled?: boolean
  label: string
  description?: ReactNode
  onChange: (checked: boolean) => void
}

function ToggleSwitch({ checked, disabled = false, label, description, onChange }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-disabled={disabled}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/40 disabled:opacity-60"
    >
      <div className="min-w-0">
        <div className="text-sm font-semibold text-slate-900">{label}</div>
        {description ? <div className="mt-1 text-xs text-slate-600">{description}</div> : null}
      </div>
      <span
        className={[
          'relative inline-flex h-7 w-12 flex-shrink-0 items-center rounded-full p-1 transition-colors',
          checked ? 'bg-blue-600 ring-1 ring-blue-700/40' : 'bg-blue-600/25 ring-1 ring-blue-500/40',
        ].join(' ')}
        aria-hidden="true"
      >
        <span
          className={[
            'inline-block h-5 w-5 transform rounded-full bg-white shadow-sm transition-transform',
            checked ? 'translate-x-5' : 'translate-x-0',
          ].join(' ')}
        />
      </span>
    </button>
  )
}

function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = 'Cancel',
  icon,
  busy = false,
  danger = false,
  onConfirm,
  onClose,
  footerNote,
  children,
}: ConfirmDialogProps) {
  if (!open || typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto" role="dialog" aria-modal="true">
      <div
        className="fixed inset-0 bg-slate-900/55 backdrop-blur-sm"
        aria-hidden="true"
        onClick={() => (busy ? null : onClose())}
      />
      <div className="flex min-h-full items-start justify-center p-4 pb-20 sm:items-center sm:p-6">
        <div className="relative z-10 w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl">
          <div className="flex items-start gap-4 px-6 py-5 sm:px-7">
            {icon ? (
              <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-amber-100 text-amber-700">
                {icon}
              </div>
            ) : null}
            <div className="min-w-0 flex-1">
              <div className="flex items-start justify-between gap-3">
                <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
                <button
                  type="button"
                  onClick={onClose}
                  disabled={busy}
                  className="rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
                  aria-label="Close dialog"
                >
                  <X className="h-5 w-5" />
                </button>
              </div>
              {description ? <div className="mt-2 text-sm text-slate-600">{description}</div> : null}
            </div>
          </div>

          {children ? <div className="px-6 pb-2 sm:px-7">{children}</div> : null}

          <div className="flex flex-col gap-3 px-6 pb-6 pt-4 sm:flex-row-reverse sm:items-center sm:justify-between sm:px-7">
            <div className="flex flex-col gap-2 sm:flex-row-reverse sm:items-center">
              <button
                type="button"
                onClick={onConfirm}
                disabled={busy}
                className={[
                  'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-60',
                  danger ? 'bg-rose-600 hover:bg-rose-700 focus:ring-rose-500' : 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-500',
                ].join(' ')}
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                {confirmLabel}
              </button>
              <button
                type="button"
                onClick={onClose}
                disabled={busy}
                className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-60"
              >
                {cancelLabel}
              </button>
            </div>
            {footerNote ? <div className="text-xs font-medium text-slate-500">{footerNote}</div> : null}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  )
}

type Money = {
  amountCents: number
  currency: string
}

function normalizeCurrency(currency: string | null | undefined): string {
  const trimmed = (currency ?? '').trim()
  return trimmed ? trimmed.toUpperCase() : 'USD'
}

function formatCents(amountCents: number, currency: string): string {
  const normalized = normalizeCurrency(currency)
  const amount = amountCents / 100
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: normalized,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${normalized} ${amount.toFixed(2)}`
  }
}

function planMonthlyPriceCents(plan: BillingPlan): number {
  const raw = typeof plan.monthly_price === 'number' ? plan.monthly_price : typeof plan.price === 'number' ? plan.price : 0
  return Math.max(0, Math.round(raw * 100))
}

function buildInitialAddonQuantityMap(addons: BillingAddonContext): Record<string, number> {
  const next: Record<string, number> = {}
  const keys: BillingAddonKindKey[] = ['taskPack', 'contactPack', 'browserTaskPack', 'advancedCaptcha']
  keys.forEach((key) => {
    const options = addons.kinds[key]?.options ?? []
    options.forEach((option) => {
      next[option.priceId] = option.quantity ?? 0
    })
  })
  return next
}

function buildAddonOptionLabel(kind: BillingAddonKindKey, option: BillingAddonOption): string {
  const delta = option.delta ?? 0
  if (kind === 'taskPack') return `+${delta.toLocaleString()} tasks`
  if (kind === 'contactPack') return `+${delta.toLocaleString()} contacts`
  if (kind === 'browserTaskPack') return `+${delta.toLocaleString()} browser tasks/day`
  return `Advanced CAPTCHA`
}

type AddonSectionMeta = {
  key: BillingAddonKindKey
  title: string
  description: string
  icon: ReactNode
}

const ADDON_SECTIONS: AddonSectionMeta[] = [
  {
    key: 'taskPack',
    title: 'Task Packs',
    description: 'Add more monthly tasks to this subscription.',
    icon: <Layers3 className="h-5 w-5" />,
  },
  {
    key: 'contactPack',
    title: 'Contact Packs',
    description: 'Increase your contacts per agent limit.',
    icon: <Users className="h-5 w-5" />,
  },
  {
    key: 'browserTaskPack',
    title: 'Browser Task Packs',
    description: 'Increase browser task throughput limits.',
    icon: <GlobeLock className="h-5 w-5" />,
  },
  {
    key: 'advancedCaptcha',
    title: 'Advanced CAPTCHA',
    description: 'Enable advanced CAPTCHA resolution support.',
    icon: <BadgeCheck className="h-5 w-5" />,
  },
]

type DedicatedRemovePrompt = {
  proxyId: string
  proxyLabel: string
  assignedAgents: DedicatedIpAssignedAgent[]
  unassign: boolean
}

function resolveAddonLineItems(
  addons: BillingAddonContext,
  quantities: Record<string, number>,
): Array<{ id: string; label: string; money: Money }> {
  const items: Array<{ id: string; label: string; money: Money }> = []
  const keys: BillingAddonKindKey[] = ['taskPack', 'contactPack', 'browserTaskPack', 'advancedCaptcha']
  keys.forEach((kind) => {
    const options = addons.kinds[kind]?.options ?? []
    options.forEach((option) => {
      const qty = quantities[option.priceId] ?? 0
      if (qty <= 0) {
        return
      }
      const unitAmount = typeof option.unitAmount === 'number' ? option.unitAmount : 0
      const amountCents = unitAmount * qty
      const currency = normalizeCurrency(option.currency || addons.totals.currency)
      items.push({
        id: option.priceId,
        label: `${buildAddonOptionLabel(kind, option)} x ${qty}`,
        money: { amountCents, currency },
      })
    })
  })
  return items
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    const body = error.body
    if (body && typeof body === 'object') {
      const maybeDetail = (body as { detail?: unknown }).detail
      const maybeError = (body as { error?: unknown }).error
      if (typeof maybeDetail === 'string' && maybeDetail.trim()) {
        return maybeDetail
      }
      if (typeof maybeError === 'string' && maybeError.trim()) {
        return maybeError
      }
    }
    if (typeof body === 'string' && body.trim()) {
      return body
    }
    return 'Request failed. Please try again.'
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Request failed. Please try again.'
}

export function BillingScreen({ initialData }: BillingScreenProps) {
  const isOrg = initialData.contextType === 'organization'
  const planName = (initialData.plan?.name as string | undefined) ?? (isOrg ? 'Team' : 'Plan')
  const planCurrency = normalizeCurrency((initialData.plan?.currency as string | undefined) ?? 'USD')
  const basePriceCents = planMonthlyPriceCents(initialData.plan)

  const {
    currentPlan,
    isProprietaryMode,
    isUpgradeModalOpen,
    upgradeModalSource,
    upgradeModalDismissible,
    openUpgradeModal,
    closeUpgradeModal,
    ensureAuthenticated,
  } = useSubscriptionStore()

  const [addonQuantities, setAddonQuantities] = useState<Record<string, number>>(() =>
    buildInitialAddonQuantityMap(initialData.addons),
  )
  const [selectedAddonByKind, setSelectedAddonByKind] = useState<Record<BillingAddonKindKey, string>>({
    taskPack: '',
    contactPack: '',
    browserTaskPack: '',
    advancedCaptcha: '',
  })

  const [dedicatedAddQty, setDedicatedAddQty] = useState(0)
  const [dedicatedRemoveIds, setDedicatedRemoveIds] = useState<Set<string>>(new Set())
  const [dedicatedUnassignIds, setDedicatedUnassignIds] = useState<Set<string>>(new Set())
  const [dedicatedPrompt, setDedicatedPrompt] = useState<DedicatedRemovePrompt | null>(null)

  const [seatTarget, setSeatTarget] = useState(() => (isOrg ? initialData.seats.purchased : 0))
  const [cancelSeatSchedule, setCancelSeatSchedule] = useState(false)

  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [cancelModalOpen, setCancelModalOpen] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)

  const initialAddonQuantities = useMemo(() => buildInitialAddonQuantityMap(initialData.addons), [initialData.addons])

  const seatPurchaseRequired = useMemo(() => {
    if (!isOrg) return false
    return initialData.seats.purchased <= 0
  }, [initialData, isOrg])

  const addonsInteractable = useMemo(() => {
    if (!initialData.canManageBilling) return false
    if (initialData.addonsDisabled) return false
    if (isOrg && seatPurchaseRequired) return false
    return true
  }, [initialData.addonsDisabled, initialData.canManageBilling, isOrg, seatPurchaseRequired])

  const dedicatedInteractable = useMemo(() => {
    if (!initialData.canManageBilling) return false
    if (!initialData.dedicatedIps.allowed) return false
    if (isOrg && seatPurchaseRequired) return false
    return true
  }, [initialData.canManageBilling, initialData.dedicatedIps.allowed, isOrg, seatPurchaseRequired])

  const hasSeatChanges = useMemo(() => {
    if (!isOrg) return false
    if (seatTarget !== initialData.seats.purchased) return true
    if (cancelSeatSchedule) return true
    return false
  }, [cancelSeatSchedule, initialData, isOrg, seatTarget])

  const hasAddonChanges = useMemo(() => {
    const keys = Object.keys({ ...initialAddonQuantities, ...addonQuantities })
    return keys.some((key) => (addonQuantities[key] ?? 0) !== (initialAddonQuantities[key] ?? 0))
  }, [addonQuantities, initialAddonQuantities])

  const hasDedicatedChanges = useMemo(() => dedicatedAddQty > 0 || dedicatedRemoveIds.size > 0, [dedicatedAddQty, dedicatedRemoveIds.size])

  const hasAnyChanges = useMemo(
    () => hasSeatChanges || hasAddonChanges || hasDedicatedChanges,
    [hasAddonChanges, hasDedicatedChanges, hasSeatChanges],
  )

  const effectiveDedicatedCount = useMemo(() => {
    const current = initialData.dedicatedIps.proxies.length
    const removed = dedicatedRemoveIds.size
    return Math.max(0, current + dedicatedAddQty - removed)
  }, [dedicatedAddQty, dedicatedRemoveIds.size, initialData.dedicatedIps.proxies.length])

  const summaryItems = useMemo(() => {
    const items: Array<{ id: string; label: string; money: Money }> = []
    if (isOrg) {
      const seatUnitCents = basePriceCents
      items.push({
        id: 'seats',
        label: `${seatTarget} seat${seatTarget === 1 ? '' : 's'} (${formatCents(seatUnitCents, planCurrency)}/seat)`,
        money: { amountCents: seatUnitCents * seatTarget, currency: planCurrency },
      })
    } else {
      items.push({
        id: 'plan',
        label: planName,
        money: { amountCents: basePriceCents, currency: planCurrency },
      })
    }

    const addonItems = resolveAddonLineItems(initialData.addons, addonQuantities)
    addonItems.forEach((item) => items.push(item))

    if (effectiveDedicatedCount > 0) {
      const unitCents = Math.max(0, Math.round((initialData.dedicatedIps.unitPrice || 0) * 100))
      const currency = normalizeCurrency(initialData.dedicatedIps.currency || planCurrency)
      items.push({
        id: 'dedicated-ips',
        label: `Dedicated IP${effectiveDedicatedCount === 1 ? '' : 's'} x ${effectiveDedicatedCount}`,
        money: { amountCents: unitCents * effectiveDedicatedCount, currency },
      })
    }

    return items
  }, [
    addonQuantities,
    basePriceCents,
    effectiveDedicatedCount,
    initialData.addons,
    initialData.dedicatedIps.currency,
    initialData.dedicatedIps.unitPrice,
    isOrg,
    planCurrency,
    planName,
    seatTarget,
  ])

  const summaryTotal = useMemo(() => {
    const currency = planCurrency
    const amountCents = summaryItems.reduce((sum, item) => sum + item.money.amountCents, 0)
    return { amountCents, currency }
  }, [planCurrency, summaryItems])

  const resetDraft = useCallback(() => {
    setSaveError(null)
    setAddonQuantities(buildInitialAddonQuantityMap(initialData.addons))
    setSelectedAddonByKind({ taskPack: '', contactPack: '', browserTaskPack: '', advancedCaptcha: '' })
    setDedicatedAddQty(0)
    setDedicatedRemoveIds(new Set())
    setDedicatedUnassignIds(new Set())
    setDedicatedPrompt(null)
    if (isOrg) {
      setSeatTarget(initialData.seats.purchased)
      setCancelSeatSchedule(false)
    }
  }, [initialData, isOrg])

  const handleSeatAdjust = useCallback((delta: number) => {
    if (!isOrg) return
    setSeatTarget((prev) => {
      const min = Math.max(0, initialData.seats.reserved)
      const next = Math.max(min, prev + delta)
      return next
    })
    setCancelSeatSchedule(false)
  }, [initialData, isOrg])

  const handleCancelSeatSchedule = useCallback(() => {
    if (!isOrg) return
    setSeatTarget(initialData.seats.purchased)
    setCancelSeatSchedule(true)
  }, [initialData, isOrg])

  const handleAddonRemove = useCallback((priceId: string) => {
    if (!addonsInteractable) return
    setAddonQuantities((prev) => ({ ...prev, [priceId]: 0 }))
  }, [addonsInteractable])

  const handleAddonUndo = useCallback((priceId: string) => {
    if (!addonsInteractable) return
    const initialQty = initialAddonQuantities[priceId] ?? 0
    setAddonQuantities((prev) => ({ ...prev, [priceId]: initialQty }))
  }, [addonsInteractable, initialAddonQuantities])

  const handleAddonAdd = useCallback((kind: BillingAddonKindKey) => {
    if (!addonsInteractable) return
    const selected = (selectedAddonByKind[kind] || '').trim()
    if (!selected) return
    setAddonQuantities((prev) => {
      const current = prev[selected] ?? 0
      return { ...prev, [selected]: Math.min(999, current + 1) }
    })
  }, [addonsInteractable, selectedAddonByKind])

  const captchaOptions = useMemo(() => initialData.addons.kinds.advancedCaptcha?.options ?? [], [initialData.addons.kinds])
  const captchaActivePriceId = useMemo(() => {
    const options = captchaOptions
    const active = options.find((opt) => (addonQuantities[opt.priceId] ?? 0) > 0)
    if (active?.priceId) return active.priceId
    const first = options[0]
    return first?.priceId ?? ''
  }, [addonQuantities, captchaOptions])

  const captchaEnabled = useMemo(() => {
    if (!captchaOptions.length) return false
    return captchaOptions.some((opt) => (addonQuantities[opt.priceId] ?? 0) > 0)
  }, [addonQuantities, captchaOptions])

  const handleCaptchaToggle = useCallback((enabled: boolean) => {
    if (!addonsInteractable) return
    setAddonQuantities((prev) => {
      const next = { ...prev }
      captchaOptions.forEach((opt) => {
        next[opt.priceId] = 0
      })
      if (enabled && captchaActivePriceId) {
        next[captchaActivePriceId] = 1
      }
      return next
    })
  }, [addonsInteractable, captchaActivePriceId, captchaOptions])

  const promptDedicatedRemove = useCallback((proxy: DedicatedIpProxy) => {
    if (!dedicatedInteractable) return
    if (dedicatedRemoveIds.has(proxy.id)) return

    if (proxy.assignedAgents.length) {
      setDedicatedPrompt({
        proxyId: proxy.id,
        proxyLabel: proxy.label || proxy.staticIp || proxy.host,
        assignedAgents: proxy.assignedAgents,
        unassign: true,
      })
      return
    }

    setDedicatedRemoveIds((prev) => new Set([...prev, proxy.id]))
  }, [dedicatedInteractable, dedicatedRemoveIds])

  const confirmDedicatedRemove = useCallback(() => {
    if (!dedicatedPrompt) return
    setDedicatedRemoveIds((prev) => new Set([...prev, dedicatedPrompt.proxyId]))
    if (dedicatedPrompt.unassign) {
      setDedicatedUnassignIds((prev) => new Set([...prev, dedicatedPrompt.proxyId]))
    }
    setDedicatedPrompt(null)
  }, [dedicatedPrompt])

  const handleDedicatedUndoRemove = useCallback((proxyId: string) => {
    setDedicatedRemoveIds((prev) => {
      const next = new Set(prev)
      next.delete(proxyId)
      return next
    })
    setDedicatedUnassignIds((prev) => {
      const next = new Set(prev)
      next.delete(proxyId)
      return next
    })
  }, [])

  const handleUpgrade = useCallback(async (plan: PlanTier) => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
      plan,
      source: upgradeModalSource ?? 'billing',
    })
    closeUpgradeModal()
    const checkoutPath = plan === 'startup' ? '/subscribe/startup/' : '/subscribe/scale/'
    window.open(appendReturnTo(checkoutPath, '/console/billing/'), '_top')
  }, [closeUpgradeModal, ensureAuthenticated, upgradeModalSource])

  const handleSave = useCallback(async () => {
    if (saving) return
    setSaving(true)
    setSaveError(null)

    const payload: Record<string, unknown> = {}
    if (isOrg) {
      payload.ownerType = 'organization'
      payload.organizationId = initialData.organization.id
    } else {
      payload.ownerType = 'user'
    }

    if (isOrg && hasSeatChanges) {
      payload.seatsTarget = seatTarget
      payload.cancelSeatSchedule = cancelSeatSchedule
    }

    if (hasAddonChanges && addonsInteractable) {
      const diff: Record<string, number> = {}
      const keys = Object.keys({ ...initialAddonQuantities, ...addonQuantities })
      keys.forEach((key) => {
        const nextQty = addonQuantities[key] ?? 0
        const initialQty = initialAddonQuantities[key] ?? 0
        if (nextQty !== initialQty) {
          diff[key] = nextQty
        }
      })
      payload.addonQuantities = diff
    }

    if (hasDedicatedChanges && dedicatedInteractable) {
      payload.dedicatedIps = {
        addQuantity: dedicatedAddQty,
        removeProxyIds: Array.from(dedicatedRemoveIds),
        unassignProxyIds: Array.from(dedicatedUnassignIds),
      }
    }

    try {
      const result = await jsonRequest<{ ok: boolean; redirectUrl?: string; stripeActionUrl?: string }>(
        initialData.endpoints.updateUrl,
        {
          method: 'POST',
          includeCsrf: true,
          json: payload,
        },
      )

      if (result?.redirectUrl) {
        window.location.assign(result.redirectUrl)
        return
      }
      if (result?.stripeActionUrl) {
        window.location.assign(result.stripeActionUrl)
        return
      }
      window.location.reload()
    } catch (error) {
      setSaveError(safeErrorMessage(error))
    } finally {
      setSaving(false)
    }
  }, [
    addonQuantities,
    addonsInteractable,
    cancelSeatSchedule,
    dedicatedAddQty,
    dedicatedInteractable,
    dedicatedRemoveIds,
    dedicatedUnassignIds,
    hasAddonChanges,
    hasDedicatedChanges,
    hasSeatChanges,
    initialAddonQuantities,
    initialData,
    isOrg,
    saving,
    seatTarget,
  ])

  const handleCancelSubscription = useCallback(async () => {
    if (cancelBusy) return
    const url = initialData.contextType === 'personal' ? initialData.endpoints.cancelSubscriptionUrl : undefined
    if (!url) return
    setCancelBusy(true)
    setCancelError(null)
    try {
      const result = await jsonRequest<{ success: boolean; error?: string }>(url, {
        method: 'POST',
        includeCsrf: true,
      })
      if (!result?.success) {
        setCancelError(result?.error ?? 'Unable to cancel subscription.')
        return
      }
      window.location.reload()
    } catch (error) {
      setCancelError(safeErrorMessage(error))
    } finally {
      setCancelBusy(false)
    }
  }, [cancelBusy, initialData])

  const seatPendingLabel = useMemo(() => {
    if (!isOrg) return null
    if (initialData.seats.pendingQuantity === null || !initialData.seats.pendingEffectiveAtIso) return null
    const date = new Date(initialData.seats.pendingEffectiveAtIso)
    const effective = Number.isFinite(date.getTime()) ? date.toLocaleDateString() : initialData.seats.pendingEffectiveAtIso
    return `Seats scheduled to change to ${initialData.seats.pendingQuantity} on ${effective}.`
  }, [initialData, isOrg])

  const addonsDisabledReason = useMemo(() => {
    if (!initialData.canManageBilling) return 'You do not have permission to manage billing.'
    if (initialData.addonsDisabled) return 'Add-ons are unavailable for this subscription.'
    if (isOrg && seatPurchaseRequired) return 'Purchase at least one seat to manage add-ons.'
    return null
  }, [initialData.addonsDisabled, initialData.canManageBilling, isOrg, seatPurchaseRequired])

  return (
    <div className="app-shell">
      <div className="card card--header">
        <div className="card__body card__body--header flex flex-col gap-4 py-4 sm:py-3">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-2xl bg-white/90 text-blue-700 shadow-sm">
              <CreditCard className="h-6 w-6" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h1 className="text-2xl font-bold text-slate-900 tracking-tight">Billing</h1>
              <p className="text-slate-700 font-medium">
                {isOrg ? `Organization: ${initialData.organization.name}` : 'Personal subscription and add-ons.'}
              </p>
            </div>
          </div>
        </div>
      </div>

      <main className="app-main">
        <section className="card" data-section="billing-plan">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                {isOrg ? <Building2 className="h-4 w-4 text-slate-500" /> : <CreditCard className="h-4 w-4 text-slate-500" />}
                <span>Base plan</span>
              </div>
              <div className="text-2xl font-bold text-slate-900">{planName}</div>
              {isOrg ? (
                <p className="text-sm text-slate-600">
                  {formatCents(basePriceCents, planCurrency)} per seat per month.
                </p>
              ) : (
                <p className="text-sm text-slate-600">
                  {formatCents(basePriceCents, planCurrency)} per month.
                </p>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {!isOrg && isProprietaryMode ? (
                <button
                  type="button"
                  onClick={() => openUpgradeModal('unknown')}
                  className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500/40"
                >
                  Change plan
                </button>
              ) : null}
              {initialData.contextType === 'personal' && initialData.paidSubscriber && !initialData.cancelAtPeriodEnd ? (
                <button
                  type="button"
                  onClick={() => setCancelModalOpen(true)}
                  className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-200 bg-white px-4 py-2.5 text-sm font-semibold text-rose-700 transition hover:border-rose-300 hover:text-rose-800 focus:outline-none focus:ring-2 focus:ring-rose-500/30"
                >
                  <ShieldAlert className="h-4 w-4" />
                  Cancel
                </button>
              ) : null}
            </div>
          </div>

          {isOrg ? (
            <div className="mt-6 flex flex-col gap-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
                  <Users className="h-4 w-4 text-slate-500" />
                  <span>Seats</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleSeatAdjust(-1)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
                    disabled={!initialData.canManageBilling || saving || seatTarget <= Math.max(0, initialData.seats.reserved)}
                    aria-label="Decrease seats"
                  >
                    <Minus className="h-4 w-4" strokeWidth={3} />
                  </button>
                  <div className="min-w-[5.5rem] rounded-xl border border-slate-200 bg-white px-4 py-2 text-center text-lg font-bold text-slate-900 tabular-nums">
                    {seatTarget}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleSeatAdjust(1)}
                    className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 hover:text-slate-900 disabled:opacity-50"
                    disabled={!initialData.canManageBilling || saving}
                    aria-label="Increase seats"
                  >
                    <Plus className="h-4 w-4" strokeWidth={3} />
                  </button>
                </div>
              </div>

              <div className="grid gap-3 text-sm text-slate-600 sm:grid-cols-3">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Purchased</div>
                  <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.purchased}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Reserved</div>
                  <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.reserved}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Available</div>
                  <div className="mt-1 font-semibold text-slate-900 tabular-nums">{initialData.seats.available}</div>
                </div>
              </div>

              {seatPendingLabel ? (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <div>{seatPendingLabel}</div>
                    <button
                      type="button"
                      onClick={handleCancelSeatSchedule}
                      className="inline-flex items-center justify-center gap-2 rounded-xl border border-amber-200 bg-white px-3 py-2 text-sm font-semibold text-amber-800 transition hover:border-amber-300"
                      disabled={!initialData.canManageBilling || saving}
                    >
                      Cancel scheduled change
                    </button>
                  </div>
                </div>
              ) : null}

              {seatPurchaseRequired ? (
                <div className="rounded-2xl border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
                  Add-ons and dedicated IPs are disabled until this org has at least one seat.
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mt-6 grid gap-4 sm:grid-cols-3">
              <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Billing period</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">
                  {initialData.periodStartDate && initialData.periodEndDate
                    ? `${initialData.periodStartDate} to ${initialData.periodEndDate}`
                    : '—'}
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Renewal</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">
                  {initialData.cancelAtPeriodEnd && initialData.cancelAt
                    ? `Cancels on ${initialData.cancelAt}`
                    : (initialData.periodEndDate ?? '—')}
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3">
                <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">Status</div>
                <div className="mt-1 text-sm font-semibold text-slate-900">
                  {initialData.paidSubscriber ? 'Active' : 'Free'}
                </div>
              </div>
            </div>
          )}
        </section>

        <section className="card" data-section="billing-addons">
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
              <Layers3 className="h-4 w-4 text-slate-500" />
              <span>Add-ons</span>
            </div>
            <p className="text-sm text-slate-600">
              Stage changes here, then save at the bottom.
            </p>
          </div>

          {addonsDisabledReason ? (
            <div className="mt-5 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
              {addonsDisabledReason}
            </div>
          ) : null}

          <div className="mt-6 space-y-10">
            {ADDON_SECTIONS.map((section) => {
              const options = initialData.addons.kinds[section.key]?.options ?? []
              const selectableOptions = options.filter((opt) => opt.priceId)
              const selected = selectedAddonByKind[section.key] ?? ''

              if (section.key === 'advancedCaptcha') {
                const option = captchaOptions[0] ?? null
                const priceHint = option?.priceDisplay ? `${option.priceDisplay}/mo` : null
                return (
                  <div key={section.key} className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
                    <div className="flex items-start gap-3 sm:w-72">
                      <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-blue-50 text-blue-700">
                        {section.icon}
                      </div>
                      <div className="min-w-0">
                        <div className="text-base font-bold text-slate-900">{section.title}</div>
                        <div className="mt-1 text-sm text-slate-600">{section.description}</div>
                      </div>
                    </div>

                    <div className="min-w-0 flex-1 space-y-3">
                      {captchaOptions.length ? (
                        <ToggleSwitch
                          checked={captchaEnabled}
                          disabled={!addonsInteractable || saving}
                          label={captchaEnabled ? 'Enabled' : 'Disabled'}
                          description={priceHint ? `Billed monthly (${priceHint}).` : 'Billed monthly.'}
                          onChange={handleCaptchaToggle}
                        />
                      ) : (
                        <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                          No options are configured for this add-on.
                        </div>
                      )}
                    </div>
                  </div>
                )
              }

              const rows = selectableOptions
                .map((opt) => {
                  const currentQty = initialAddonQuantities[opt.priceId] ?? 0
                  const nextQty = addonQuantities[opt.priceId] ?? 0
                  const visible = currentQty > 0 || nextQty > 0
                  return { opt, currentQty, nextQty, visible }
                })
                .filter((row) => row.visible)

              return (
                <div key={section.key} className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
                  <div className="flex items-start gap-3 sm:w-72">
                    <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-blue-50 text-blue-700">
                      {section.icon}
                    </div>
                    <div className="min-w-0">
                      <div className="text-base font-bold text-slate-900">{section.title}</div>
                      <div className="mt-1 text-sm text-slate-600">{section.description}</div>
                    </div>
                  </div>

                  <div className="min-w-0 flex-1 space-y-4">
                    {rows.length ? (
                      <div className="space-y-2">
                        {rows.map(({ opt, currentQty, nextQty }) => {
                          const isRemoved = currentQty > 0 && nextQty === 0
                          const isChanged = currentQty !== nextQty
                          const currency = normalizeCurrency(opt.currency || initialData.addons.totals.currency || planCurrency)
                          const lineCents = (typeof opt.unitAmount === 'number' ? opt.unitAmount : 0) * nextQty
                          return (
                            <div
                              key={opt.priceId}
                              className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                            >
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <div className="text-sm font-semibold text-slate-900">
                                    {buildAddonOptionLabel(section.key, opt)}
                                  </div>
                                  {opt.priceDisplay ? (
                                    <div className="text-xs font-semibold text-slate-500">{opt.priceDisplay}/mo</div>
                                  ) : null}
                                  {isChanged ? (
                                    <div className="text-xs font-semibold text-amber-700">
                                      {isRemoved ? 'Will be removed' : `Will change (${currentQty} → ${nextQty})`}
                                    </div>
                                  ) : null}
                                </div>
                                <div className="mt-1 text-xs text-slate-600">
                                  Quantity: <span className="font-semibold text-slate-900 tabular-nums">{nextQty}</span>
                                  {typeof opt.unitAmount === 'number' && nextQty > 0 ? (
                                    <>
                                      {' '}
                                      · {formatCents(lineCents, currency)}
                                    </>
                                  ) : null}
                                </div>
                              </div>

                              <div className="flex flex-wrap items-center gap-2">
                                {nextQty > 0 ? (
                                  <button
                                    type="button"
                                    onClick={() => handleAddonRemove(opt.priceId)}
                                    disabled={!addonsInteractable || saving}
                                    className="inline-flex items-center justify-center rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 disabled:opacity-50"
                                  >
                                    Remove
                                  </button>
                                ) : null}
                                {currentQty > 0 && nextQty === 0 ? (
                                  <button
                                    type="button"
                                    onClick={() => handleAddonUndo(opt.priceId)}
                                    disabled={!addonsInteractable || saving}
                                    className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                                  >
                                    Undo
                                  </button>
                                ) : null}
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    ) : (
                      <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                        No {section.title.toLowerCase()} are currently active.
                      </div>
                    )}

                    {selectableOptions.length ? (
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <div className="relative flex-1">
                          <select
                            value={selected}
                            onChange={(event) =>
                              setSelectedAddonByKind((prev) => ({ ...prev, [section.key]: event.target.value }))
                            }
                            disabled={!addonsInteractable || saving}
                            className="w-full appearance-none rounded-xl border border-slate-200 bg-white px-3 py-2.5 pr-10 text-sm font-semibold text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-500/30 disabled:opacity-50"
                            aria-label={`Select ${section.title} option`}
                          >
                            <option value="">Select an option…</option>
                            {selectableOptions.map((opt) => (
                              <option key={opt.priceId} value={opt.priceId}>
                                {buildAddonOptionLabel(section.key, opt)}
                                {opt.priceDisplay ? ` (${opt.priceDisplay}/mo)` : ''}
                              </option>
                            ))}
                          </select>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleAddonAdd(section.key)}
                          disabled={!addonsInteractable || saving || !selected}
                          className="inline-flex items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:opacity-50"
                        >
                          <Plus className="h-4 w-4" />
                          Add
                        </button>
                      </div>
                    ) : (
                      <div className="text-sm text-slate-600">
                        No options are configured for this add-on.
                      </div>
                    )}
                  </div>
                </div>
              )
            })}

            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
              <div className="flex items-start gap-3 sm:w-72">
                <div className="mt-0.5 grid h-11 w-11 place-items-center rounded-2xl bg-indigo-50 text-indigo-700">
                  <GlobeLock className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <div className="text-base font-bold text-slate-900">Dedicated IPs</div>
                  <div className="mt-1 text-sm text-slate-600">
                    Reserved static IP addresses for this subscription.
                  </div>
                </div>
              </div>

              <div className="min-w-0 flex-1 space-y-4">
                <div className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-semibold text-slate-900">
                      {effectiveDedicatedCount} reserved
                    </div>
                    {initialData.dedicatedIps.unitPrice ? (
                      <div className="text-xs font-semibold text-slate-500">
                        {formatCents(Math.round(initialData.dedicatedIps.unitPrice * 100), normalizeCurrency(initialData.dedicatedIps.currency || planCurrency))}/IP/mo
                      </div>
                    ) : null}
                  </div>
                  {!initialData.dedicatedIps.multiAssign ? (
                    <div className="text-xs text-amber-700">
                      Each dedicated IP can be assigned to only one agent at a time.
                    </div>
                  ) : null}
                </div>

                <div className="space-y-2">
                  {initialData.dedicatedIps.proxies.length ? (
                    initialData.dedicatedIps.proxies.map((proxy) => {
                      const stagedRemove = dedicatedRemoveIds.has(proxy.id)
                      const label = proxy.label || proxy.staticIp || proxy.host
                      return (
                        <div
                          key={proxy.id}
                          className="flex flex-col gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                        >
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <div className="text-sm font-semibold text-slate-900">{label}</div>
                              {stagedRemove ? (
                                <div className="text-xs font-semibold text-amber-700">Will be removed</div>
                              ) : null}
                            </div>
                            {proxy.assignedAgents.length ? (
                              <div className="mt-1 text-xs text-slate-600">
                                In use by {proxy.assignedAgents.map((a) => a.name).join(', ')}
                              </div>
                            ) : (
                              <div className="mt-1 text-xs text-slate-600">Not assigned to any agents.</div>
                            )}
                          </div>

                          <div className="flex flex-wrap items-center gap-2">
                            {stagedRemove ? (
                              <button
                                type="button"
                                onClick={() => handleDedicatedUndoRemove(proxy.id)}
                                disabled={saving}
                                className="inline-flex items-center justify-center rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                              >
                                Undo
                              </button>
                            ) : (
                              <button
                                type="button"
                                onClick={() => promptDedicatedRemove(proxy)}
                                disabled={!dedicatedInteractable || saving}
                                className="inline-flex items-center justify-center rounded-xl border border-rose-200 bg-white px-3 py-2 text-sm font-semibold text-rose-700 transition hover:border-rose-300 disabled:opacity-50"
                              >
                                Remove
                              </button>
                            )}
                          </div>
                        </div>
                      )
                    })
                  ) : (
                    <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
                      No dedicated IPs are currently provisioned.
                    </div>
                  )}
                </div>

                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                  <div className="flex flex-1 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setDedicatedAddQty((prev) => Math.max(0, prev - 1))}
                      disabled={!dedicatedInteractable || saving || dedicatedAddQty <= 0}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                      aria-label="Decrease dedicated IP quantity to add"
                    >
                      <Minus className="h-4 w-4" strokeWidth={3} />
                    </button>
                    <div className="min-w-[5.5rem] rounded-xl border border-slate-200 bg-white px-4 py-2 text-center text-lg font-bold text-slate-900 tabular-nums">
                      {dedicatedAddQty}
                    </div>
                    <button
                      type="button"
                      onClick={() => setDedicatedAddQty((prev) => Math.min(99, prev + 1))}
                      disabled={!dedicatedInteractable || saving}
                      className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-slate-200 bg-white text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
                      aria-label="Increase dedicated IP quantity to add"
                    >
                      <Plus className="h-4 w-4" strokeWidth={3} />
                    </button>
                  </div>

                  <div className="text-sm text-slate-600">
                    Add this many new dedicated IPs (staged).
                  </div>
                </div>

                {!initialData.dedicatedIps.allowed ? (
                  <div className="rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    Dedicated IPs require a paid plan.
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </section>

        <section className="card" data-section="billing-summary">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-slate-700">
              <CreditCard className="h-4 w-4 text-slate-500" />
              <span>Subscription Summary</span>
            </div>
          </div>

          <div className="mt-5 space-y-2">
            {summaryItems.map((item) => (
              <div
                key={item.id}
                className="flex flex-col gap-1 rounded-2xl border border-slate-200 bg-white px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="text-sm font-semibold text-slate-900">{item.label}</div>
                <div className="text-sm font-bold text-slate-900 tabular-nums">
                  {formatCents(item.money.amountCents, item.money.currency)}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-6 border-t border-slate-200 pt-4">
            <div className="flex items-center justify-between gap-3 rounded-2xl bg-slate-900 px-4 py-3 text-white">
              <div className="text-sm font-semibold">Total per month</div>
              <div className="text-lg font-extrabold tabular-nums">
                {formatCents(summaryTotal.amountCents, summaryTotal.currency)}
              </div>
            </div>
          </div>
        </section>
      </main>

      <SaveBar
        id="billing-save-bar"
        visible={hasAnyChanges}
        onCancel={resetDraft}
        onSave={handleSave}
        busy={saving}
        error={saveError}
      />

      {isUpgradeModalOpen && !isOrg && isProprietaryMode ? (
        <SubscriptionUpgradeModal
          currentPlan={currentPlan}
          onClose={closeUpgradeModal}
          onUpgrade={handleUpgrade}
          source={upgradeModalSource ?? undefined}
          dismissible={upgradeModalDismissible}
        />
      ) : null}

      <ConfirmDialog
        open={cancelModalOpen}
        title="Cancel subscription"
        description={
          <>
            You will keep access until the end of your current billing period.
            {cancelError ? <div className="mt-2 text-sm font-semibold text-rose-700">{cancelError}</div> : null}
          </>
        }
        confirmLabel="Cancel subscription"
        cancelLabel="Keep subscription"
        icon={<ShieldAlert className="h-5 w-5" />}
        busy={cancelBusy}
        danger
        onConfirm={handleCancelSubscription}
        onClose={() => (cancelBusy ? null : setCancelModalOpen(false))}
      />

      <ConfirmDialog
        open={Boolean(dedicatedPrompt)}
        title="Remove dedicated IP"
        description={
          dedicatedPrompt ? (
            <>
              This IP is currently assigned to agents. Removing it can break workflows unless you unassign first.
              <div className="mt-2 text-sm font-semibold text-slate-900">{dedicatedPrompt.proxyLabel}</div>
            </>
          ) : null
        }
        confirmLabel="Remove IP"
        icon={<GlobeLock className="h-5 w-5" />}
        danger
        onConfirm={confirmDedicatedRemove}
        onClose={() => setDedicatedPrompt(null)}
        footerNote="Changes are staged until you click Save."
      >
        {dedicatedPrompt ? (
          <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700">
            <div className="font-semibold text-slate-900">Assigned agents</div>
            <div className="mt-1 text-sm text-slate-700">
              {dedicatedPrompt.assignedAgents.map((a) => a.name).join(', ')}
            </div>
            <label className="mt-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
              <input
                type="checkbox"
                checked={dedicatedPrompt.unassign}
                onChange={(event) =>
                  setDedicatedPrompt((prev) => (prev ? { ...prev, unassign: event.target.checked } : prev))
                }
              />
              Unassign from these agents
            </label>
          </div>
        ) : null}
      </ConfirmDialog>
    </div>
  )
}
