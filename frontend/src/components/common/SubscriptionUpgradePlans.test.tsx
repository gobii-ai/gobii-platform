import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'

import { SubscriptionUpgradePlans } from './SubscriptionUpgradePlans'
import type { AppStore } from '../../store/appStore'
import { createTestAppStore, seedSubscriptionState, StoreProvider } from '../../test/storeTestUtils'

vi.mock('../../util/analytics', () => ({
  track: vi.fn(),
}))

function buildInitialSubscriptionState() {
  return {
    currentPlan: 'free' as const,
    isLoading: false,
    isUpgradeModalOpen: false,
    upgradeModalSource: null,
    upgradeModalDismissible: true,
    isProprietaryMode: true,
    pricingModalAlmostFullScreen: true,
    ctaPricingCancelTextUnderBtn: false,
    ctaStartFreeTrial: true,
    ctaUnlockAgentCopy: false,
    ctaPickAPlan: false,
    ctaContinueAgentBtn: false,
    ctaNoChargeDuringTrial: false,
    trialDaysByPlan: { startup: 7, scale: 7 },
    trialEligible: true,
    ensureAuthenticated: vi.fn(async () => true),
  }
}

describe('SubscriptionUpgradePlans mobile layout', () => {
  let appStore: AppStore

  beforeEach(() => {
    appStore = createTestAppStore()
    seedSubscriptionState(appStore, buildInitialSubscriptionState())
  })

  function renderPlans(props: ComponentProps<typeof SubscriptionUpgradePlans>) {
    return render(
      <StoreProvider store={appStore}>
        <SubscriptionUpgradePlans {...props} />
      </StoreProvider>,
    )
  }

  it('renders the trial gate dial for the signup funnel with Scale selected by default', () => {
    const onUpgrade = vi.fn()
    renderPlans(
      {
        currentPlan: 'free',
        onUpgrade,
        source: 'trial_onboarding',
      },
    )

    expect(screen.getByTestId('subscription-plans-grid')).toBeInTheDocument()
    expect(screen.getByTestId('subscription-plan-startup')).toBeInTheDocument()
    expect(screen.getByText('$0 today')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('subscription-plan-scale'))
    expect(onUpgrade).toHaveBeenCalledWith('scale')
  })

  it('starts a Pro trial through the escape hatch', () => {
    const onUpgrade = vi.fn()
    renderPlans(
      {
        currentPlan: 'free',
        onUpgrade,
        source: 'trial_onboarding',
      },
    )

    fireEvent.click(screen.getByTestId('subscription-plan-startup'))
    expect(onUpgrade).toHaveBeenCalledWith('startup')
  })

  it('uses the unlock copy only when the unlock variant is requested', () => {
    renderPlans(
      {
        currentPlan: 'free',
        onUpgrade: vi.fn(),
        source: 'signup_preview_panel',
        trialCopyVariant: 'unlock_agent',
      },
    )

    expect(screen.getAllByRole('button', { name: /start for free/i })).toHaveLength(2)
    expect(screen.getAllByText('No charge today. Cancel anytime.')).toHaveLength(2)
  })
})
