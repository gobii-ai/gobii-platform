import { createAsyncThunk, createSlice, type PayloadAction } from '@reduxjs/toolkit'

import { HttpError, jsonFetch, scheduleLoginRedirect } from '../api/http'
import type { AppDispatch, RootState } from './appStore'

export type PlanTier = 'free' | 'startup' | 'scale'
export type UpgradeModalSource =
  | 'banner'
  | 'task_credits_callout'
  | 'contact_cap_callout'
  | 'intelligence_selector'
  | 'trial_onboarding'
  | 'agent_limit_error'
  | 'unknown'

export type UpgradeModalOptions = {
  dismissible?: boolean
}

const CONTINUATION_UPGRADE_MODAL_SOURCES: readonly UpgradeModalSource[] = [
  'trial_onboarding',
  'agent_limit_error',
]

export function isContinuationUpgradeModalSource(
  source: UpgradeModalSource | string | null | undefined,
): boolean {
  return Boolean(source && CONTINUATION_UPGRADE_MODAL_SOURCES.includes(source as UpgradeModalSource))
}

export type TrialDaysByPlan = {
  startup: number
  scale: number
}

export type PlanTaskCreditsByPlan = {
  startup: number
  scale: number
}

export type SubscriptionState = {
  currentPlan: PlanTier | null
  isLoading: boolean
  isUpgradeModalOpen: boolean
  upgradeModalSource: UpgradeModalSource | null
  upgradeModalDismissible: boolean
  isProprietaryMode: boolean
  pricingModalAlmostFullScreen: boolean
  ctaPricingCancelTextUnderBtn: boolean
  ctaStartFreeTrial: boolean
  ctaUnlockAgentCopy: boolean
  ctaPickAPlan: boolean
  ctaContinueAgentBtn: boolean
  ctaNoChargeDuringTrial: boolean
  personalSignupPreviewAvailable: boolean
  personalSignupPreviewProcessingAvailable: boolean
  trialDaysByPlan: TrialDaysByPlan
  planTaskCreditsByPlan: PlanTaskCreditsByPlan
  trialEligible: boolean
}

export type UserPlanPayload = {
  plan?: string | null
  is_proprietary_mode?: boolean
  pricing_modal_almost_full_screen?: boolean | string | null
  cta_pricing_cancel_text_under_btn?: boolean | string | null
  cta_start_free_trial?: boolean | string | null
  cta_unlock_agent_copy?: boolean | string | null
  cta_pick_a_plan?: boolean | string | null
  cta_continue_agent_btn?: boolean | string | null
  cta_no_charge_during_trial?: boolean | string | null
  personal_signup_preview_available?: boolean | string | null
  personal_signup_preview_processing_available?: boolean | string | null
  startup_trial_days?: number | string | null
  scale_trial_days?: number | string | null
  startup_task_credits?: number | string | null
  scale_task_credits?: number | string | null
  trial_eligible?: boolean | string | null
}

type UserPlanResponse = Omit<SubscriptionState, 'isLoading' | 'isUpgradeModalOpen' | 'upgradeModalSource' | 'upgradeModalDismissible'> & {
  authenticated: boolean
}

export const initialSubscriptionState: SubscriptionState = {
  currentPlan: null,
  isLoading: false,
  isUpgradeModalOpen: false,
  upgradeModalSource: null,
  upgradeModalDismissible: true,
  isProprietaryMode: false,
  pricingModalAlmostFullScreen: true,
  ctaPricingCancelTextUnderBtn: false,
  ctaStartFreeTrial: false,
  ctaUnlockAgentCopy: false,
  ctaPickAPlan: false,
  ctaContinueAgentBtn: false,
  ctaNoChargeDuringTrial: false,
  personalSignupPreviewAvailable: false,
  personalSignupPreviewProcessingAvailable: false,
  trialDaysByPlan: { startup: 0, scale: 0 },
  planTaskCreditsByPlan: { startup: 500, scale: 10000 },
  trialEligible: false,
}

export function normalizePlan(plan: unknown): PlanTier | null {
  if (plan && ['free', 'startup', 'scale'].includes(String(plan))) {
    return plan as PlanTier
  }
  return null
}

function normalizeNonNegativeInteger(value: unknown, defaultValue = 0): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return defaultValue
  }
  return Math.max(0, Math.trunc(numeric))
}

function normalizeBoolean(value: unknown, defaultValue = false): boolean {
  if (typeof value === 'boolean') {
    return value
  }
  if (typeof value === 'string') {
    return value.toLowerCase() === 'true'
  }
  return defaultValue
}

function normalizeTrialDaysByPlan(payload: UserPlanPayload | null | undefined): TrialDaysByPlan {
  return {
    startup: normalizeNonNegativeInteger(payload?.startup_trial_days),
    scale: normalizeNonNegativeInteger(payload?.scale_trial_days),
  }
}

function normalizePlanTaskCreditsByPlan(payload: UserPlanPayload | null | undefined): PlanTaskCreditsByPlan {
  return {
    startup: normalizeNonNegativeInteger(payload?.startup_task_credits, 500),
    scale: normalizeNonNegativeInteger(payload?.scale_task_credits, 10000),
  }
}

function buildUserPlanResponse(payload: UserPlanPayload | null | undefined, authenticated: boolean): UserPlanResponse {
  return {
    currentPlan: normalizePlan(payload?.plan),
    isProprietaryMode: Boolean(payload?.is_proprietary_mode),
    pricingModalAlmostFullScreen: normalizeBoolean(payload?.pricing_modal_almost_full_screen, true),
    ctaPricingCancelTextUnderBtn: normalizeBoolean(payload?.cta_pricing_cancel_text_under_btn),
    ctaStartFreeTrial: normalizeBoolean(payload?.cta_start_free_trial),
    ctaUnlockAgentCopy: normalizeBoolean(payload?.cta_unlock_agent_copy),
    ctaPickAPlan: normalizeBoolean(payload?.cta_pick_a_plan),
    ctaContinueAgentBtn: normalizeBoolean(payload?.cta_continue_agent_btn),
    ctaNoChargeDuringTrial: normalizeBoolean(payload?.cta_no_charge_during_trial),
    personalSignupPreviewAvailable: normalizeBoolean(payload?.personal_signup_preview_available),
    personalSignupPreviewProcessingAvailable: normalizeBoolean(payload?.personal_signup_preview_processing_available),
    trialDaysByPlan: normalizeTrialDaysByPlan(payload),
    planTaskCreditsByPlan: normalizePlanTaskCreditsByPlan(payload),
    trialEligible: normalizeBoolean(payload?.trial_eligible),
    authenticated,
  }
}

async function fetchUserPlan(): Promise<UserPlanResponse> {
  try {
    const data = await jsonFetch<UserPlanPayload>('/api/v1/user/plan/', { method: 'GET' })
    if (!data || typeof data !== 'object') {
      return buildUserPlanResponse(null, false)
    }
    return buildUserPlanResponse(data, true)
  } catch (error) {
    return buildUserPlanResponse(null, !(error instanceof HttpError && error.status === 401))
  }
}

export const ensureAuthenticated = createAsyncThunk<boolean, void, { state: RootState }>(
  'subscription/ensureAuthenticated',
  async (_, { dispatch }) => {
    if (typeof window === 'undefined') {
      return false
    }
    try {
      const data = await jsonFetch<UserPlanPayload>('/api/v1/user/plan/', { method: 'GET' })
      if (!data || typeof data !== 'object') {
        scheduleLoginRedirect()
        return false
      }
      dispatch(subscriptionActions.planHydrated(buildUserPlanResponse(data, true)))
      return true
    } catch (error) {
      if (error instanceof HttpError && error.status === 401) {
        scheduleLoginRedirect()
        return false
      }
      return true
    }
  },
)

export function hydrateSubscriptionFromMountElement(mountElement: HTMLElement) {
  return (dispatch: AppDispatch): void => {
    const payload: UserPlanPayload = {
      plan: mountElement.dataset.userPlan,
      is_proprietary_mode: mountElement.dataset.isProprietaryMode === 'true',
      pricing_modal_almost_full_screen: mountElement.dataset.pricingModalAlmostFullScreen,
      cta_pricing_cancel_text_under_btn: mountElement.dataset.ctaPricingCancelTextUnderBtn,
      cta_start_free_trial: mountElement.dataset.ctaStartFreeTrial,
      cta_unlock_agent_copy: mountElement.dataset.ctaUnlockAgentCopy,
      cta_pick_a_plan: mountElement.dataset.ctaPickAPlan,
      cta_continue_agent_btn: mountElement.dataset.ctaContinueAgentBtn,
      cta_no_charge_during_trial: mountElement.dataset.ctaNoChargeDuringTrial,
      personal_signup_preview_available: mountElement.dataset.personalSignupPreviewAvailable,
      personal_signup_preview_processing_available: mountElement.dataset.personalSignupPreviewProcessingAvailable,
      startup_trial_days: mountElement.dataset.startupTrialDays,
      scale_trial_days: mountElement.dataset.scaleTrialDays,
      startup_task_credits: mountElement.dataset.startupTaskCredits,
      scale_task_credits: mountElement.dataset.scaleTaskCredits,
      trial_eligible: mountElement.dataset.trialEligible,
    }

    const hasServerPlan = (
      mountElement.dataset.isProprietaryMode !== undefined
      && Boolean(mountElement.dataset.userPlan)
      && mountElement.dataset.trialEligible !== undefined
    )

    if (hasServerPlan) {
      dispatch(subscriptionActions.planHydrated(buildUserPlanResponse(payload, true)))
      return
    }

    dispatch(subscriptionActions.planLoading())
    void fetchUserPlan().then((response) => {
      dispatch(subscriptionActions.planHydrated(response))
    })
  }
}

const subscriptionSlice = createSlice({
  name: 'subscription',
  initialState: initialSubscriptionState,
  reducers: {
    planLoading(state) {
      state.isLoading = true
    },
    planHydrated(state, action: PayloadAction<UserPlanResponse>) {
      Object.assign(state, {
        currentPlan: action.payload.currentPlan,
        isProprietaryMode: action.payload.isProprietaryMode,
        pricingModalAlmostFullScreen: action.payload.pricingModalAlmostFullScreen,
        ctaPricingCancelTextUnderBtn: action.payload.ctaPricingCancelTextUnderBtn,
        ctaStartFreeTrial: action.payload.ctaStartFreeTrial,
        ctaUnlockAgentCopy: action.payload.ctaUnlockAgentCopy,
        ctaPickAPlan: action.payload.ctaPickAPlan,
        ctaContinueAgentBtn: action.payload.ctaContinueAgentBtn,
        ctaNoChargeDuringTrial: action.payload.ctaNoChargeDuringTrial,
        personalSignupPreviewAvailable: action.payload.personalSignupPreviewAvailable,
        personalSignupPreviewProcessingAvailable: action.payload.personalSignupPreviewProcessingAvailable,
        trialDaysByPlan: action.payload.trialDaysByPlan,
        planTaskCreditsByPlan: action.payload.planTaskCreditsByPlan,
        trialEligible: action.payload.trialEligible,
        isLoading: false,
      })
    },
    setCurrentPlan(state, action: PayloadAction<PlanTier | null>) {
      state.currentPlan = action.payload
      state.isLoading = false
    },
    setProprietaryMode(state, action: PayloadAction<boolean>) {
      state.isProprietaryMode = action.payload
    },
    setPricingModalAlmostFullScreen(state, action: PayloadAction<boolean>) {
      state.pricingModalAlmostFullScreen = action.payload
    },
    setCtaPricingCancelTextUnderBtn(state, action: PayloadAction<boolean>) {
      state.ctaPricingCancelTextUnderBtn = action.payload
    },
    setCtaStartFreeTrial(state, action: PayloadAction<boolean>) {
      state.ctaStartFreeTrial = action.payload
    },
    setCtaUnlockAgentCopy(state, action: PayloadAction<boolean>) {
      state.ctaUnlockAgentCopy = action.payload
    },
    setCtaPickAPlan(state, action: PayloadAction<boolean>) {
      state.ctaPickAPlan = action.payload
    },
    setCtaContinueAgentBtn(state, action: PayloadAction<boolean>) {
      state.ctaContinueAgentBtn = action.payload
    },
    setCtaNoChargeDuringTrial(state, action: PayloadAction<boolean>) {
      state.ctaNoChargeDuringTrial = action.payload
    },
    setPersonalSignupPreviewAvailable(state, action: PayloadAction<boolean>) {
      state.personalSignupPreviewAvailable = action.payload
    },
    setPersonalSignupPreviewProcessingAvailable(state, action: PayloadAction<boolean>) {
      state.personalSignupPreviewProcessingAvailable = action.payload
    },
    setTrialDaysByPlan(state, action: PayloadAction<TrialDaysByPlan>) {
      state.trialDaysByPlan = action.payload
    },
    setPlanTaskCreditsByPlan(state, action: PayloadAction<PlanTaskCreditsByPlan>) {
      state.planTaskCreditsByPlan = action.payload
    },
    setTrialEligible(state, action: PayloadAction<boolean>) {
      state.trialEligible = action.payload
    },
    openUpgradeModal(
      state,
      action: PayloadAction<{ source?: UpgradeModalSource | null; options?: UpgradeModalOptions } | undefined>,
    ) {
      const source = action.payload?.source ?? 'unknown'
      const dismissible = action.payload?.options?.dismissible ?? true
      state.isUpgradeModalOpen = true
      state.upgradeModalSource = source
      state.upgradeModalDismissible = dismissible
    },
    closeUpgradeModal(state) {
      state.isUpgradeModalOpen = false
      state.upgradeModalSource = null
      state.upgradeModalDismissible = true
    },
  },
})

export const subscriptionActions = subscriptionSlice.actions
export const subscriptionReducer = subscriptionSlice.reducer

export const selectSubscriptionState = (state: RootState): SubscriptionState => state.subscription
