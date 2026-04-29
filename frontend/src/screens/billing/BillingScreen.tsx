import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import { AlertTriangle, CreditCard, GlobeLock, ShieldAlert } from 'lucide-react'

import { getCsrfToken, jsonRequest } from '../../api/http'
import { safeErrorMessage } from '../../api/safeErrorMessage'
import { SaveBar } from '../../components/common/SaveBar'
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

const CANCEL_FEEDBACK_MAX_LENGTH = 500

type CancelReasonCode =
  | ''
  | 'too_expensive'
  | 'missing_features'
  | 'reliability_issues'
  | 'switching_tools'
  | 'no_longer_needed'
  | 'other'

const CANCEL_REASON_OPTIONS: Array<{ value: Exclude<CancelReasonCode, ''>; label: string }> = [
  { value: 'too_expensive', label: 'Too expensive' },
  { value: 'missing_features', label: 'Missing features I need' },
  { value: 'reliability_issues', label: 'Reliability or performance issues' },
  { value: 'switching_tools', label: 'Switching to another tool' },
  { value: 'no_longer_needed', label: 'No longer need it' },
  { value: 'other', label: 'Other' },
]

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
}

function buildChurnKeyBaseAnalytics(initialData: BillingInitialData) {
  const churnKeyConfig = initialData.contextType === 'personal' ? initialData.churnKey : null
  return {
    source: 'billing_header',
    provider: churnKeyConfig?.provider ?? 'stripe',
    mode: churnKeyConfig?.mode ?? null,
    hasSubscriptionId: Boolean(churnKeyConfig?.subscriptionId),
    planId: String(initialData.plan?.id ?? ''),
    isTrialing: Boolean(initialData.trial?.isTrialing),
  }
}

function buildChurnKeySessionAnalytics(sessionResults: unknown): Record<string, unknown> {
  const session = asRecord(sessionResults)
  const acceptedOffer = asRecord(session?.acceptedOffer)
  return {
    result: typeof session?.result === 'string' ? session.result : null,
    sessionMode: typeof session?.mode === 'string' ? session.mode : null,
    surveyResponse: typeof session?.surveyResponse === 'string' ? session.surveyResponse : null,
    followupQuestion: typeof session?.followupQuestion === 'string' ? session.followupQuestion : null,
    hasFollowupResponse: typeof session?.followupResponse === 'string' ? session.followupResponse.length > 0 : Boolean(session?.followupResponse),
    hasFeedback: typeof session?.feedback === 'string' ? session.feedback.length > 0 : Boolean(session?.feedback),
    usedClickToCancel: Boolean(session?.usedClickToCancel),
    acceptedOfferType: typeof acceptedOffer?.offerType === 'string' ? acceptedOffer.offerType : null,
    pauseDuration: typeof acceptedOffer?.pauseDuration === 'number' ? acceptedOffer.pauseDuration : null,
    trialExtensionDays: typeof acceptedOffer?.trialExtensionDays === 'number' ? acceptedOffer.trialExtensionDays : null,
    newPlanId: typeof acceptedOffer?.newPlanId === 'string' ? acceptedOffer.newPlanId : null,
    redirectUrlPresent: typeof acceptedOffer?.redirectUrl === 'string' && acceptedOffer.redirectUrl.length > 0,
    couponId: typeof acceptedOffer?.couponId === 'string' ? acceptedOffer.couponId : null,
    couponType: typeof acceptedOffer?.couponType === 'string' ? acceptedOffer.couponType : null,
    couponAmount: typeof acceptedOffer?.couponAmount === 'number' ? acceptedOffer.couponAmount : null,
    couponDuration: typeof acceptedOffer?.couponDuration === 'number' ? acceptedOffer.couponDuration : null,
  }
}

async function waitForChurnKeyReady(timeoutMs = 1500): Promise<boolean> {
  if (typeof window.churnkey?.init === 'function') {
    return true
  }

  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    await new Promise((resolve) => window.setTimeout(resolve, 50))
    if (typeof window.churnkey?.init === 'function') {
      return true
    }
  }

  return false
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

export function BillingScreen({ initialData, variant = 'standalone' }: BillingScreenProps) {
  const isOrg = initialData.contextType === 'organization'
  const isEmbedded = variant === 'embedded'
  const rootClassName = isEmbedded ? 'billing-screen billing-screen--embedded grid w-full gap-6' : 'billing-screen app-shell'
  const mainClassName = isEmbedded ? 'billing-screen__main grid gap-6' : 'billing-screen__main app-main'
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
  const [cancelReason, setCancelReason] = useState<CancelReasonCode>('')
  const [cancelFeedback, setCancelFeedback] = useState('')

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

  const scrollToBillingSummary = useCallback(() => {
    document.getElementById('billing-summary')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

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

  const handleFreeUpgradeClick = useCallback(() => {
    track(AnalyticsEvent.CTA_FREE_UPGRADE_PLAN, {
      source: 'billing',
    })
    window.location.assign('/pricing/')
  }, [])

  const showPlanAction = !isOrg && isProprietaryMode && initialData.contextType === 'personal'
  const handlePlanActionClick = useCallback(() => {
    if (!showPlanAction) return
    if (initialData.paidSubscriber) {
      openUpgradeModal('unknown')
      return
    }
    handleFreeUpgradeClick()
  }, [showPlanAction, initialData.paidSubscriber, openUpgradeModal, handleFreeUpgradeClick])

  const handleManageInStripe = useCallback(() => {
    const stripePortalUrl = initialData.endpoints.stripePortalUrl
    if (!stripePortalUrl || typeof document === 'undefined') {
      return
    }

    const form = document.createElement('form')
    form.method = 'POST'
    form.action = stripePortalUrl
    form.target = '_top'

    const csrfToken = getCsrfToken()
    if (csrfToken) {
      const csrfInput = document.createElement('input')
      csrfInput.type = 'hidden'
      csrfInput.name = 'csrfmiddlewaretoken'
      csrfInput.value = csrfToken
      form.appendChild(csrfInput)
    }

    document.body.appendChild(form)
    form.submit()
    form.remove()
  }, [initialData.endpoints.stripePortalUrl])

  useEffect(() => {
    const appId = initialData.contextType === 'personal' ? initialData.churnKey?.appId : null
    if (!appId || typeof document === 'undefined') {
      return
    }
    if (typeof window.churnkey?.init === 'function') {
      return
    }
    const existingScript = document.querySelector<HTMLScriptElement>(
      `script[data-churnkey-app-id="${appId}"], script[src*="assets.churnkey.co/js/app.js?appId=${appId}"]`,
    )
    if (existingScript) {
      return
    }
    const script = document.createElement('script')
    script.src = `https://assets.churnkey.co/js/app.js?appId=${encodeURIComponent(appId)}`
    script.async = true
    script.dataset.churnkeyAppId = appId
    const firstScript = document.getElementsByTagName('script')[0]
    if (firstScript?.parentNode) {
      firstScript.parentNode.insertBefore(script, firstScript)
      return
    }
    document.body.appendChild(script)
  }, [initialData])

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
  const churnKeySyncUrl = initialData.contextType === 'personal' ? initialData.endpoints.churnKeySyncUrl : undefined
  const resumeUrl = initialData.contextType === 'personal' ? initialData.endpoints.resumeSubscriptionUrl : undefined
  const cancelAction = useConfirmPostAction({ url: cancelUrl, defaultErrorMessage: 'Unable to cancel subscription.' })
  const resumeAction = useConfirmPostAction({ url: resumeUrl, defaultErrorMessage: 'Unable to resume subscription.' })
  const {
    openDialog: openCancelActionDialog,
    closeDialog: closeCancelActionDialog,
    busy: cancelActionBusy,
  } = cancelAction

  const resetCancelFeedback = useCallback(() => {
    setCancelReason('')
    setCancelFeedback('')
  }, [])

  const openCancelDialog = useCallback(() => {
    resetCancelFeedback()
    openCancelActionDialog()
  }, [openCancelActionDialog, resetCancelFeedback])

  const churnKeyConfig = initialData.contextType === 'personal' ? initialData.churnKey : null
  const churnKeyAnalyticsBase = useMemo(() => buildChurnKeyBaseAnalytics(initialData), [initialData])

  const openCancelFlow = useCallback(async () => {
    setSaveError(null)

    if (!churnKeyConfig?.enabled) {
      track(AnalyticsEvent.BILLING_CANCEL_FLOW_ERROR, {
        ...churnKeyAnalyticsBase,
        errorType: 'missing_config',
        errorMessage: 'ChurnKey config unavailable for billing page.',
        fallback: 'native_cancel_modal',
      })
      openCancelDialog()
      return
    }

    const churnKeyReady = await waitForChurnKeyReady()
    if (!churnKeyReady || typeof window.churnkey?.init !== 'function') {
      track(AnalyticsEvent.BILLING_CANCEL_FLOW_ERROR, {
        ...churnKeyAnalyticsBase,
        errorType: 'script_not_ready',
        errorMessage: 'ChurnKey script has not finished loading.',
        fallback: 'native_cancel_modal',
      })
      openCancelDialog()
      return
    }

    let shouldRefreshOnClose = false
    let shouldSyncSubscriptionState = false
    const markMutation = () => {
      shouldRefreshOnClose = true
    }

    try {
      track(AnalyticsEvent.BILLING_CANCEL_FLOW_OPENED, churnKeyAnalyticsBase)

      window.churnkey.init('show', {
        appId: churnKeyConfig.appId,
        customerId: churnKeyConfig.customerId,
        authHash: churnKeyConfig.authHash,
        subscriptionId: churnKeyConfig.subscriptionId,
        mode: churnKeyConfig.mode,
        provider: churnKeyConfig.provider,
        record: true,
        onCancel: (_customer, surveyResponse) => {
          markMutation()
          shouldSyncSubscriptionState = true
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ACTION_SELECTED, {
            ...churnKeyAnalyticsBase,
            action: 'cancel',
            surveyResponse: surveyResponse ?? null,
          })
        },
        onPause: (_customer, data) => {
          markMutation()
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ACTION_SELECTED, {
            ...churnKeyAnalyticsBase,
            action: 'pause',
            pauseDuration: data?.pauseDuration ?? null,
          })
        },
        onDiscount: (_customer, coupon) => {
          markMutation()
          shouldSyncSubscriptionState = true
          const couponRecord = asRecord(coupon)
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ACTION_SELECTED, {
            ...churnKeyAnalyticsBase,
            action: 'discount',
            couponId: typeof couponRecord?.couponId === 'string' ? couponRecord.couponId : null,
            couponType: typeof couponRecord?.couponType === 'string' ? couponRecord.couponType : null,
            couponAmount: typeof couponRecord?.couponAmount === 'number' ? couponRecord.couponAmount : null,
            couponDuration: typeof couponRecord?.couponDuration === 'number' ? couponRecord.couponDuration : null,
          })
        },
        onPlanChange: (_customer, data) => {
          markMutation()
          shouldSyncSubscriptionState = true
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ACTION_SELECTED, {
            ...churnKeyAnalyticsBase,
            action: 'plan_change',
            targetPlanId: data?.planId ?? null,
          })
        },
        onTrialExtension: (_customer, data) => {
          markMutation()
          shouldSyncSubscriptionState = true
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ACTION_SELECTED, {
            ...churnKeyAnalyticsBase,
            action: 'trial_extension',
            trialExtensionDays: data?.trialExtensionDays ?? null,
          })
        },
        onGoToAccount: (sessionResults) => {
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_GO_TO_ACCOUNT, {
            ...churnKeyAnalyticsBase,
            ...buildChurnKeySessionAnalytics(sessionResults),
          })
        },
        onClose: async (sessionResults) => {
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_CLOSED, {
            ...churnKeyAnalyticsBase,
            ...buildChurnKeySessionAnalytics(sessionResults),
          })
          if (shouldRefreshOnClose) {
            if (shouldSyncSubscriptionState && churnKeySyncUrl && churnKeyConfig.subscriptionId) {
              try {
                const result = await jsonRequest<{ success: boolean; error?: string }>(churnKeySyncUrl, {
                  method: 'POST',
                  includeCsrf: true,
                  json: { subscriptionId: churnKeyConfig.subscriptionId },
                })
                if (!result?.success) {
                  setSaveError(result?.error ?? 'Your billing changes were applied, but billing may take a moment to refresh.')
                  return
                }
              } catch (error) {
                track(AnalyticsEvent.BILLING_CANCEL_FLOW_ERROR, {
                  ...churnKeyAnalyticsBase,
                  errorType: 'sync_failed',
                  errorMessage: error instanceof Error ? error.message : String(error ?? ''),
                  fallback: 'await_webhook',
                })
                setSaveError('Your billing changes were applied, but billing may take a moment to refresh.')
                return
              }
            }
            window.location.reload()
          }
        },
        onError: (error, errorType) => {
          track(AnalyticsEvent.BILLING_CANCEL_FLOW_ERROR, {
            ...churnKeyAnalyticsBase,
            errorType: errorType ?? null,
            errorMessage: error instanceof Error ? error.message : String(error ?? ''),
            fallback: 'native_cancel_modal',
          })
          openCancelDialog()
        },
      })
    } catch (error) {
      track(AnalyticsEvent.BILLING_CANCEL_FLOW_ERROR, {
        ...churnKeyAnalyticsBase,
        errorType: 'init_exception',
        errorMessage: error instanceof Error ? error.message : String(error ?? ''),
        fallback: 'native_cancel_modal',
      })
      openCancelDialog()
    }
  }, [churnKeyAnalyticsBase, churnKeyConfig, churnKeySyncUrl, openCancelDialog])

  const closeCancelDialog = useCallback(() => {
    if (cancelActionBusy) return
    resetCancelFeedback()
    closeCancelActionDialog()
  }, [cancelActionBusy, closeCancelActionDialog, resetCancelFeedback])

  const cancelConfirmDisabled = cancelReason === '' || (cancelReason === 'other' && cancelFeedback.trim().length === 0)

  const dismissPlanConfirm = useCallback(() => {
    setPlanConfirmOpen(false)
    setPlanConfirmTarget(null)
    setPlanConfirmBusy(false)
    setPlanConfirmError(null)
  }, [])

  return (
    <div className={rootClassName}>
      {!isEmbedded ? (
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
      ) : null}

      <main className={mainClassName}>
        <BillingHeader
          initialData={initialData}
          variant={variant}
          onChangePlan={showPlanAction ? handlePlanActionClick : undefined}
          onCancel={!isOrg && initialData.contextType === 'personal' && initialData.paidSubscriber ? openCancelFlow : undefined}
          onResume={!isOrg
            && initialData.contextType === 'personal'
            && initialData.paidSubscriber
            && initialData.cancelAtPeriodEnd
            && initialData.endpoints.resumeSubscriptionUrl
            ? resumeAction.openDialog
            : undefined}
          onManageInStripe={handleManageInStripe}
          seatTarget={initialData.contextType === 'organization' ? (draft.seatTarget ?? initialData.seats.purchased) : undefined}
          saving={saving}
          onAdjustSeat={initialData.contextType === 'organization' ? handleSeatAdjust : undefined}
          onCancelScheduledSeatChange={initialData.contextType === 'organization' ? handleCancelSeatSchedule : undefined}
        />

        {saveError && !hasAnyChanges ? (
          <section
            className="rounded-2xl border border-rose-200 bg-white px-4 py-3 text-sm font-semibold text-rose-700"
            role="alert"
            aria-live="polite"
          >
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
              <div>{saveError}</div>
            </div>
          </section>
        ) : null}

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

      <SaveBar
        visible={hasAnyChanges && !summaryActionsVisible && nearTop}
        onCancel={resetDraft}
        onSave={isEmbedded ? scrollToBillingSummary : handleSave}
        busy={saving}
        error={saveError}
        title={isEmbedded ? 'You have unsaved changes.' : undefined}
        variant={isEmbedded ? 'embedded' : 'standalone'}
        placement={isEmbedded ? 'sticky' : 'fixed'}
        showCancel={!isEmbedded}
        saveLabel={isEmbedded ? 'Review and update' : undefined}
        showSaveIcon={!isEmbedded}
      />

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
        confirmDisabled={cancelConfirmDisabled}
        icon={<ShieldAlert className="h-5 w-5" />}
        busy={cancelAction.busy}
        danger
        onConfirm={() => cancelAction.confirm({ reason: cancelReason, feedback: cancelFeedback })}
        onClose={closeCancelDialog}
      >
        <div className="space-y-4 pb-2">
          <fieldset>
            <legend className="text-sm font-semibold text-slate-900">
              Why are you canceling? <span className="text-rose-700">*</span>
            </legend>
            <div className="mt-2 space-y-2">
              {CANCEL_REASON_OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2.5 transition hover:border-slate-300"
                >
                  <input
                    type="radio"
                    name="cancel-reason"
                    value={option.value}
                    checked={cancelReason === option.value}
                    disabled={cancelAction.busy}
                    onChange={() => setCancelReason(option.value)}
                    className="mt-0.5 h-4 w-4"
                  />
                  <span className="text-sm font-medium text-slate-800">{option.label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div>
            <label htmlFor="cancel-feedback" className="text-sm font-semibold text-slate-900">
              Anything else? (optional)
            </label>
            <textarea
              id="cancel-feedback"
              value={cancelFeedback}
              disabled={cancelAction.busy}
              onChange={(event) => setCancelFeedback(event.target.value.slice(0, CANCEL_FEEDBACK_MAX_LENGTH))}
              rows={4}
              className="mt-2 block w-full resize-y rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-500/30"
              placeholder="Share any details that would help us improve."
            />
            <div className="mt-1 text-right text-xs font-medium text-slate-500">
              {cancelFeedback.length}/{CANCEL_FEEDBACK_MAX_LENGTH}
            </div>
          </div>
        </div>
      </ConfirmDialog>

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
        onConfirm={() => resumeAction.confirm()}
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
            const result = await jsonRequest<{ ok: boolean; redirectUrl?: string; stripeActionUrl?: string }>(
              initialData.endpoints.updateUrl,
              {
                method: 'POST',
                includeCsrf: true,
                json: { ownerType: 'user', planTarget: planConfirmTarget },
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
