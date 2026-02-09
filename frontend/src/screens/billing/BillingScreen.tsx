import { useCallback, useMemo, useReducer, useState } from 'react'
import { CreditCard, GlobeLock, ShieldAlert } from 'lucide-react'

import { jsonRequest } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { SubscriptionUpgradeModal } from '../../components/common/SubscriptionUpgradeModal'
import { type PlanTier, useSubscriptionStore } from '../../stores/subscriptionStore'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'

import type { BillingInitialData, BillingScreenProps, DedicatedIpProxy } from './types'
import { billingDraftReducer, initialDraftState, type BillingDraftState } from './draft'
import { buildInitialAddonQuantityMap } from './utils'
import { BillingHeader } from './BillingHeader'
import { AddonSections } from './AddonSections'
import { ExtraTasksSection } from './ExtraTasksSection'
import { SubscriptionSummary } from './SubscriptionSummary'
import { ConfirmDialog } from './ConfirmDialog'
import { useBillingNudgeVisibility } from './useBillingNudgeVisibility'
import { useConfirmPostAction } from './useConfirmPostAction'

type DedicatedRemovePrompt = {
  proxyId: string
  proxyLabel: string
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
  } = useSubscriptionStore()

  const [draft, dispatch] = useReducer(billingDraftReducer, initialDraftState(initialData))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const [dedicatedPrompt, setDedicatedPrompt] = useState<DedicatedRemovePrompt | null>(null)
  const [trialConfirmOpen, setTrialConfirmOpen] = useState(false)
  const [trialConfirmPayload, setTrialConfirmPayload] = useState<Record<string, unknown> | null>(null)
  const [planConfirmOpen, setPlanConfirmOpen] = useState(false)
  const [planConfirmTarget, setPlanConfirmTarget] = useState<PlanTier | null>(null)
  const [planConfirmBusy, setPlanConfirmBusy] = useState(false)
  const [planConfirmError, setPlanConfirmError] = useState<string | null>(null)

  const addonsDisabledReason = useMemo(() => computeAddonsDisabledReason(initialData), [initialData])
  const addonsInteractable = useMemo(() => computeAddonsInteractable(initialData), [initialData])
  const dedicatedInteractable = useMemo(() => computeDedicatedInteractable(initialData), [initialData])

  const hasAnyChanges = useMemo(() => isDraftDirty(initialData, draft), [draft, initialData])

  const { summaryActionsVisible, nearTop } = useBillingNudgeVisibility({ enabled: hasAnyChanges })

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
      })
      return
    }

    dispatch({ type: 'dedicated.stageRemove', proxy })
  }, [dedicatedInteractable, draft.dedicatedRemoveIds, dispatch])

  const confirmDedicatedRemove = useCallback(() => {
    if (!dedicatedPrompt) return
    const proxy = initialData.dedicatedIps.proxies.find((p) => p.id === dedicatedPrompt.proxyId)
    if (proxy) {
      dispatch({ type: 'dedicated.stageRemove', proxy })
    }
    setDedicatedPrompt(null)
  }, [dedicatedPrompt, dispatch, initialData.dedicatedIps.proxies])

  const handlePlanSelect = useCallback((plan: PlanTier) => {
    track(AnalyticsEvent.UPGRADE_PLAN_SELECTED, {
      plan,
      source: upgradeModalSource ?? 'billing',
    })
    closeUpgradeModal()
    setPlanConfirmTarget(plan)
    setPlanConfirmError(null)
    setPlanConfirmOpen(true)
  }, [closeUpgradeModal, upgradeModalSource])

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

  const cancelUrl = initialData.contextType === 'personal' ? initialData.endpoints.cancelSubscriptionUrl : undefined
  const resumeUrl = initialData.contextType === 'personal' ? initialData.endpoints.resumeSubscriptionUrl : undefined
  const cancelAction = useConfirmPostAction({ url: cancelUrl, defaultErrorMessage: 'Unable to cancel subscription.' })
  const resumeAction = useConfirmPostAction({ url: resumeUrl, defaultErrorMessage: 'Unable to resume subscription.' })

  const dismissPlanConfirm = useCallback(() => {
    setPlanConfirmOpen(false)
    setPlanConfirmTarget(null)
    setPlanConfirmBusy(false)
    setPlanConfirmError(null)
  }, [])

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
          onCancel={!isOrg && initialData.contextType === 'personal' && initialData.paidSubscriber ? cancelAction.openDialog : undefined}
          onResume={!isOrg
            && initialData.contextType === 'personal'
            && initialData.paidSubscriber
            && initialData.cancelAtPeriodEnd
            && initialData.endpoints.resumeSubscriptionUrl
            ? resumeAction.openDialog
            : undefined}
          seatTarget={initialData.contextType === 'organization' ? (draft.seatTarget ?? initialData.seats.purchased) : undefined}
          saving={saving}
          onAdjustSeat={initialData.contextType === 'organization' ? handleSeatAdjust : undefined}
          onCancelScheduledSeatChange={initialData.contextType === 'organization' ? handleCancelSeatSchedule : undefined}
        />

        <AddonSections
          initialData={initialData}
          draft={draft}
          dispatch={dispatch}
          saving={saving}
          addonsInteractable={addonsInteractable}
          addonsDisabledReason={addonsDisabledReason}
          dedicatedInteractable={dedicatedInteractable}
          onRequestDedicatedRemove={requestDedicatedRemove}
        />

        <SubscriptionSummary
          initialData={initialData}
          draft={draft}
          showActions={hasAnyChanges}
          saving={saving}
          error={saveError}
          onSave={handleSave}
          onCancel={resetDraft}
        />

        <ExtraTasksSection initialData={initialData} />
      </main>

      {hasAnyChanges && !summaryActionsVisible && nearTop ? (
        <div className="fixed inset-x-0 bottom-0 z-40 px-4 pb-4 sm:px-6">
          <div className="mx-auto flex w-full max-w-5xl items-center justify-between gap-3 rounded-2xl bg-slate-900 px-4 py-3 text-white shadow-lg">
            <div className="min-w-0 text-sm font-semibold">
              You have unsaved changes.
            </div>
            <button
              type="button"
              onClick={() => document.getElementById('billing-summary')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
              className="inline-flex flex-none items-center justify-center rounded-xl bg-white/10 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/15"
            >
              Review and update
            </button>
          </div>
        </div>
      ) : null}

      {isUpgradeModalOpen && !isOrg && isProprietaryMode ? (
        <SubscriptionUpgradeModal
          currentPlan={currentPlan}
          onClose={closeUpgradeModal}
          onUpgrade={handlePlanSelect}
          source={upgradeModalSource ?? undefined}
          dismissible={upgradeModalDismissible}
          allowDowngrade
        />
      ) : null}

      <ConfirmDialog
        open={cancelAction.open}
        title="Cancel subscription"
        description={
          <>
            You will keep access until the end of your current billing period.
            {cancelAction.error ? <div className="mt-2 text-sm font-semibold text-rose-700">{cancelAction.error}</div> : null}
          </>
        }
        confirmLabel="Cancel subscription"
        cancelLabel="Keep subscription"
        icon={<ShieldAlert className="h-5 w-5" />}
        busy={cancelAction.busy}
        danger
        onConfirm={cancelAction.confirm}
        onClose={cancelAction.closeDialog}
      />

      <ConfirmDialog
        open={resumeAction.open}
        title="Resume subscription?"
        description={
          <>
            Your subscription will stay active and renew normally.
            {resumeAction.error ? <div className="mt-2 text-sm font-semibold text-rose-700">{resumeAction.error}</div> : null}
          </>
        }
        confirmLabel="Resume subscription"
        cancelLabel="Keep cancellation"
        icon={<ShieldAlert className="h-5 w-5" />}
        busy={resumeAction.busy}
        onConfirm={resumeAction.confirm}
        onClose={resumeAction.closeDialog}
      />

      <ConfirmDialog
        open={Boolean(dedicatedPrompt)}
        title="Remove dedicated IP"
        description={
          dedicatedPrompt ? (
            <>
              This IP is currently assigned to agents. Removing it will automatically unassign it from all of your agents.
              <div className="mt-2 text-sm font-semibold text-slate-900">{dedicatedPrompt.proxyLabel}</div>
            </>
          ) : null
        }
        confirmLabel="Remove IP"
        icon={<GlobeLock className="h-5 w-5" />}
        danger
        onConfirm={confirmDedicatedRemove}
        onClose={() => setDedicatedPrompt(null)}
        footerNote="Changes apply when you click Save."
      />

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

      <ConfirmDialog
        open={planConfirmOpen}
        title={planConfirmTarget === 'startup' ? 'Switch to Pro?' : 'Switch to Scale?'}
        description={
          <>
            This changes your base subscription plan immediately.
            {hasAnyChanges ? (
              <div className="mt-2 text-sm font-semibold text-amber-800">
                Save or cancel your changes below before switching plans.
              </div>
            ) : null}
            {planConfirmError ? (
              <div className="mt-2 text-sm font-semibold text-rose-700">
                {planConfirmError}
              </div>
            ) : null}
          </>
        }
        confirmLabel="Continue"
        cancelLabel="Back"
        confirmDisabled={hasAnyChanges || planConfirmBusy}
        busy={planConfirmBusy}
        onConfirm={async () => {
          if (!planConfirmTarget) return
          if (hasAnyChanges) return
          setPlanConfirmBusy(true)
          setPlanConfirmError(null)
          try {
            const result = await jsonRequest<{ ok: boolean; stripeActionUrl?: string }>(
              initialData.endpoints.updateUrl,
              {
                method: 'POST',
                includeCsrf: true,
                json: { ownerType: 'user', planTarget: planConfirmTarget },
              },
            )
            if (result?.stripeActionUrl) {
              window.location.assign(result.stripeActionUrl)
              return
            }
            window.location.reload()
          } catch (error) {
            setPlanConfirmError(safeErrorMessage(error))
          } finally {
            setPlanConfirmBusy(false)
          }
        }}
        onClose={dismissPlanConfirm}
      />
    </div>
  )
}
