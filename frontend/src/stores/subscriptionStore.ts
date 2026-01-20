import { create } from 'zustand'

export type PlanTier = 'free' | 'startup' | 'scale'

type SubscriptionState = {
  currentPlan: PlanTier | null
  isUpgradeModalOpen: boolean
  setCurrentPlan: (plan: PlanTier | null) => void
  openUpgradeModal: () => void
  closeUpgradeModal: () => void
}

export const useSubscriptionStore = create<SubscriptionState>((set) => ({
  currentPlan: null,
  isUpgradeModalOpen: false,
  setCurrentPlan: (plan) => set({ currentPlan: plan }),
  openUpgradeModal: () => set({ isUpgradeModalOpen: true }),
  closeUpgradeModal: () => set({ isUpgradeModalOpen: false }),
}))

/**
 * Initialize the subscription store from DOM data attributes.
 * Call this once on app startup with the mount element.
 */
export function initializeSubscriptionStore(mountElement: HTMLElement): void {
  const planAttr = mountElement.dataset.userPlan
  if (planAttr && ['free', 'startup', 'scale'].includes(planAttr)) {
    useSubscriptionStore.getState().setCurrentPlan(planAttr as PlanTier)
  }
}
