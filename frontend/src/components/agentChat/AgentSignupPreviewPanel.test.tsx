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

  it('renders the freeze wall with the agent name at the paused state', () => {
    const onUpgrade = vi.fn()
    renderSignupPreviewPanel({
      status: 'awaiting_signup_completion',
      agentName: 'Bob Smith',
      onUpgrade,
    })

    expect(screen.getByTestId('trial-freeze-wall')).toBeInTheDocument()
    expect(screen.getByText('Bob Smith is mid-job')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /keep Bob working/i })).toBeInTheDocument()

    screen.getByTestId('subscription-plan-scale').click()
    expect(onUpgrade).toHaveBeenCalledWith('scale', 'signup_preview_panel')

    screen.getByTestId('subscription-plan-startup').click()
    expect(onUpgrade).toHaveBeenCalledWith('startup', 'signup_preview_panel')
  })

  it('keeps the classic plans panel before the first-reply pause', () => {
    renderSignupPreviewPanel({
      status: 'awaiting_first_reply_pause',
      agentName: '',
      onUpgrade: vi.fn(),
    })

    expect(screen.queryByTestId('trial-freeze-wall')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your agent is ready.' })).toBeInTheDocument()
  })
})
