import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

import { SubscriptionUpgradePlans } from './SubscriptionUpgradePlans'
import { useSubscriptionStore } from '../../stores/subscriptionStore'

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
  beforeEach(() => {
    useSubscriptionStore.setState(buildInitialSubscriptionState())
  })

  it('does not force full-height stacked plan cards in the expanded modal layout', () => {
    render(
      <SubscriptionUpgradePlans
        currentPlan="free"
        onUpgrade={vi.fn()}
        source="trial_onboarding"
      />,
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
    render(
      <SubscriptionUpgradePlans
        currentPlan="free"
        onUpgrade={vi.fn()}
        source="trial_onboarding"
      />,
    )

    expect(screen.getAllByRole('button', { name: /start free trial/i })).toHaveLength(2)
    expect(screen.queryByText('No charge today. Cancel anytime.')).not.toBeInTheDocument()
  })

  it('uses the unlock copy only when the unlock variant is requested', () => {
    render(
      <SubscriptionUpgradePlans
        currentPlan="free"
        onUpgrade={vi.fn()}
        source="signup_preview_panel"
        trialCopyVariant="unlock_agent"
      />,
    )

    expect(screen.getAllByRole('button', { name: /start for free/i })).toHaveLength(2)
    expect(screen.getAllByText('No charge today. Cancel anytime.')).toHaveLength(2)
  })
})
