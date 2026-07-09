import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
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

  it('does not force full-height stacked plan cards in the expanded modal layout', () => {
    renderPlans(
      {
        currentPlan: 'free',
        onUpgrade: vi.fn(),
        source: 'trial_onboarding',
      },
    )

    const grid = screen.getByTestId('subscription-plans-grid')
    const startupPlan = screen.getByTestId('subscription-plan-startup')
    const scalePlan = screen.getByTestId('subscription-plan-scale')

    expect(grid).toHaveClass('sm:min-h-full')
    expect(grid).not.toHaveClass('h-full')
    expect(grid).not.toHaveClass('min-h-full')
    expect(startupPlan).toHaveClass('sm:h-full')
    expect(startupPlan).not.toHaveClass('h-full')
    expect(scalePlan).toHaveClass('sm:h-full')
    expect(scalePlan).not.toHaveClass('h-full')
    expect(screen.getAllByRole('button', { name: /start free trial/i })).toHaveLength(2)
  })

  it('keeps the default trial copy when no unlock variant is requested', () => {
    renderPlans(
      {
        currentPlan: 'free',
        onUpgrade: vi.fn(),
        source: 'trial_onboarding',
      },
    )

    expect(screen.getAllByRole('button', { name: /start free trial/i })).toHaveLength(2)
    expect(screen.queryByText('No charge today. Cancel anytime.')).not.toBeInTheDocument()
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
