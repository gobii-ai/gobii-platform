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

/**
 * Fetch the user's plan from the API.
 */
async function fetchUserPlan(): Promise<PlanTier | null> {
  try {
    const response = await fetch('/api/v1/user/plan/', {
      credentials: 'same-origin',
    })
    if (!response.ok) return null
    const data = await response.json()
    if (data.plan && ['free', 'startup', 'scale'].includes(data.plan)) {
      return data.plan as PlanTier
    }
    return null
  } catch {
    return null
  }
}

/**
 * Initialize the subscription store from DOM data attributes,
 * falling back to API fetch if not present.
 * Call this once on app startup with the mount element.
 */
export function initializeSubscriptionStore(mountElement: HTMLElement): void {
  // Initialize proprietary mode from data attribute
  const proprietaryAttr = mountElement.dataset.isProprietaryMode
  useSubscriptionStore.getState().setProprietaryMode(proprietaryAttr === 'true')

  const planAttr = mountElement.dataset.userPlan
  if (planAttr && ['free', 'startup', 'scale'].includes(planAttr)) {
    useSubscriptionStore.getState().setCurrentPlan(planAttr as PlanTier)
    return
  }

  // No data attribute - fetch from API
  useSubscriptionStore.setState({ isLoading: true })
  fetchUserPlan().then((plan) => {
    useSubscriptionStore.getState().setCurrentPlan(plan)
  })
}
