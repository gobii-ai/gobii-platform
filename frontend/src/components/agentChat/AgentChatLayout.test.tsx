import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AgentChatLayout } from './AgentChatLayout'
import { useSubscriptionStore } from '../../stores/subscriptionStore'

vi.mock('../../util/analytics', () => ({
  track: vi.fn(),
}))

vi.mock('./TypingIndicator', () => ({
  TypingIndicator: () => null,
  deriveTypingStatusText: vi.fn(() => ''),
}))

vi.mock('./AgentComposer', () => ({
  AgentComposer: () => <div data-testid="agent-composer" />,
}))

vi.mock('./TimelineVirtualItem', () => ({
  TimelineVirtualItem: ({ event, onMessageLinkClick }: { event?: { messageLinkHref?: string | null }, onMessageLinkClick?: (href: string) => boolean | void }) => {
    const href = event?.messageLinkHref
    if (!href) {
      return null
    }
    return (
      <a
        data-testid="timeline-message-link"
        href={href}
        onClick={(event) => {
          if (onMessageLinkClick?.(href)) {
            event.preventDefault()
          }
        }}
      >
        Open settings
      </a>
    )
  },
}))

vi.mock('./StreamingReplyCard', () => ({
  StreamingReplyCard: () => null,
}))

vi.mock('./StreamingThinkingCard', () => ({
  StreamingThinkingCard: () => null,
}))

vi.mock('./ChatSidebar', () => ({
  ChatSidebar: () => null,
}))

vi.mock('./AgentChatBanner', () => ({
  AgentChatBanner: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}))

vi.mock('./AgentChatMobileSheet', () => ({
  AgentChatMobileSheet: () => null,
}))

vi.mock('./AgentChatSettingsPanel', () => ({
  AgentChatSettingsPanel: ({ open }: { open: boolean }) => (
    <div data-testid="agent-chat-settings-panel" data-open={String(open)} />
  ),
}))

vi.mock('./AgentChatAddonsPanel', () => ({
  AgentChatAddonsPanel: () => null,
}))

vi.mock('./HighPriorityBanner', () => ({
  HighPriorityBanner: () => null,
}))

vi.mock('./HardLimitCalloutCard', () => ({
  HardLimitCalloutCard: () => null,
}))

vi.mock('./ContactCapCalloutCard', () => ({
  ContactCapCalloutCard: () => null,
}))

vi.mock('./TaskCreditsCalloutCard', () => ({
  TaskCreditsCalloutCard: () => null,
}))

vi.mock('./ScheduledResumeCard', () => ({
  ScheduledResumeCard: () => null,
}))

vi.mock('./StarterPromptSuggestions', () => ({
  StarterPromptSuggestions: () => null,
}))

vi.mock('./useStarterPrompts', () => ({
  useStarterPrompts: vi.fn(() => ({
    starterPrompts: [],
    starterPromptsLoading: false,
    starterPromptSubmitting: false,
    handleStarterPromptSelect: vi.fn(),
  })),
}))

vi.mock('../common/SubscriptionUpgradePlans', () => ({
  SubscriptionUpgradePlans: () => null,
}))

vi.mock('./AgentSignupPreviewPanel', () => ({
  AgentSignupPreviewPanel: ({ status }: { status: string }) => (
    <div data-testid="signup-preview-panel" data-status={status} />
  ),
}))

vi.mock('../common/SubscriptionUpgradeModal', () => ({
  SubscriptionUpgradeModal: ({
    source,
    dismissible,
  }: {
    source?: string
    dismissible?: boolean
  }) => (
    <div
      data-testid="subscription-upgrade-modal"
      data-source={source ?? ''}
      data-dismissible={String(Boolean(dismissible))}
    />
  ),
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
    ctaPickAPlan: true,
    ctaContinueAgentBtn: false,
    ctaNoChargeDuringTrial: true,
    personalSignupPreviewAvailable: false,
    personalSignupPreviewProcessingAvailable: false,
    trialDaysByPlan: { startup: 7, scale: 7 },
    trialEligible: true,
    ensureAuthenticated: vi.fn(async () => true),
  }
}

function renderAgentChatLayout() {
  return render(
    <AgentChatLayout
      agentFirstName="Agent"
      events={[]}
    />,
  )
}

describe('AgentChatLayout upgrade modal gating', () => {
  beforeEach(() => {
    window.innerWidth = 1200
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      value: {
        getItem: vi.fn(() => null),
        setItem: vi.fn(),
        removeItem: vi.fn(),
      },
    })
    useSubscriptionStore.setState(buildInitialSubscriptionState())
  })

  afterEach(() => {
    useSubscriptionStore.setState(buildInitialSubscriptionState())
  })

  it('keeps trial onboarding open while the subscription plan is still loading', async () => {
    useSubscriptionStore.setState({
      ...buildInitialSubscriptionState(),
      isLoading: true,
      isProprietaryMode: false,
      isUpgradeModalOpen: true,
      upgradeModalSource: 'trial_onboarding',
      upgradeModalDismissible: false,
    })

    renderAgentChatLayout()

    expect(screen.queryByTestId('subscription-upgrade-modal')).not.toBeInTheDocument()
    expect(useSubscriptionStore.getState().isUpgradeModalOpen).toBe(true)

    await act(async () => {
      useSubscriptionStore.setState({
        isLoading: false,
        isProprietaryMode: true,
      })
    })

    const modal = await screen.findByTestId('subscription-upgrade-modal')
    expect(modal).toHaveAttribute('data-source', 'trial_onboarding')
    expect(modal).toHaveAttribute('data-dismissible', 'false')
    expect(useSubscriptionStore.getState().isUpgradeModalOpen).toBe(true)
  })

  it('closes the upgrade modal after plan hydration confirms proprietary mode is off', async () => {
    useSubscriptionStore.setState({
      ...buildInitialSubscriptionState(),
      isLoading: false,
      isProprietaryMode: false,
      isUpgradeModalOpen: true,
      upgradeModalSource: 'trial_onboarding',
    })

    renderAgentChatLayout()

    await waitFor(() => {
      expect(useSubscriptionStore.getState().isUpgradeModalOpen).toBe(false)
    })
  })

  it('renders the signup preview panel instead of the composer when requested', () => {
    render(
      <AgentChatLayout
        agentFirstName="Agent"
        events={[]}
        showSignupPreviewPanel
        signupPreviewState="awaiting_signup_completion"
      />,
    )

    expect(screen.getByTestId('signup-preview-panel')).toHaveAttribute(
      'data-status',
      'awaiting_signup_completion',
    )
    expect(screen.queryByTestId('agent-composer')).not.toBeInTheDocument()
  })

  it('renders the composer instead of the signup preview panel while planning', () => {
    render(
      <AgentChatLayout
        agentFirstName="Agent"
        events={[]}
        showSignupPreviewPanel
        signupPreviewState="awaiting_first_reply_pause"
        planningState="planning"
      />,
    )

    expect(screen.queryByTestId('signup-preview-panel')).not.toBeInTheDocument()
    expect(screen.getByTestId('agent-composer')).toBeInTheDocument()
  })

  it('opens the settings panel when a chat message links to the current agent settings page', () => {
    render(
      <AgentChatLayout
        agentId="agent-123"
        agentFirstName="Agent"
        events={[{ cursor: 'message-1', kind: 'message', messageLinkHref: '/console/agents/agent-123/' } as any]}
        onUpdateDailyCredits={vi.fn(async () => undefined)}
      />,
    )

    expect(screen.getByTestId('agent-chat-settings-panel')).toHaveAttribute('data-open', 'false')

    fireEvent.click(screen.getByTestId('timeline-message-link'))

    expect(screen.getByTestId('agent-chat-settings-panel')).toHaveAttribute('data-open', 'true')
  })
})
