import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'

import { AgentSignupPreviewPanel } from './AgentSignupPreviewPanel'
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
    ctaUnlockAgentCopy: true,
    ctaPickAPlan: false,
    ctaContinueAgentBtn: false,
    ctaNoChargeDuringTrial: false,
    personalSignupPreviewAvailable: true,
    personalSignupPreviewProcessingAvailable: true,
    trialDaysByPlan: { startup: 7, scale: 7 },
    trialEligible: true,
    ensureAuthenticated: vi.fn(async () => true),
  }
}

describe('AgentSignupPreviewPanel', () => {
  let appStore: AppStore

  beforeEach(() => {
    appStore = createTestAppStore()
    seedSubscriptionState(appStore, buildInitialSubscriptionState())
  })

  function renderSignupPreviewPanel(props: ComponentProps<typeof AgentSignupPreviewPanel>) {
    return render(
      <StoreProvider store={appStore}>
        <AgentSignupPreviewPanel {...props} />
      </StoreProvider>,
    )
  }

  it('renders the unlock copy with the agent name and scoped trial CTA copy', () => {
    renderSignupPreviewPanel({
      status: 'awaiting_signup_completion',
      agentName: 'Bob Smith',
      onUpgrade: vi.fn(),
    })

    expect(screen.getByRole('heading', { name: 'Bob Smith is ready.' })).toBeInTheDocument()
    expect(screen.getByText('Unlock your agent now.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /start for free/i })).toHaveLength(2)
    expect(screen.getAllByText('No charge today. Cancel anytime.')).toHaveLength(2)
  })

  it('falls back to a generic agent label when no agent name is available', () => {
    renderSignupPreviewPanel({
      status: 'awaiting_first_reply_pause',
      agentName: '',
      onUpgrade: vi.fn(),
    })

    expect(screen.getByRole('heading', { name: 'Your agent is ready.' })).toBeInTheDocument()
  })
})
