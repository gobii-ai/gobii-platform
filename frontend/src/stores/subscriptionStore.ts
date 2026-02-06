import { create } from 'zustand'

import { HttpError, jsonFetch, scheduleLoginRedirect } from '../api/http'
import { track, AnalyticsEvent } from '../util/analytics'

export type PlanTier = 'free' | 'startup' | 'scale'
export type UpgradeModalSource =
  | 'banner'
  | 'task_credits_callout'
  | 'contact_cap_callout'
  | 'intelligence_selector'
  | 'trial_onboarding'
  | 'unknown'

type UpgradeModalOptions = {
  dismissible?: boolean
}

export type TrialDaysByPlan = {
  startup: number
  scale: number
}

type SubscriptionState = {
  currentPlan: PlanTier | null
  isLoading: boolean
  isUpgradeModalOpen: boolean
  upgradeModalSource: UpgradeModalSource | null
  upgradeModalDismissible: boolean
  isProprietaryMode: boolean
  trialDaysByPlan: TrialDaysByPlan
  setCurrentPlan: (plan: PlanTier | null) => void
  setProprietaryMode: (isProprietary: boolean) => void
  setTrialDaysByPlan: (trialDaysByPlan: TrialDaysByPlan) => void
  openUpgradeModal: (source?: UpgradeModalSource, options?: UpgradeModalOptions) => void
  closeUpgradeModal: () => void
  ensureAuthenticated: () => Promise<boolean>
}

export const useSubscriptionStore = create<SubscriptionState>((set) => ({
  currentPlan: null,
  isLoading: false,
  isUpgradeModalOpen: false,
  upgradeModalSource: null,
  upgradeModalDismissible: true,
  isProprietaryMode: false,
  trialDaysByPlan: { startup: 0, scale: 0 },
  setCurrentPlan: (plan) => set({ currentPlan: plan, isLoading: false }),
  setProprietaryMode: (isProprietary) => set({ isProprietaryMode: isProprietary }),
  setTrialDaysByPlan: (trialDaysByPlan) => set({ trialDaysByPlan }),
  openUpgradeModal: (source = 'unknown', options = {}) => set((state) => {
    const resolvedSource = source ?? 'unknown'
    const dismissible = options.dismissible ?? true
    if (!state.isUpgradeModalOpen && typeof window !== 'undefined') {
      track(AnalyticsEvent.UPGRADE_MODAL_OPENED, {
        currentPlan: state.currentPlan,
        source: resolvedSource,
        isProprietaryMode: state.isProprietaryMode,
      })
    }
    return {
      isUpgradeModalOpen: true,
      upgradeModalSource: resolvedSource,
      upgradeModalDismissible: dismissible,
    }
  }),
  closeUpgradeModal: () =>
    set({ isUpgradeModalOpen: false, upgradeModalSource: null, upgradeModalDismissible: true }),
  ensureAuthenticated: async () => {
    if (typeof window === 'undefined') {
      return false
    }
    try {
      const data = await jsonFetch<UserPlanPayload>('/api/v1/user/plan/', {
        method: 'GET',
      })
      if (!data || typeof data !== 'object') {
        scheduleLoginRedirect()
        return false
      }
      const plan = normalizePlan(data?.plan)
      set({
        currentPlan: plan,
        isProprietaryMode: Boolean(data?.is_proprietary_mode),
        trialDaysByPlan: normalizeTrialDaysByPlan(data),
        isLoading: false,
      })
      return true
    } catch (error) {
      if (error instanceof HttpError && error.status === 401) {
        scheduleLoginRedirect()
        return false
      }
      return true
    }
  },
}))

type UserPlanPayload = {
  plan?: string | null
  is_proprietary_mode?: boolean
  startup_trial_days?: number | string | null
  scale_trial_days?: number | string | null
}

type UserPlanResponse = {
  plan: PlanTier | null
  isProprietaryMode: boolean
  trialDaysByPlan: TrialDaysByPlan
  authenticated: boolean
}

function normalizePlan(plan: unknown): PlanTier | null {
  if (plan && ['free', 'startup', 'scale'].includes(String(plan))) {
    return plan as PlanTier
  }
  return null
}

function normalizeTrialDays(value: unknown): number {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return 0
  }
  return Math.max(0, Math.trunc(numeric))
}

function normalizeTrialDaysByPlan(payload: UserPlanPayload | null | undefined): TrialDaysByPlan {
  return {
    startup: normalizeTrialDays(payload?.startup_trial_days),
    scale: normalizeTrialDays(payload?.scale_trial_days),
  }
}

/**
 * Fetch the user's plan from the API.
 */
async function fetchUserPlan(): Promise<UserPlanResponse> {
  try {
    const data = await jsonFetch<UserPlanPayload>('/api/v1/user/plan/', {
      method: 'GET',
    })
    if (!data || typeof data !== 'object') {
      return {
        plan: null,
        isProprietaryMode: false,
        trialDaysByPlan: { startup: 0, scale: 0 },
        authenticated: false,
      }
    }
    const plan = normalizePlan(data?.plan)
    return {
      plan,
      isProprietaryMode: Boolean(data?.is_proprietary_mode),
      trialDaysByPlan: normalizeTrialDaysByPlan(data),
      authenticated: true,
    }
  } catch (error) {
    if (error instanceof HttpError && error.status === 401) {
      return {
        plan: null,
        isProprietaryMode: false,
        trialDaysByPlan: { startup: 0, scale: 0 },
        authenticated: false,
      }
    }
    return {
      plan: null,
      isProprietaryMode: false,
      trialDaysByPlan: { startup: 0, scale: 0 },
      authenticated: true,
    }
  }
}

/**
 * Initialize the subscription store from DOM data attributes,
 * falling back to API fetch if not present.
 * Call this once on app startup with the mount element.
 */
export function initializeSubscriptionStore(mountElement: HTMLElement): void {
  // Check for data attributes first (server-rendered templates)
  const proprietaryAttr = mountElement.dataset.isProprietaryMode
  const planAttr = mountElement.dataset.userPlan
  const trialDaysByPlan: TrialDaysByPlan = {
    startup: normalizeTrialDays(mountElement.dataset.startupTrialDays),
    scale: normalizeTrialDays(mountElement.dataset.scaleTrialDays),
  }

  useSubscriptionStore.getState().setTrialDaysByPlan(trialDaysByPlan)

  // If we have both data attributes, use them directly
  if (proprietaryAttr !== undefined && planAttr && ['free', 'startup', 'scale'].includes(planAttr)) {
    useSubscriptionStore.getState().setProprietaryMode(proprietaryAttr === 'true')
    useSubscriptionStore.getState().setCurrentPlan(planAttr as PlanTier)
    return
  }

  // No data attributes (e.g., static app shell) - fetch from API
  useSubscriptionStore.setState({ isLoading: true })
  fetchUserPlan().then(({ plan, isProprietaryMode, trialDaysByPlan: apiTrialDaysByPlan }) => {
    useSubscriptionStore.getState().setCurrentPlan(plan)
    useSubscriptionStore.getState().setProprietaryMode(isProprietaryMode)
    useSubscriptionStore.getState().setTrialDaysByPlan(apiTrialDaysByPlan)
  })
}
