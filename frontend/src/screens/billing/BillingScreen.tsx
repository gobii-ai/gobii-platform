import { useCallback, useMemo, useReducer, useState } from 'react'
import { CreditCard, GlobeLock, ShieldAlert } from 'lucide-react'

import { HttpError, jsonRequest } from '../../api/http'
import { SaveBar } from '../../components/common/SaveBar'
import { SubscriptionUpgradeModal } from '../../components/common/SubscriptionUpgradeModal'
import { type PlanTier, useSubscriptionStore } from '../../stores/subscriptionStore'
import { appendReturnTo } from '../../util/returnTo'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'

import type { BillingInitialData, BillingScreenProps, DedicatedIpAssignedAgent, DedicatedIpProxy } from './types'
import { billingDraftReducer, initialDraftState, type BillingDraftState } from './draft'
import { buildInitialAddonQuantityMap } from './utils'
import { BillingHeader } from './BillingHeader'
import { SeatManager } from './SeatManager'
import { AddonSections } from './AddonSections'
import { DedicatedIpSection } from './DedicatedIpSection'
import { SubscriptionSummary } from './SubscriptionSummary'
import { ConfirmDialog } from './ConfirmDialog'

type DedicatedRemovePrompt = {
  proxyId: string
  proxyLabel: string
  assignedAgents: DedicatedIpAssignedAgent[]
  unassign: boolean
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

function computeAddonsDisabledReason(initialData: BillingInitialData): string | null {
  if (!initialData.canManageBilling) return 'You do not have permission to manage billing.'
  if (initialData.addonsDisabled) return 'Add-ons are unavailable for this subscription.'
  if (initialData.contextType === 'organization' && initialData.seats.purchased <= 0) {
    return 'Purchase at least one seat to manage add-ons.'
  }
  return null
}

function computeDedicatedInteractable(initialData: BillingInitialData): boolean {
  if (!initialData.canManageBilling) return false
  if (!initialData.dedicatedIps.allowed) return false
  if (initialData.contextType === 'organization' && initialData.seats.purchased <= 0) return false
  return true
}

function computeAddonsInteractable(initialData: BillingInitialData): boolean {
  if (!initialData.canManageBilling) return false
  if (initialData.addonsDisabled) return false
  if (initialData.contextType === 'organization' && initialData.seats.purchased <= 0) return false
  return true
}

function isDraftDirty(initialData: BillingInitialData, draft: BillingDraftState): boolean {
  const initialAddons = buildInitialAddonQuantityMap(initialData.addons)
  const keys = Object.keys({ ...initialAddons, ...draft.addonQuantities })
  const addonsDirty = keys.some((key) => (draft.addonQuantities[key] ?? 0) !== (initialAddons[key] ?? 0))

  const dedicatedDirty = draft.dedicatedAddQty > 0 || draft.dedicatedRemoveIds.length > 0

  const seatsDirty = initialData.contextType === 'organization'
    ? (draft.seatTarget ?? initialData.seats.purchased) !== initialData.seats.purchased || draft.cancelSeatSchedule
    : false

  return addonsDirty || dedicatedDirty || seatsDirty
}

export function BillingScreen({ initialData }: BillingScreenProps) {
  const isOrg = initialData.contextType === 'organization'
  const trialEndsLabel = useMemo(() => {
    const iso = initialData.trial?.trialEndsAtIso
    if (!iso) return null
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return null
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', year: 'numeric' }).format(d)
  }, [initialData.trial?.trialEndsAtIso])

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

  const [draft, dispatch] = useReducer(billingDraftReducer, initialDraftState(initialData))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [cancelModalOpen, setCancelModalOpen] = useState(false)
  const [cancelBusy, setCancelBusy] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)

  const [dedicatedPrompt, setDedicatedPrompt] = useState<DedicatedRemovePrompt | null>(null)
  const [trialConfirmOpen, setTrialConfirmOpen] = useState(false)
  const [trialConfirmPayload, setTrialConfirmPayload] = useState<Record<string, unknown> | null>(null)

  const addonsDisabledReason = useMemo(() => computeAddonsDisabledReason(initialData), [initialData])
  const addonsInteractable = useMemo(() => computeAddonsInteractable(initialData), [initialData])
  const dedicatedInteractable = useMemo(() => computeDedicatedInteractable(initialData), [initialData])

  const hasAnyChanges = useMemo(() => isDraftDirty(initialData, draft), [draft, initialData])

  const resetDraft = useCallback(() => {
    setSaveError(null)
    dispatch({ type: 'reset', initialData })
  }, [initialData])

  const handleSeatAdjust = useCallback((delta: number) => {
    if (!isOrg) return
    dispatch({ type: 'seat.adjust', delta, min: Math.max(0, initialData.seats.reserved) })
  }, [initialData, isOrg])

  const handleCancelSeatSchedule = useCallback(() => {
    if (!isOrg) return
    dispatch({ type: 'seat.setTarget', value: initialData.seats.purchased })
    dispatch({ type: 'seat.cancelSchedule' })
  }, [initialData, isOrg])

  const requestDedicatedRemove = useCallback((proxy: DedicatedIpProxy) => {
    if (!dedicatedInteractable) return
    if (draft.dedicatedRemoveIds.includes(proxy.id)) return

    if (proxy.assignedAgents.length) {
      setDedicatedPrompt({
        proxyId: proxy.id,
        proxyLabel: proxy.label || proxy.staticIp || proxy.host,
        assignedAgents: proxy.assignedAgents,
        unassign: true,
      })
      return
    }

    dispatch({ type: 'dedicated.stageRemove', proxy, unassign: false })
  }, [dedicatedInteractable, draft.dedicatedRemoveIds, dispatch])

  const confirmDedicatedRemove = useCallback(() => {
    if (!dedicatedPrompt) return
    const proxy = initialData.dedicatedIps.proxies.find((p) => p.id === dedicatedPrompt.proxyId)
    if (proxy) {
      dispatch({ type: 'dedicated.stageRemove', proxy, unassign: dedicatedPrompt.unassign })
    }
    setDedicatedPrompt(null)
  }, [dedicatedPrompt, dispatch, initialData.dedicatedIps.proxies])

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

  const submitSave = useCallback(async (payload: Record<string, unknown>) => {
    if (saving) return
    setSaving(true)
    setSaveError(null)
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
  }, [initialData.endpoints.updateUrl, saving])

  const handleSave = useCallback(async () => {
    const payload: Record<string, unknown> = {}
    if (initialData.contextType === 'organization') {
      payload.ownerType = 'organization'
      payload.organizationId = initialData.organization.id
      payload.seatsTarget = draft.seatTarget
      payload.cancelSeatSchedule = draft.cancelSeatSchedule
    } else {
      payload.ownerType = 'user'
    }

    const initialAddons = buildInitialAddonQuantityMap(initialData.addons)
    const addonDiff: Record<string, number> = {}
    let addonPurchase = false
    const addonKeys = Object.keys({ ...initialAddons, ...draft.addonQuantities })
    addonKeys.forEach((key) => {
      const nextQty = draft.addonQuantities[key] ?? 0
      const initialQty = initialAddons[key] ?? 0
      if (nextQty !== initialQty) {
        addonDiff[key] = nextQty
      }
      if (nextQty > initialQty) {
        addonPurchase = true
      }
    })
    if (Object.keys(addonDiff).length && addonsInteractable) {
      payload.addonQuantities = addonDiff
    } else {
      addonPurchase = false
    }

    const dedicatedPurchase = Boolean(dedicatedInteractable && draft.dedicatedAddQty > 0)
    if ((draft.dedicatedAddQty > 0 || draft.dedicatedRemoveIds.length) && dedicatedInteractable) {
      payload.dedicatedIps = {
        addQuantity: draft.dedicatedAddQty,
        removeProxyIds: draft.dedicatedRemoveIds,
        unassignProxyIds: draft.dedicatedUnassignIds,
      }
    }

    const trialing = Boolean(initialData.trial?.isTrialing)
    if (trialing && (addonPurchase || dedicatedPurchase) && !trialConfirmOpen) {
      setTrialConfirmPayload(payload)
      setTrialConfirmOpen(true)
      return
    }

    await submitSave(payload)
  }, [addonsInteractable, dedicatedInteractable, draft, initialData, submitSave, trialConfirmOpen])

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
        <BillingHeader
          initialData={initialData}
          onChangePlan={!isOrg && isProprietaryMode ? () => openUpgradeModal('unknown') : undefined}
          onCancel={!isOrg && initialData.contextType === 'personal' && initialData.paidSubscriber ? () => setCancelModalOpen(true) : undefined}
        />

        {initialData.contextType === 'organization' ? (
          <section className="card">
            <SeatManager
              initialData={initialData}
              seatTarget={draft.seatTarget ?? initialData.seats.purchased}
              canManage={initialData.canManageBilling}
              saving={saving}
              onAdjust={handleSeatAdjust}
              onCancelScheduledChange={handleCancelSeatSchedule}
            />
          </section>
        ) : null}

        <AddonSections
          initialData={initialData}
          draft={draft}
          dispatch={dispatch}
          saving={saving}
          addonsInteractable={addonsInteractable}
          addonsDisabledReason={addonsDisabledReason}
        />

        <section className="card" data-section="billing-dedicated">
          <DedicatedIpSection
            initialData={initialData}
            draft={draft}
            dispatch={dispatch}
            saving={saving}
            dedicatedInteractable={dedicatedInteractable}
            onRequestRemove={requestDedicatedRemove}
          />
        </section>

        <SubscriptionSummary initialData={initialData} draft={draft} />
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
        onConfirm={() => {
          if (!trialConfirmPayload) return
          setTrialConfirmOpen(false)
          const payload = trialConfirmPayload
          setTrialConfirmPayload(null)
          submitSave(payload)
        }}
        onClose={() => {
          setTrialConfirmOpen(false)
          setTrialConfirmPayload(null)
        }}
      />
    </div>
  )
}
