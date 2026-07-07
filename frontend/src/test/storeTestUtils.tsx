import type { ReactNode } from 'react'
import { Provider } from 'react-redux'
import type { QueryClient } from '@tanstack/react-query'

import { createAppStore, type AppStore } from '../store/appStore'
import {
  initialSubscriptionState,
  selectSubscriptionState,
  subscriptionActions,
  type SubscriptionState,
} from '../store/subscriptionSlice'

export function createTestAppStore({ queryClient = null }: { queryClient?: QueryClient | null } = {}): AppStore {
  return createAppStore({ queryClient })
}

export function seedSubscriptionState(store: AppStore, state: Partial<SubscriptionState> = {}): void {
  const next: SubscriptionState = {
    ...initialSubscriptionState,
    ...state,
  }

  store.dispatch(subscriptionActions.planHydrated({
    currentPlan: next.currentPlan,
    isProprietaryMode: next.isProprietaryMode,
    pricingModalAlmostFullScreen: next.pricingModalAlmostFullScreen,
    ctaPricingCancelTextUnderBtn: next.ctaPricingCancelTextUnderBtn,
    ctaStartFreeTrial: next.ctaStartFreeTrial,
    ctaUnlockAgentCopy: next.ctaUnlockAgentCopy,
    ctaPickAPlan: next.ctaPickAPlan,
    ctaContinueAgentBtn: next.ctaContinueAgentBtn,
    ctaNoChargeDuringTrial: next.ctaNoChargeDuringTrial,
    personalSignupPreviewAvailable: next.personalSignupPreviewAvailable,
    personalSignupPreviewProcessingAvailable: next.personalSignupPreviewProcessingAvailable,
    trialDaysByPlan: next.trialDaysByPlan,
    planTaskCreditsByPlan: next.planTaskCreditsByPlan,
    trialEligible: next.trialEligible,
    authenticated: true,
  }))
  if (next.isLoading) {
    store.dispatch(subscriptionActions.planLoading())
  }
  if (next.isUpgradeModalOpen) {
    store.dispatch(subscriptionActions.openUpgradeModal({
      source: next.upgradeModalSource ?? 'unknown',
      options: { dismissible: next.upgradeModalDismissible },
    }))
  }
}

export function getSubscriptionState(store: AppStore): SubscriptionState {
  return selectSubscriptionState(store.getState())
}

export function StoreProvider({ store, children }: { store: AppStore; children: ReactNode }) {
  return <Provider store={store}>{children}</Provider>
}
