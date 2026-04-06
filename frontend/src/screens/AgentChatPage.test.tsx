import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'

import { AgentChatPage } from './AgentChatPage'
import { useSubscriptionStore } from '../stores/subscriptionStore'

const {
  createAgentMock,
  updateAgentMock,
  fetchAgentSpawnIntentMock,
  ensureAuthenticatedMock,
  rosterContext,
  rosterState,
  llmIntelligence,
  agentChatStoreState,
  timelineState,
} = vi.hoisted(() => ({
  createAgentMock: vi.fn(),
  updateAgentMock: vi.fn(),
  fetchAgentSpawnIntentMock: vi.fn(),
  ensureAuthenticatedMock: vi.fn(async () => true),
  rosterContext: {
    type: 'personal',
    id: 'user-1',
    name: 'Test User',
  } as const,
  rosterState: {
    agents: [] as unknown[],
  },
  llmIntelligence: {
    systemDefaultTier: 'standard',
    maxAllowedTier: 'standard',
    options: [
      {
        key: 'standard',
        label: 'Standard',
        multiplier: 1,
      },
    ],
  },
  agentChatStoreState: {
    agentId: null,
    agentColorHex: null,
    agentName: null,
    agentAvatarUrl: null,
    signupPreviewState: 'none',
    hasUnseenActivity: false,
    processingActive: false,
    processingStartedAt: null,
    awaitingResponse: false,
    processingWebTasks: [],
    nextScheduledAt: null,
    streaming: null,
    streamingLastUpdatedAt: null,
    insights: [],
    currentInsightIndex: 0,
    dismissedInsightIds: [],
    insightsPaused: false,
    autoScrollPinned: true,
    setAgentId: vi.fn(),
    sendMessage: vi.fn(),
    receiveRealtimeEvent: vi.fn(),
    finalizeStreaming: vi.fn(),
    refreshProcessing: vi.fn(),
    fetchInsights: vi.fn(),
    startInsightRotation: vi.fn(),
    stopInsightRotation: vi.fn(),
    dismissInsight: vi.fn(),
    setInsightsPaused: vi.fn(),
    setCurrentInsightIndex: vi.fn(),
    setAutoScrollPinned: vi.fn(),
    suppressAutoScrollPin: vi.fn(),
    autoScrollPinSuppressedUntil: null,
    updateProcessing: vi.fn(),
    updateAgentIdentity: vi.fn(),
  },
  timelineState: {
    data: undefined as unknown,
    flatEvents: [] as unknown[],
    initialPageResponse: null as unknown,
    isLoading: false,
    error: null as unknown,
  },
}))

vi.mock('../api/agents', () => ({
  createAgent: createAgentMock,
  updateAgent: updateAgentMock,
}))

vi.mock('../api/agentSpawnIntent', () => ({
  fetchAgentSpawnIntent: fetchAgentSpawnIntentMock,
}))

vi.mock('../api/agentChat', () => ({
  respondToHumanInputRequest: vi.fn(),
  respondToHumanInputRequestsBatch: vi.fn(),
}))

vi.mock('../api/userPreferences', () => ({
  parseNullableBooleanPreference: vi.fn(() => null),
  updateUserPreferences: vi.fn(),
  parseFavoriteAgentIdsPreference: vi.fn(() => []),
  USER_PREFERENCE_KEY_AGENT_CHAT_INSIGHTS_PANEL_EXPANDED: 'agent_chat_insights_panel_expanded',
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_FAVORITE_AGENT_IDS: 'agent_chat_roster_favorite_agent_ids',
  USER_PREFERENCE_KEY_AGENT_CHAT_ROSTER_SORT_MODE: 'agent_chat_roster_sort_mode',
}))

vi.mock('../components/usage/api', () => ({
  fetchUsageSummary: vi.fn(async () => ({
    metrics: {
      quota: {
        total: -1,
        available: -1,
        used_pct: 0,
      },
    },
    extra_tasks: {
      enabled: false,
    },
  })),
  fetchUsageBurnRate: vi.fn(async () => ({
    quota: {
      unlimited: true,
    },
    extra_tasks: {
      enabled: false,
    },
    projection: {
      projected_days_remaining: null,
    },
    snapshot: {
      burn_rate_per_day: null,
    },
  })),
}))

vi.mock('../components/agentChat/AgentChatLayout', async () => {
  const { useSubscriptionStore: mockedUseSubscriptionStore } = await vi.importActual<
    typeof import('../stores/subscriptionStore')
  >('../stores/subscriptionStore')

  return {
    AgentChatLayout: ({
      spawnIntentLoading,
      signupPreviewState,
    }: {
      spawnIntentLoading?: boolean
      signupPreviewState?: string
    }) => {
      const {
        isUpgradeModalOpen,
        upgradeModalSource,
        upgradeModalDismissible,
      } = mockedUseSubscriptionStore()
      return (
        <div>
          <div data-testid="spawn-intent-loading">{String(Boolean(spawnIntentLoading))}</div>
          <div data-testid="signup-preview-state">{signupPreviewState ?? ''}</div>
          {isUpgradeModalOpen ? (
            <div
              data-testid="upgrade-modal"
              data-source={upgradeModalSource ?? ''}
              data-dismissible={String(upgradeModalDismissible)}
            />
          ) : null}
        </div>
      )
    },
  }
})

vi.mock('../components/agentChat/AgentIntelligenceGateModal', () => ({
  AgentIntelligenceGateModal: () => null,
}))

vi.mock('../components/agentChat/CollaboratorInviteDialog', () => ({
  CollaboratorInviteDialog: () => null,
}))

vi.mock('../components/agentChat/ChatSidebar', () => ({
  ChatSidebar: () => null,
}))

vi.mock('../components/agentChat/HighPriorityBanner', () => ({
  HighPriorityBanner: () => null,
}))

vi.mock('../components/agentChat/statusExpansion', () => ({
  findLatestStatusExpansionTargets: vi.fn(() => []),
}))

vi.mock('../hooks/useAgentChatSocket', () => ({
  useAgentChatSocket: vi.fn(() => ({ status: 'connected', lastError: null })),
}))

vi.mock('../hooks/useAgentWebSession', () => ({
  useAgentWebSession: vi.fn(() => ({ status: 'connected', error: null })),
}))

vi.mock('../hooks/useAgentRoster', () => ({
  useAgentRoster: vi.fn(() => ({
    data: {
      context: rosterContext,
      agents: rosterState.agents,
      agentRosterSortMode: 'recent',
      favoriteAgentIds: [],
      insightsPanelExpanded: null,
      requestedAgentStatus: null,
      billingStatus: null,
      llmIntelligence,
    },
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    error: null,
  })),
}))

vi.mock('../hooks/useAgentQuickSettings', () => ({
  useAgentQuickSettings: vi.fn(() => ({
    data: null,
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    updateQuickSettings: vi.fn(),
    updating: false,
  })),
}))

vi.mock('../hooks/useAgentAddons', () => ({
  useAgentAddons: vi.fn(() => ({
    data: null,
    refetch: vi.fn(),
    updateAddons: vi.fn(),
    updating: false,
  })),
}))

vi.mock('../hooks/useAgentPanelRequestsEnabled', () => ({
  useAgentPanelRequestsEnabled: vi.fn(() => ({
    allowAgentPanelRequests: false,
  })),
}))

vi.mock('../hooks/useConsoleContextSwitcher', () => ({
  useConsoleContextSwitcher: vi.fn(() => ({
    data: {
      context: rosterContext,
      personal: rosterContext,
      organizations: [],
      organizationsEnabled: false,
    },
    isSwitching: false,
    error: null,
    switchContext: vi.fn(),
  })),
}))

vi.mock('../stores/agentChatStore', () => {
  const useAgentChatStore = Object.assign(
    (selector: (state: typeof agentChatStoreState) => unknown) => selector(agentChatStoreState),
    {
      getState: () => agentChatStoreState,
    },
  )

  return {
    useAgentChatStore,
    setTimelineQueryClient: vi.fn(),
  }
})

vi.mock('../hooks/useAgentTimeline', () => ({
  useAgentTimeline: vi.fn(() => ({
    data: timelineState.data,
    hasPreviousPage: false,
    hasNextPage: false,
    isFetchingPreviousPage: false,
    isFetchingNextPage: false,
    fetchPreviousPage: vi.fn(),
    fetchNextPage: vi.fn(),
    isLoading: timelineState.isLoading,
    error: timelineState.error,
  })),
  flattenTimelinePages: vi.fn(() => timelineState.flatEvents),
  getInitialPageResponse: vi.fn(() => timelineState.initialPageResponse),
}))

vi.mock('../hooks/useTimelineCacheInjector', () => ({
  refreshTimelineLatestInCache: vi.fn(async () => undefined),
  replacePendingHumanInputRequestsInCache: vi.fn(),
  DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES: 3,
}))

vi.mock('../hooks/useSimplifiedTimeline', () => ({
  collapseDetailedStatusRuns: vi.fn((events: unknown[]) => events),
}))

vi.mock('../hooks/usePageLifecycle', () => ({
  usePageLifecycle: vi.fn(),
}))

function renderAgentChatPage({ agentId = null }: { agentId?: string | null } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <AgentChatPage
        agentId={agentId}
        viewerUserId={1}
        viewerEmail="user@example.com"
      />
    </QueryClientProvider>,
  )
}

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
    trialDaysByPlan: { startup: 14, scale: 14 },
    trialEligible: true,
    ensureAuthenticated: ensureAuthenticatedMock,
  }
}

describe('AgentChatPage trial onboarding', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/app/agents/new?spawn=1')
    createAgentMock.mockReset()
    createAgentMock.mockResolvedValue({
      agent_id: 'agent-1',
      agent_name: 'Test Agent',
      agent_email: 'agent@example.com',
    })
    updateAgentMock.mockReset()
    fetchAgentSpawnIntentMock.mockReset()
    ensureAuthenticatedMock.mockClear()
    useSubscriptionStore.setState(buildInitialSubscriptionState())
    timelineState.data = undefined
    timelineState.flatEvents = []
    timelineState.initialPageResponse = null
    timelineState.isLoading = false
    timelineState.error = null
    rosterState.agents = []
    agentChatStoreState.signupPreviewState = 'none'
    agentChatStoreState.processingActive = false
  })

  afterEach(() => {
    window.history.pushState({}, '', '/')
  })

  it('opens the non-dismissible upgrade modal when the spawn intent requires plan selection', async () => {
    fetchAgentSpawnIntentMock.mockResolvedValue({
      charter: 'Build me an agent',
      charter_override: null,
      preferred_llm_tier: null,
      selected_pipedream_app_slugs: [],
      onboarding_target: 'agent_ui',
      requires_plan_selection: true,
    })

    renderAgentChatPage()

    const modal = await screen.findByTestId('upgrade-modal')
    expect(modal).toHaveAttribute('data-source', 'trial_onboarding')
    expect(modal).toHaveAttribute('data-dismissible', 'false')
    expect(createAgentMock).not.toHaveBeenCalled()
  })

  it('skips the modal and auto-submits the spawn charter when plan selection is not required', async () => {
    fetchAgentSpawnIntentMock.mockResolvedValue({
      charter: 'Build me an agent',
      charter_override: null,
      preferred_llm_tier: null,
      selected_pipedream_app_slugs: ['slack'],
      onboarding_target: null,
      requires_plan_selection: false,
    })

    renderAgentChatPage()

    await waitFor(() => {
      expect(createAgentMock).toHaveBeenCalledWith(
        'Build me an agent',
        'standard',
        null,
        ['slack'],
      )
    })
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('keeps the pricing modal closed during signup preview auto-submit', async () => {
    useSubscriptionStore.setState({
      ...buildInitialSubscriptionState(),
      personalSignupPreviewAvailable: true,
      personalSignupPreviewProcessingAvailable: true,
    })
    fetchAgentSpawnIntentMock.mockResolvedValue({
      charter: 'Help me get started',
      charter_override: null,
      preferred_llm_tier: null,
      selected_pipedream_app_slugs: [],
      onboarding_target: null,
      requires_plan_selection: false,
    })

    renderAgentChatPage()

    await waitFor(() => {
      expect(createAgentMock).toHaveBeenCalledWith(
        'Help me get started',
        'standard',
        null,
        [],
      )
    })
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('treats signup preview as paused once processing is idle after refresh', async () => {
    useSubscriptionStore.setState({
      ...buildInitialSubscriptionState(),
      personalSignupPreviewAvailable: true,
    })
    agentChatStoreState.signupPreviewState = 'awaiting_first_reply_pause'
    agentChatStoreState.processingActive = false
    agentChatStoreState.awaitingResponse = false
    rosterState.agents = [
      {
        id: 'agent-1',
        name: 'Test Agent',
        avatarUrl: null,
        displayColorHex: null,
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        auditUrl: null,
        isOrgOwned: false,
        isCollaborator: false,
        canManageAgent: true,
        canManageCollaborators: true,
        preferredLlmTier: null,
        email: null,
        sms: null,
        lastInteractionAt: null,
        signupPreviewState: 'awaiting_first_reply_pause',
      },
    ]
    timelineState.flatEvents = []
    timelineState.isLoading = false

    window.history.pushState({}, '', '/app/agents/agent-1')
    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('signup-preview-state')).toHaveTextContent(
        'awaiting_signup_completion',
      )
    })
  })
})
