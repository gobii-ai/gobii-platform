import { useMemo, useSyncExternalStore } from 'react'

import {
  ensureAuthenticated,
  selectSubscriptionState,
  subscriptionActions,
  type PlanTaskCreditsByPlan,
  type PlanTier,
  type SubscriptionState,
  type TrialDaysByPlan,
  type UpgradeModalOptions,
  type UpgradeModalSource,
} from '../store/subscriptionSlice'
import type { AppDispatch } from '../store/appStore'
import { useAppStore } from '../store/hooks'

const CONTINUATION_UPGRADE_MODAL_SOURCES: readonly UpgradeModalSource[] = [
  'trial_onboarding',
  'agent_limit_error',
]

export function isContinuationUpgradeModalSource(
  source: UpgradeModalSource | string | null | undefined,
): boolean {
  return Boolean(source && CONTINUATION_UPGRADE_MODAL_SOURCES.includes(source as UpgradeModalSource))
}

type SubscriptionActions = {
  setCurrentPlan: (plan: PlanTier | null) => void
  setProprietaryMode: (isProprietary: boolean) => void
  setPricingModalAlmostFullScreen: (pricingModalAlmostFullScreen: boolean) => void
  setCtaPricingCancelTextUnderBtn: (ctaPricingCancelTextUnderBtn: boolean) => void
  setCtaStartFreeTrial: (ctaStartFreeTrial: boolean) => void
  setCtaUnlockAgentCopy: (ctaUnlockAgentCopy: boolean) => void
  setCtaPickAPlan: (ctaPickAPlan: boolean) => void
  setCtaContinueAgentBtn: (ctaContinueAgentBtn: boolean) => void
  setCtaNoChargeDuringTrial: (ctaNoChargeDuringTrial: boolean) => void
  setPersonalSignupPreviewAvailable: (personalSignupPreviewAvailable: boolean) => void
  setPersonalSignupPreviewProcessingAvailable: (personalSignupPreviewProcessingAvailable: boolean) => void
  setTrialDaysByPlan: (trialDaysByPlan: TrialDaysByPlan) => void
  setPlanTaskCreditsByPlan: (planTaskCreditsByPlan: PlanTaskCreditsByPlan) => void
  setTrialEligible: (trialEligible: boolean) => void
  openUpgradeModal: (source?: UpgradeModalSource, options?: UpgradeModalOptions) => void
  closeUpgradeModal: () => void
  ensureAuthenticated: () => Promise<boolean>
}

type SubscriptionStoreFacade = SubscriptionState & SubscriptionActions

function createSubscriptionActions(dispatch: AppDispatch): SubscriptionActions {
  return {
    setCurrentPlan: (plan) => dispatch(subscriptionActions.setCurrentPlan(plan)),
    setProprietaryMode: (isProprietary) => dispatch(subscriptionActions.setProprietaryMode(isProprietary)),
    setPricingModalAlmostFullScreen: (value) => dispatch(subscriptionActions.setPricingModalAlmostFullScreen(value)),
    setCtaPricingCancelTextUnderBtn: (value) => dispatch(subscriptionActions.setCtaPricingCancelTextUnderBtn(value)),
    setCtaStartFreeTrial: (value) => dispatch(subscriptionActions.setCtaStartFreeTrial(value)),
    setCtaUnlockAgentCopy: (value) => dispatch(subscriptionActions.setCtaUnlockAgentCopy(value)),
    setCtaPickAPlan: (value) => dispatch(subscriptionActions.setCtaPickAPlan(value)),
    setCtaContinueAgentBtn: (value) => dispatch(subscriptionActions.setCtaContinueAgentBtn(value)),
    setCtaNoChargeDuringTrial: (value) => dispatch(subscriptionActions.setCtaNoChargeDuringTrial(value)),
    setPersonalSignupPreviewAvailable: (value) => dispatch(subscriptionActions.setPersonalSignupPreviewAvailable(value)),
    setPersonalSignupPreviewProcessingAvailable: (value) =>
      dispatch(subscriptionActions.setPersonalSignupPreviewProcessingAvailable(value)),
    setTrialDaysByPlan: (value) => dispatch(subscriptionActions.setTrialDaysByPlan(value)),
    setPlanTaskCreditsByPlan: (value) => dispatch(subscriptionActions.setPlanTaskCreditsByPlan(value)),
    setTrialEligible: (value) => dispatch(subscriptionActions.setTrialEligible(value)),
    openUpgradeModal: (source = 'unknown', options = {}) =>
      dispatch(subscriptionActions.openUpgradeModal({ source, options })),
    closeUpgradeModal: () => dispatch(subscriptionActions.closeUpgradeModal()),
    ensureAuthenticated: async () => dispatch(ensureAuthenticated()).unwrap(),
  }
}

export function useSubscriptionStore<T = SubscriptionStoreFacade>(selector?: (state: SubscriptionStoreFacade) => T): T {
  const store = useAppStore()
  const rootState = useSyncExternalStore(store.subscribe, store.getState, store.getState)
  const state = selectSubscriptionState(rootState)
  const actions = useMemo(() => createSubscriptionActions(store.dispatch), [store])
  const facade = useMemo(() => ({ ...state, ...actions }), [actions, state])
  return selector ? selector(facade) : (facade as T)
}

export type {
  PlanTier,
  UpgradeModalSource,
  TrialDaysByPlan,
  PlanTaskCreditsByPlan,
}
