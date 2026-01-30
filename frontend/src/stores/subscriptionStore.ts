import { create } from 'zustand'

import { HttpError, jsonFetch } from '../api/http'

export type PlanTier = 'free' | 'startup' | 'scale'

type SubscriptionState = {
  currentPlan: PlanTier | null
  isLoading: boolean
  isUpgradeModalOpen: boolean
  isProprietaryMode: boolean
  setCurrentPlan: (plan: PlanTier | null) => void
  setProprietaryMode: (isProprietary: boolean) => void
  openUpgradeModal: () => void
  closeUpgradeModal: () => void
  ensureAuthenticated: () => Promise<boolean>
}

export const useSubscriptionStore = create<SubscriptionState>((set) => ({
  currentPlan: null,
  isLoading: false,
  isUpgradeModalOpen: false,
  isProprietaryMode: false,
  setCurrentPlan: (plan) => set({ currentPlan: plan, isLoading: false }),
  setProprietaryMode: (isProprietary) => set({ isProprietaryMode: isProprietary }),
  openUpgradeModal: () => set({ isUpgradeModalOpen: true }),
  closeUpgradeModal: () => set({ isUpgradeModalOpen: false }),
  ensureAuthenticated: async () => {
    if (typeof window === 'undefined') {
      return false
    }
    try {
      const data = await jsonFetch<UserPlanPayload>('/api/v1/user/plan/', {
        method: 'GET',
      })
      if (!data || typeof data !== 'object') {
        return false
      }
      const plan = normalizePlan(data?.plan)
      set({
        currentPlan: plan,
        isProprietaryMode: Boolean(data?.is_proprietary_mode),
        isLoading: false,
      })
      return true
    } catch (error) {
      if (error instanceof HttpError && error.status === 401) {
        return false
      }
      return true
    }
  },
}))

type UserPlanPayload = {
  plan?: string | null
  is_proprietary_mode?: boolean
}

type UserPlanResponse = {
  plan: PlanTier | null
  isProprietaryMode: boolean
  authenticated: boolean
}

function normalizePlan(plan: unknown): PlanTier | null {
  if (plan && ['free', 'startup', 'scale'].includes(String(plan))) {
    return plan as PlanTier
  }
  return null
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
      return { plan: null, isProprietaryMode: false, authenticated: false }
    }
    const plan = normalizePlan(data?.plan)
    return {
      plan,
      isProprietaryMode: Boolean(data?.is_proprietary_mode),
      authenticated: true,
    }
  } catch (error) {
    if (error instanceof HttpError && error.status === 401) {
      return { plan: null, isProprietaryMode: false, authenticated: false }
    }
    return { plan: null, isProprietaryMode: false, authenticated: true }
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

  // If we have both data attributes, use them directly
  if (proprietaryAttr !== undefined && planAttr && ['free', 'startup', 'scale'].includes(planAttr)) {
    useSubscriptionStore.getState().setProprietaryMode(proprietaryAttr === 'true')
    useSubscriptionStore.getState().setCurrentPlan(planAttr as PlanTier)
    return
  }

  // No data attributes (e.g., static app shell) - fetch from API
  useSubscriptionStore.setState({ isLoading: true })
  fetchUserPlan().then(({ plan, isProprietaryMode }) => {
    useSubscriptionStore.getState().setCurrentPlan(plan)
    useSubscriptionStore.getState().setProprietaryMode(isProprietaryMode)
  })
}
