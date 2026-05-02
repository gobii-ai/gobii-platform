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
  ChatSidebar: ({
    desktopMode,
    showEmbeddedSettings,
    onBackFromEmbeddedSettings,
  }: {
    desktopMode?: string
    showEmbeddedSettings?: boolean
    onBackFromEmbeddedSettings?: () => void
  }) => (
    <div
      data-testid="chat-sidebar"
      data-mode={desktopMode ?? ''}
      data-embedded-settings={String(Boolean(showEmbeddedSettings))}
    >
      <button type="button" data-testid="chat-sidebar-back" onClick={() => onBackFromEmbeddedSettings?.()}>
        Back
      </button>
    </div>
  ),
}))

vi.mock('./AgentChatBanner', () => ({
  AgentChatBanner: ({
    children,
    onPlanOpen,
    planPanelMode,
  }: {
    children?: React.ReactNode
    onPlanOpen?: () => void
    planPanelMode?: string
  }) => (
    <div>
      <button
        type="button"
        data-testid="banner-plan-button"
        data-plan-mode={planPanelMode ?? ''}
        onClick={() => onPlanOpen?.()}
      >
        Plan
      </button>
      {children}
    </div>
  ),
}))

vi.mock('./AgentChatMobileSheet', () => ({
  AgentChatMobileSheet: ({
    open,
    title,
    tone,
  }: {
    open: boolean
    title: string
    tone?: string
  }) => (
    open ? (
      <div data-testid={`mobile-sheet-${title}`} data-tone={tone ?? ''} />
    ) : null
  ),
}))

vi.mock('./AgentChatSettingsPanel', () => ({
  AgentChatSettingsPanel: ({
    open,
    onOpenFullSettings,
  }: {
    open: boolean
    onOpenFullSettings?: () => void
  }) => (
    <div data-testid="agent-chat-settings-panel" data-open={String(open)}>
      <button type="button" data-testid="agent-chat-settings-more" onClick={() => onOpenFullSettings?.()}>
        More settings
      </button>
    </div>
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
      agentName="Agent"
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

  it('routes quick settings to the embedded full settings view callback', () => {
    const handleOpenFullSettings = vi.fn()

    render(
      <AgentChatLayout
        agentId="agent-123"
        agentFirstName="Agent"
        events={[{ cursor: 'message-1', kind: 'message', messageLinkHref: '/console/agents/agent-123/' } as any]}
        onUpdateDailyCredits={vi.fn(async () => undefined)}
        onOpenFullSettings={handleOpenFullSettings}
      />,
    )

    fireEvent.click(screen.getByTestId('timeline-message-link'))
    expect(screen.getByTestId('agent-chat-settings-panel')).toHaveAttribute('data-open', 'true')

    fireEvent.click(screen.getByTestId('agent-chat-settings-more'))

    expect(handleOpenFullSettings).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('agent-chat-settings-panel')).toHaveAttribute('data-open', 'false')
  })

  it('forces the sidebar into gallery mode while embedded settings are visible', () => {
    const { rerender } = render(
      <AgentChatLayout
        agentFirstName="Agent"
        events={[]}
      />,
    )

    expect(screen.getByTestId('chat-sidebar')).toHaveAttribute('data-mode', 'list')

    rerender(
      <AgentChatLayout
        agentFirstName="Agent"
        agentName="Agent"
        events={[]}
        showEmbeddedSettings
        embeddedSettingsPanel={<div>Settings</div>}
      />,
    )

    expect(screen.getByTestId('chat-sidebar')).toHaveAttribute('data-mode', 'gallery')
    expect(screen.getByTestId('chat-sidebar')).toHaveAttribute('data-embedded-settings', 'true')
  })

  it('toggles the desktop plan panel in non-gallery mode', () => {
    renderAgentChatLayout()

    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'docked')
    expect(screen.getByTestId('banner-plan-button')).toHaveAttribute('data-plan-mode', 'docked')

    fireEvent.click(screen.getByTestId('banner-plan-button'))

    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'floating')
    expect(screen.getByTestId('banner-plan-button')).toHaveAttribute('data-plan-mode', 'floating')
  })

  it('forces the desktop plan panel to float in gallery mode without changing stored mode', () => {
    const { rerender } = renderAgentChatLayout()

    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'docked')

    rerender(
      <AgentChatLayout
        agentFirstName="Agent"
        agentName="Agent"
        events={[]}
        showEmbeddedSettings
        embeddedSettingsPanel={<div>Settings</div>}
      />,
    )

    expect(screen.getByTestId('chat-sidebar')).toHaveAttribute('data-mode', 'gallery')
    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'floating')
    expect(screen.getByTestId('banner-plan-button')).toHaveAttribute('data-plan-mode', 'floating')

    fireEvent.click(screen.getByTestId('banner-plan-button'))
    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'floating')

    rerender(
      <AgentChatLayout
        agentFirstName="Agent"
        agentName="Agent"
        events={[]}
      />,
    )

    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'docked')
  })

  it('opens the plan sheet from the banner button on mobile', () => {
    window.innerWidth = 500

    renderAgentChatLayout()

    expect(screen.queryByTestId('mobile-sheet-Plan')).not.toBeInTheDocument()

    fireEvent.click(screen.getByTestId('banner-plan-button'))

    expect(screen.getByTestId('mobile-sheet-Plan')).toHaveAttribute('data-tone', 'plan')
    expect(document.getElementById('agent-workspace-root')).toHaveAttribute('data-plan-mode', 'docked')
  })
})
