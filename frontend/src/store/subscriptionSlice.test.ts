import { beforeEach, describe, expect, it, vi } from 'vitest'

import { track } from '../util/analytics'
import { createAppStore } from './appStore'
import { hydrateSubscriptionFromMountElement, selectSubscriptionState, subscriptionActions } from './subscriptionSlice'

vi.mock('../util/analytics', () => ({
  track: vi.fn(),
}))

describe('subscriptionSlice', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('hydrates subscription flags from the mount element payload', () => {
    const store = createAppStore()
    const mountElement = document.createElement('div')
    mountElement.dataset.userPlan = 'startup'
    mountElement.dataset.isProprietaryMode = 'true'
    mountElement.dataset.pricingModalAlmostFullScreen = 'false'
    mountElement.dataset.ctaStartFreeTrial = 'true'
    mountElement.dataset.ctaPickAPlan = 'true'
    mountElement.dataset.personalSignupPreviewAvailable = 'true'
    mountElement.dataset.personalSignupPreviewProcessingAvailable = 'true'
    mountElement.dataset.startupTrialDays = '14'
    mountElement.dataset.scaleTrialDays = '7'
    mountElement.dataset.startupTaskCredits = '500'
    mountElement.dataset.scaleTaskCredits = '10000'
    mountElement.dataset.trialEligible = 'true'

    store.dispatch(subscriptionActions.planLoading())
    store.dispatch(hydrateSubscriptionFromMountElement(mountElement))

    expect(selectSubscriptionState(store.getState())).toMatchObject({
      currentPlan: 'startup',
      isLoading: false,
      isProprietaryMode: true,
      ctaStartFreeTrial: true,
      ctaPickAPlan: true,
      personalSignupPreviewAvailable: true,
      personalSignupPreviewProcessingAvailable: true,
      trialDaysByPlan: { startup: 14, scale: 7 },
      trialEligible: true,
    })
  })

  it('tracks upgrade modal opens from listener middleware once per closed-to-open transition', () => {
    const store = createAppStore()
    store.dispatch(subscriptionActions.setCurrentPlan('free'))
    store.dispatch(subscriptionActions.setProprietaryMode(true))

    store.dispatch(subscriptionActions.openUpgradeModal({ source: 'trial_onboarding', options: { dismissible: false } }))
    store.dispatch(subscriptionActions.openUpgradeModal({ source: 'banner' }))

    expect(track).toHaveBeenCalledTimes(1)
    expect(track).toHaveBeenCalledWith('Upgrade Modal Opened', {
      currentPlan: 'free',
      source: 'trial_onboarding',
      isProprietaryMode: true,
    })
    expect(selectSubscriptionState(store.getState())).toMatchObject({
      isUpgradeModalOpen: true,
      upgradeModalSource: 'banner',
      upgradeModalDismissible: true,
    })
  })
})
