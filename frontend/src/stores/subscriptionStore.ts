import { create } from 'zustand'

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
}))

type UserPlanResponse = {
  plan: PlanTier | null
  isProprietaryMode: boolean
}

/**
 * Fetch the user's plan from the API.
 */
async function fetchUserPlan(): Promise<UserPlanResponse> {
  try {
    const response = await fetch('/api/v1/user/plan/', {
      credentials: 'same-origin',
    })
    if (!response.ok) return { plan: null, isProprietaryMode: false }
    const data = await response.json()
    const plan = data.plan && ['free', 'startup', 'scale'].includes(data.plan)
      ? (data.plan as PlanTier)
      : null
    return {
      plan,
      isProprietaryMode: Boolean(data.is_proprietary_mode),
    }
  } catch {
    return { plan: null, isProprietaryMode: false }
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
