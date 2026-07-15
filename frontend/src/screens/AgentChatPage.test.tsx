import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ComponentProps } from 'react'

import { AgentChatPage } from './AgentChatPage'
import type { AppStore } from '../store/appStore'
import { chatActions } from '../store/chatSlice'
import { createTestAppStore, seedSubscriptionState, StoreProvider } from '../test/storeTestUtils'

class FakeNotification {
  static permission: NotificationPermission = 'granted'
  static nextPermission: NotificationPermission = 'granted'
  static requestPermissionMock = vi.fn(async () => {
    FakeNotification.permission = FakeNotification.nextPermission
    return FakeNotification.permission
  })

  static requestPermission() {
    return FakeNotification.requestPermissionMock()
  }
}

const {
  createAgentMock,
  createSystemMessageMock,
  updateAgentMock,
  fetchAgentSpawnIntentMock,
  updateUserPreferencesMock,
  useQuickSettingsMock,
  useAddonsMock,
  quickSettingsRefetchMock,
  addonsRefetchMock,
  panelRequestsState,
  rosterContext,
  rosterState,
  llmIntelligence,
  agentChatStoreState,
  timelineState,
  usageBurnRateState,
} = vi.hoisted(() => ({
  createAgentMock: vi.fn(),
  createSystemMessageMock: vi.fn(),
  updateAgentMock: vi.fn(),
  fetchAgentSpawnIntentMock: vi.fn(),
  updateUserPreferencesMock: vi.fn(),
  useQuickSettingsMock: vi.fn(),
  useAddonsMock: vi.fn(),
  quickSettingsRefetchMock: vi.fn(),
  addonsRefetchMock: vi.fn(),
  panelRequestsState: { allow: true },
  rosterContext: {
    type: 'personal',
    id: 'user-1',
    name: 'Test User',
  } as const,
  rosterState: {
    agents: [] as unknown[],
    agentChatNotificationsEnabled: true,
    mutedAgentIds: [] as string[],
    personalSignupPreviewCreateAvailable: false,
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
    agentId: null as string | null,
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
    pendingEvents: [] as unknown[],
    setAgentId: vi.fn(),
    sendMessage: vi.fn(),
    receiveRealtimeEvent: vi.fn(),
    finalizeStreaming: vi.fn(),
    refreshProcessing: vi.fn(),
    persistPendingEventsToCache: vi.fn(),
    setInsightsForAgent: vi.fn(),
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
  usageBurnRateState: {
    quota: { unlimited: true },
    extra_tasks: { enabled: false },
    projection: { projected_days_remaining: null as number | null },
    snapshot: { burn_rate_per_day: null as number | null },
  },
}))

vi.mock('../api/agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agents')>()),
  createAgent: createAgentMock,
  updateAgent: updateAgentMock,
}))

vi.mock('../api/agentAudit', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api/agentAudit')>()),
  createSystemMessage: createSystemMessageMock,
}))

vi.mock('../api/agentSpawnIntent', () => ({
  fetchAgentSpawnIntent: fetchAgentSpawnIntentMock,
}))

vi.mock('../api/agentChat', () => ({
  dismissHumanInputRequest: vi.fn(),
  fulfillRequestedSecrets: vi.fn(),
  removeRequestedSecrets: vi.fn(),
  resolveContactRequests: vi.fn(),
  resolveSpawnRequest: vi.fn(),
  respondToHumanInputRequest: vi.fn(),
  respondToHumanInputRequestsBatch: vi.fn(),
  skipAgentPlanning: vi.fn(),
}))

vi.mock('../api/userPreferences', () => ({
  parseBooleanPreference: vi.fn((value: unknown) => value === true),
  parseNullableBooleanPreference: vi.fn(() => null),
  updateUserPreferences: updateUserPreferencesMock,
  parseFavoriteAgentIdsPreference: vi.fn(() => []),
  USER_PREFERENCE_KEY_AGENT_CHAT_NOTIFICATIONS_ENABLED: 'agent_chat_notifications_enabled',
  USER_PREFERENCE_KEY_AGENT_CHAT_MUTED_AGENT_IDS: 'agent_chat_muted_agent_ids',
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
  fetchUsageBurnRate: vi.fn(async () => usageBurnRateState),
}))

vi.mock('../components/agentChat/AgentChatLayout', async () => {
  const { useAppSelector: mockedUseAppSelector } = await vi.importActual<
    typeof import('../store/hooks')
  >('../store/hooks')
  const emptyEnabledIntegrationTabs: Record<string, true> = {}

  return {
    AgentChatLayout: ({
      spawnIntentLoading,
      agentId,
      sidebar,
      onOpenFullSettings,
      onRefreshDailyCredits,
      onRefreshAddons,
      events,
      templateRecommendations,
      onTemplateRecommendationCreate,
      composerDisabled,
      normalSendDisabledReason,
      developerMode,
      onSendSystemMessage,
      showComposerActionMenu,
      onShare,
      onPublicShare,
    }: {
      spawnIntentLoading?: boolean
      agentId?: string | null
      sidebar?: {
        agents?: Array<{ id: string }>
        showEmbeddedSettings?: boolean
        onConfigureAgent?: (agent: { id: string }) => void
        onBackFromEmbeddedSettings?: () => void
        settings?: {
          notificationsEnabled?: boolean
          notificationStatus?: string
          onNotificationsEnabledChange?: (enabled: boolean) => void
        }
      }
      onOpenFullSettings?: () => void
      onRefreshDailyCredits?: () => void
      onRefreshAddons?: () => void
      events?: Array<{
        kind: string
        message?: {
          bodyText?: string
          status?: string
        }
      }>
      templateRecommendations?: Array<{
        id: string
        name: string
        templateCode: string
        templateId: string
        templateSource: 'organization' | 'public'
      }>
      onTemplateRecommendationCreate?: (template: {
        id: string
        name: string
        templateCode: string
        templateId: string
        templateSource: 'organization' | 'public'
      }, position: number) => void | Promise<void>
      composerDisabled?: boolean
      normalSendDisabledReason?: string | null
      developerMode?: boolean
      onSendSystemMessage?: (body: string) => void | Promise<void>
      showComposerActionMenu?: boolean
      onShare?: () => void
      onPublicShare?: () => void
    }) => {
      const {
        isUpgradeModalOpen,
        upgradeModalSource,
        upgradeModalDismissible,
      } = mockedUseAppSelector((state) => state.subscription)
      const enabledIntegrationTabs = mockedUseAppSelector((state) => (
        agentId ? state.chat.sessionsByAgentId[agentId]?.identity.enabledIntegrationTabs ?? emptyEnabledIntegrationTabs : emptyEnabledIntegrationTabs
      ))
      const configureTarget = sidebar?.agents?.find((agent) => agent.id !== agentId) ?? sidebar?.agents?.[0] ?? null
      const sidebarNotificationsEnabled = sidebar?.settings?.notificationsEnabled
      const hasAgentReply = events?.some((event) => event.kind === 'message' && event.message?.status !== 'sending') ?? false
      const signupPreviewState = (
        agentChatStoreState.signupPreviewState === 'awaiting_first_reply_pause'
        && !agentChatStoreState.processingActive
        && (!agentChatStoreState.awaitingResponse || hasAgentReply)
      )
        ? 'awaiting_signup_completion'
        : agentChatStoreState.signupPreviewState
      return (
        <div>
          <div data-testid="spawn-intent-loading">{String(Boolean(spawnIntentLoading))}</div>
          <div data-testid="signup-preview-state">{signupPreviewState ?? ''}</div>
          <div data-testid="active-agent-id">{agentId ?? ''}</div>
          <div data-testid="embedded-settings-open">{String(Boolean(sidebar?.showEmbeddedSettings))}</div>
          <div data-testid="notifications-enabled">{String(Boolean(sidebarNotificationsEnabled))}</div>
          <div data-testid="notification-status">{sidebar?.settings?.notificationStatus ?? ''}</div>
          <div data-testid="google-sheets-drive-tab-enabled">{String(Boolean(enabledIntegrationTabs.googleSheetsDrive))}</div>
          <div data-testid="apollo-native-tab-enabled">{String(Boolean(enabledIntegrationTabs.apolloNative))}</div>
          <div data-testid="hubspot-native-tab-enabled">{String(Boolean(enabledIntegrationTabs.hubspotNative))}</div>
          <div data-testid="discord-native-tab-enabled">{String(Boolean(enabledIntegrationTabs.discordNative))}</div>
          <div data-testid="meta-ads-tab-enabled">{String(Boolean(enabledIntegrationTabs.metaAds))}</div>
          <div data-testid="timeline-event-count">{events?.length ?? 0}</div>
          <div data-testid="developer-mode">{String(Boolean(developerMode))}</div>
          <div data-testid="composer-disabled">{String(Boolean(composerDisabled))}</div>
          <div data-testid="normal-send-disabled-reason">{normalSendDisabledReason ?? ''}</div>
          <div data-testid="composer-action-menu-visible">{String(showComposerActionMenu !== false)}</div>
          <div data-testid="collaborate-visible">{String(Boolean(onShare))}</div>
          <div data-testid="public-share-visible">{String(Boolean(onPublicShare))}</div>
          <button type="button" data-testid="send-system-message" onClick={() => void onSendSystemMessage?.('Staff directive')}>
            Send system message
          </button>
          {templateRecommendations?.map((template, index) => (
            <button
              key={template.id}
              type="button"
              data-testid={`template-recommendation-${template.id}`}
              onClick={() => {
                void onTemplateRecommendationCreate?.(template, index)
              }}
            >
              {template.name}
            </button>
          ))}
          {events?.map((event, index) => (
            event.kind === 'message' ? (
              <div
                key={index}
                data-testid="timeline-message"
                data-status={event.message?.status ?? ''}
              >
                {event.message?.bodyText ?? ''}
              </div>
            ) : null
          ))}
          <button
            type="button"
            data-testid="configure-agent"
            onClick={() => {
              if (configureTarget) {
                sidebar?.onConfigureAgent?.(configureTarget)
              }
            }}
          >
            Configure
          </button>
          <button type="button" data-testid="back-from-settings" onClick={() => sidebar?.onBackFromEmbeddedSettings?.()}>
            Back
          </button>
          <button type="button" data-testid="open-full-settings" onClick={() => onOpenFullSettings?.()}>
            Open full settings
          </button>
          <button type="button" data-testid="open-quick-settings" onClick={() => onRefreshDailyCredits?.()}>
            Open quick settings
          </button>
          <button type="button" data-testid="open-addons" onClick={() => onRefreshAddons?.()}>
            Open addons
          </button>
          <button
            type="button"
            onClick={() => sidebar?.settings?.onNotificationsEnabledChange?.(!Boolean(sidebarNotificationsEnabled))}
          >
            Toggle notifications
          </button>
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
  AgentIntelligenceGateModal: ({
    open,
    onContinue,
  }: {
    open?: boolean
    onContinue?: () => void
  }) => (
    open ? (
      <button type="button" data-testid="intelligence-gate-continue" onClick={() => onContinue?.()}>
        Continue
      </button>
    ) : null
  ),
}))

vi.mock('../components/agentChat/EmbeddedAgentSettingsPanel', () => ({
  EmbeddedAgentSettingsPanel: () => <div data-testid="embedded-agent-settings-panel" />,
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

vi.mock('../hooks/useDeveloperModeSocket', () => ({
  useDeveloperModeSocket: vi.fn(),
}))

vi.mock('../hooks/useAgentWebSession', () => ({
  useAgentWebSession: vi.fn(() => ({ status: 'connected', error: null })),
}))

vi.mock('../hooks/useCreatedAgentProfileRefresh', () => ({
  useCreatedAgentProfileRefresh: vi.fn(),
}))

vi.mock('../hooks/useAgentRoster', () => ({
  useAgentRoster: vi.fn(() => ({
    data: {
      context: {
        ...rosterContext,
        personalSignupPreviewCreateAvailable: rosterState.personalSignupPreviewCreateAvailable,
      },
      agents: rosterState.agents,
      agentRosterSortMode: 'recent',
      favoriteAgentIds: [],
      mutedAgentIds: rosterState.mutedAgentIds,
      insightsPanelExpanded: null,
      agentChatNotificationsEnabled: rosterState.agentChatNotificationsEnabled,
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
  useAgentQuickSettings: (...args: unknown[]) => {
    useQuickSettingsMock(...args)
    return {
      data: null,
      isLoading: false,
      error: null,
      refetch: quickSettingsRefetchMock,
      updateQuickSettings: vi.fn(),
      updating: false,
    }
  },
}))

vi.mock('../hooks/useAgentAddons', () => ({
  useAgentAddons: (...args: unknown[]) => {
    useAddonsMock(...args)
    return {
      data: null,
      refetch: addonsRefetchMock,
      updateAddons: vi.fn(),
      updating: false,
    }
  },
}))

vi.mock('../hooks/useAgentInsights', () => ({
  useAgentInsights: vi.fn(() => ({
    data: undefined,
    dataUpdatedAt: 0,
    isFetching: false,
    isStale: false,
  })),
}))

vi.mock('../hooks/useAgentPanelRequestsEnabled', () => ({
  useAgentPanelRequestsEnabled: vi.fn(() => ({
    allowAgentPanelRequests: panelRequestsState.allow,
  })),
}))

vi.mock('../hooks/useConsoleContextSwitcher', () => ({
  useConsoleContextSwitcher: vi.fn(({ forAgentId }: { forAgentId?: string } = {}) => ({
    data: {
      context: rosterContext,
      personal: rosterContext,
      organizations: [],
      organizationsEnabled: false,
    },
    resolvedForAgentId: forAgentId,
    isLoading: false,
    isSwitching: false,
    error: null,
    switchContext: vi.fn(),
  })),
}))

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
  replacePendingActionRequestsInCache: vi.fn(),
  DEFAULT_CONTIGUOUS_BACKFILL_MAX_PAGES: 3,
}))

vi.mock('../hooks/useSimplifiedTimeline', () => ({
  collapseDetailedStatusRuns: vi.fn((events: unknown[]) => events),
}))

vi.mock('../hooks/usePageLifecycle', () => ({
  usePageLifecycle: vi.fn(),
}))

let appStore: AppStore
let queryClient: QueryClient

function createTestQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  })
}

function renderAgentChatPage(
  options: {
    agentId?: string | null
    props?: Partial<ComponentProps<typeof AgentChatPage>>
  } = { agentId: null },
) {
  if (!appStore) {
    queryClient = createTestQueryClient()
    appStore = createTestAppStore({ queryClient })
  }
  const props: ComponentProps<typeof AgentChatPage> = {
    viewerUserId: 1,
    viewerEmail: 'user@example.com',
    ...options.props,
  }
  if ('agentId' in options) {
    props.agentId = options.agentId
  }

  return render(
    <StoreProvider store={appStore}>
      <QueryClientProvider client={queryClient}>
        <AgentChatPage {...props} />
      </QueryClientProvider>
    </StoreProvider>,
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
  }
}

function buildRosterAgent(id: string, name: string, enabledSystemSkills: string[] = []) {
  return {
    id,
    name,
    avatarUrl: null,
    isActive: true,
    processingActive: false,
    lastInteractionAt: null,
    miniDescription: '',
    shortDescription: '',
    listingDescription: '',
    listingDescriptionSource: null,
    displayTags: [],
    detailUrl: `/app/agents/${id}/settings`,
    dailyCreditRemaining: null,
    dailyCreditLow: false,
    last24hCreditBurn: null,
    enabledSystemSkills,
  }
}

describe('AgentChatPage trial onboarding', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/app/agents/new?spawn=1')
    createAgentMock.mockReset()
    createSystemMessageMock.mockReset()
    createSystemMessageMock.mockResolvedValue({})
    createAgentMock.mockResolvedValue({
      agent_id: 'agent-1',
      agent_name: 'Test Agent',
      agent_email: 'agent@example.com',
      agent: {
        id: 'agent-1',
        name: 'Test Agent',
        avatar_url: null,
        is_active: true,
        processing_active: false,
        mini_description: '',
        short_description: '',
        listing_description: '',
        listing_description_source: null,
        display_tags: [],
        detail_url: '/app/agents/agent-1/settings',
        daily_credit_remaining: null,
        daily_credit_low: false,
        last_24h_credit_burn: null,
        is_org_owned: false,
        is_collaborator: false,
        can_manage_agent: true,
        can_manage_collaborators: true,
        preferred_llm_tier: 'standard',
        email: 'agent@example.com',
        sms: null,
        last_interaction_at: null,
      },
    })
    updateAgentMock.mockReset()
    fetchAgentSpawnIntentMock.mockReset()
    updateUserPreferencesMock.mockReset()
    updateUserPreferencesMock.mockResolvedValue({ preferences: {} })
    useQuickSettingsMock.mockClear()
    useAddonsMock.mockClear()
    quickSettingsRefetchMock.mockReset()
    addonsRefetchMock.mockReset()
    panelRequestsState.allow = true
    queryClient = createTestQueryClient()
    appStore = createTestAppStore({ queryClient })
    seedSubscriptionState(appStore, buildInitialSubscriptionState())
    timelineState.data = undefined
    timelineState.flatEvents = []
    timelineState.initialPageResponse = null
    timelineState.isLoading = false
    timelineState.error = null
    usageBurnRateState.quota.unlimited = true
    usageBurnRateState.extra_tasks.enabled = false
    usageBurnRateState.projection.projected_days_remaining = null
    usageBurnRateState.snapshot.burn_rate_per_day = null
    agentChatStoreState.agentId = null
    agentChatStoreState.pendingEvents = []
    agentChatStoreState.hasUnseenActivity = false
    agentChatStoreState.awaitingResponse = false
    rosterState.agents = []
    rosterState.agentChatNotificationsEnabled = true
    rosterState.mutedAgentIds = []
    rosterState.personalSignupPreviewCreateAvailable = false
    agentChatStoreState.signupPreviewState = 'none'
    agentChatStoreState.processingActive = false
    FakeNotification.permission = 'granted'
    FakeNotification.nextPermission = 'granted'
    FakeNotification.requestPermissionMock.mockClear()
    Object.defineProperty(window, 'Notification', {
      configurable: true,
      value: FakeNotification,
    })
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
    const invalidateQueries = vi.spyOn(queryClient, 'invalidateQueries')
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
        'email',
        [],
        undefined,
      )
    })
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
    expect(invalidateQueries).not.toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['agent-roster'] }),
    )
  })

  it('keeps the pricing modal closed during signup preview auto-submit', async () => {
    seedSubscriptionState(appStore, {
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
        'email',
        [],
        undefined,
      )
    })
    expect(screen.queryByTestId('upgrade-modal')).not.toBeInTheDocument()
  })

  it('routes template recommendations through the new-agent credit gate', async () => {
    window.history.pushState({}, '', '/app/agents/new')
    usageBurnRateState.quota.unlimited = false
    usageBurnRateState.projection.projected_days_remaining = 1
    usageBurnRateState.snapshot.burn_rate_per_day = 5
    fetchAgentSpawnIntentMock.mockResolvedValue({
      charter: '',
      charter_override: null,
      preferred_llm_tier: null,
      selected_pipedream_app_slugs: [],
      onboarding_target: null,
      requires_plan_selection: false,
      template_recommendations: {
        category: 'People',
        categories: ['People'],
        source: 'category',
        templates: [
          {
            id: 'template-1',
            name: 'Talent Scout',
            tagline: 'Find candidates.',
            description: 'Find candidates.',
            category: 'People',
            templateCode: 'talent-scout',
            templateId: 'template-1',
            templateSource: 'public',
            likeCount: 4,
            isOfficial: true,
          },
        ],
      },
    })

    renderAgentChatPage()

    fireEvent.click(await screen.findByTestId('template-recommendation-template-1'))

    expect(createAgentMock).not.toHaveBeenCalled()
    fireEvent.click(await screen.findByTestId('intelligence-gate-continue'))

    await waitFor(() => {
      expect(createAgentMock).toHaveBeenCalledWith(
        'Talent Scout',
        'standard',
        null,
        [],
        'web',
        [],
        {
          templateCode: 'talent-scout',
          templateId: 'template-1',
          templateSource: 'public',
        },
      )
    })
  })

  it('allows empty-state preview creation when the roster says preview creation is available', async () => {
    window.history.pushState({}, '', '/app/agents')
    seedSubscriptionState(appStore, {
      ...buildInitialSubscriptionState(),
      personalSignupPreviewAvailable: true,
      personalSignupPreviewProcessingAvailable: true,
    })
    rosterState.personalSignupPreviewCreateAvailable = true

    renderAgentChatPage({ agentId: undefined })

    const createButton = await screen.findByRole('button', { name: /create your first agent/i })
    expect(createButton).not.toHaveAttribute('aria-disabled')
    expect(screen.queryByText(/finish signup to create another agent/i)).not.toBeInTheDocument()
  })

  it('defers quick settings and add-ons until their panels are requested', async () => {
    window.history.pushState({}, '', '/app/agents/agent-1')
    rosterState.agents = [buildRosterAgent('agent-1', 'Test Agent')]

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(useQuickSettingsMock).toHaveBeenLastCalledWith('agent-1', { enabled: false })
      expect(useAddonsMock).toHaveBeenLastCalledWith('agent-1', { enabled: false })
    })

    fireEvent.click(screen.getByTestId('open-quick-settings'))
    fireEvent.click(screen.getByTestId('open-addons'))

    await waitFor(() => {
      expect(useQuickSettingsMock).toHaveBeenLastCalledWith('agent-1', { enabled: true })
      expect(useAddonsMock).toHaveBeenLastCalledWith('agent-1', { enabled: true })
    })
    expect(quickSettingsRefetchMock).toHaveBeenCalled()
    expect(addonsRefetchMock).toHaveBeenCalled()
  })

  it('keeps system messaging available in an override-only developer view', async () => {
    rosterState.agents = [
      {
        ...buildRosterAgent('agent-1', 'Test Agent'),
        canManageAgent: true,
        canSendMessages: false,
      },
    ]

    renderAgentChatPage({
      agentId: 'agent-1',
      props: {
        isSystemAdmin: true,
        developerMode: true,
        staffContext: { type: 'organization', id: 'org-1' },
      },
    })

    expect(await screen.findByTestId('developer-mode')).toHaveTextContent('true')
    expect(screen.getByTestId('composer-disabled')).toHaveTextContent('false')
    expect(screen.getByTestId('normal-send-disabled-reason')).toHaveTextContent(/read-only/i)
    expect(screen.getByTestId('composer-action-menu-visible')).toHaveTextContent('false')
    expect(screen.getByTestId('collaborate-visible')).toHaveTextContent('false')
    expect(screen.getByTestId('public-share-visible')).toHaveTextContent('false')

    fireEvent.click(screen.getByTestId('send-system-message'))
    await waitFor(() => {
      expect(createSystemMessageMock).toHaveBeenCalledWith('agent-1', { body: 'Staff directive' })
    })
  })

  it('keeps empty-state preview creation blocked when an active preview already exists', async () => {
    window.history.pushState({}, '', '/app/agents')
    seedSubscriptionState(appStore, {
      ...buildInitialSubscriptionState(),
      personalSignupPreviewAvailable: true,
      personalSignupPreviewProcessingAvailable: true,
    })
    rosterState.personalSignupPreviewCreateAvailable = false

    renderAgentChatPage({ agentId: undefined })

    const createButton = await screen.findByRole('button', { name: /create your first agent/i })
    expect(createButton).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(/finish signup to create another agent/i)).toBeInTheDocument()
  })

  it('treats signup preview as paused once processing is idle after refresh', async () => {
    seedSubscriptionState(appStore, {
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
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        developerLiveChatUrl: null,
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

  it('renders pending optimistic messages for the active agent immediately', async () => {
    const sentAt = '2026-07-01T12:00:00.000Z'
    rosterState.agents = [
      {
        id: 'agent-1',
        name: 'Test Agent',
        avatarUrl: null,
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        developerLiveChatUrl: null,
        isOrgOwned: false,
        isCollaborator: false,
        canManageAgent: true,
        canManageCollaborators: true,
        preferredLlmTier: null,
        email: null,
        sms: null,
        lastInteractionAt: null,
        signupPreviewState: 'none',
        planningState: 'skipped',
      },
    ]
    timelineState.flatEvents = [
      {
        kind: 'message',
        cursor: '1000:message:server-1',
        message: {
          id: 'server-1',
          bodyText: 'Existing message',
          isOutbound: false,
          channel: 'web',
          timestamp: sentAt,
          relativeTimestamp: null,
        },
      },
    ]
    const pendingEvent = {
      kind: 'message' as const,
      cursor: '2000:message:local-1',
      message: {
        id: 'local-1',
        bodyText: 'Pending hello',
        isOutbound: false,
        channel: 'web',
        timestamp: sentAt,
        relativeTimestamp: null,
        clientId: 'local-1',
        status: 'sending' as const,
      },
    }

    window.history.pushState({}, '', '/app/agents/agent-1')
    renderAgentChatPage({ agentId: 'agent-1' })
    appStore.dispatch(chatActions.autoScrollPinnedSet({ agentId: 'agent-1', pinned: false }))
    appStore.dispatch(chatActions.realtimeEventReceived({
      agentId: 'agent-1',
      event: pendingEvent,
    }))

    await waitFor(() => {
      expect(screen.getByTestId('timeline-event-count')).toHaveTextContent('2')
    })
    const pendingMessage = screen.getByText('Pending hello')
    expect(pendingMessage).toHaveAttribute('data-status', 'sending')
  })

  it('hydrates and persists the notifications preference from roster data', async () => {
    rosterState.agentChatNotificationsEnabled = false
    rosterState.agents = [
      {
        id: 'agent-1',
        name: 'Test Agent',
        avatarUrl: null,
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        developerLiveChatUrl: null,
        isOrgOwned: false,
        isCollaborator: false,
        canManageAgent: true,
        canManageCollaborators: true,
        preferredLlmTier: null,
        email: null,
        sms: null,
        lastInteractionAt: null,
        signupPreviewState: 'none',
        planningState: 'skipped',
      },
    ]
    FakeNotification.permission = 'default'
    FakeNotification.nextPermission = 'granted'
    updateUserPreferencesMock.mockResolvedValue({
      preferences: {
        agent_chat_notifications_enabled: true,
      },
    })

    window.history.pushState({}, '', '/app/agents/agent-1')
    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('notifications-enabled')).toHaveTextContent('false')
    })
    expect(FakeNotification.requestPermissionMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Toggle notifications' }))

    await waitFor(() => {
      expect(updateUserPreferencesMock).toHaveBeenCalledWith({
        preferences: {
          agent_chat_notifications_enabled: true,
        },
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('notifications-enabled')).toHaveTextContent('true')
    })
  })

  it('requests browser permission for enabled notifications and disables them if denied', async () => {
    rosterState.agentChatNotificationsEnabled = true
    rosterState.agents = [
      {
        id: 'agent-1',
        name: 'Test Agent',
        avatarUrl: null,
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        developerLiveChatUrl: null,
        isOrgOwned: false,
        isCollaborator: false,
        canManageAgent: true,
        canManageCollaborators: true,
        preferredLlmTier: null,
        email: null,
        sms: null,
        lastInteractionAt: null,
        signupPreviewState: 'none',
        planningState: 'skipped',
      },
    ]
    FakeNotification.permission = 'default'
    FakeNotification.nextPermission = 'denied'
    updateUserPreferencesMock.mockResolvedValue({
      preferences: {
        agent_chat_notifications_enabled: false,
      },
    })

    window.history.pushState({}, '', '/app/agents/agent-1')
    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(FakeNotification.requestPermissionMock).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(updateUserPreferencesMock).toHaveBeenCalledWith({
        preferences: {
          agent_chat_notifications_enabled: false,
        },
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('notifications-enabled')).toHaveTextContent('false')
    })
  })

  it('enables the toggle when the user allows browser notifications', async () => {
    rosterState.agentChatNotificationsEnabled = false
    rosterState.agents = [
      {
        id: 'agent-1',
        name: 'Test Agent',
        avatarUrl: null,
        isActive: true,
        processingActive: false,
        miniDescription: '',
        shortDescription: '',
        developerLiveChatUrl: null,
        isOrgOwned: false,
        isCollaborator: false,
        canManageAgent: true,
        canManageCollaborators: true,
        preferredLlmTier: null,
        email: null,
        sms: null,
        lastInteractionAt: null,
        signupPreviewState: 'none',
        planningState: 'skipped',
      },
    ]
    FakeNotification.permission = 'default'
    FakeNotification.nextPermission = 'granted'
    updateUserPreferencesMock.mockResolvedValue({
      preferences: {
        agent_chat_notifications_enabled: true,
      },
    })

    window.history.pushState({}, '', '/app/agents/agent-1')
    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('notifications-enabled')).toHaveTextContent('false')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Toggle notifications' }))

    await waitFor(() => {
      expect(FakeNotification.requestPermissionMock).toHaveBeenCalledTimes(1)
    })
    await waitFor(() => {
      expect(updateUserPreferencesMock).toHaveBeenCalledWith({
        preferences: {
          agent_chat_notifications_enabled: true,
        },
      })
    })
    await waitFor(() => {
      expect(screen.getByTestId('notifications-enabled')).toHaveTextContent('true')
    })
  })

  it('opens embedded settings from the active agent route on direct app loads', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    window.history.pushState({}, '', '/app/agents/agent-1/settings')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
    })
  })

  it('ignores malformed live tool search results when checking for Sheets skill enablement', async () => {
    const malformedResult = {}
    Object.defineProperty(malformedResult, 'status', {
      get() {
        throw new Error('malformed result')
      },
    })
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    timelineState.flatEvents = [
      {
        kind: 'steps',
        cursor: 'step:1',
        entryCount: 1,
        collapsible: false,
        collapseThreshold: 4,
        earliestTimestamp: '2026-01-01T00:00:00Z',
        latestTimestamp: '2026-01-01T00:00:00Z',
        entries: [
          {
            id: 'search-1',
            cursor: 'step:1',
            timestamp: '2026-01-01T00:00:00Z',
            toolName: 'search_tools',
            parameters: { query: 'google sheets' },
            result: malformedResult,
            status: 'complete',
          },
        ],
      },
    ]
    window.history.pushState({}, '', '/app/agents/agent-1')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('google-sheets-drive-tab-enabled')).toHaveTextContent('false')
    })
    expect(screen.getByTestId('apollo-native-tab-enabled')).toHaveTextContent('false')
    expect(screen.getByTestId('hubspot-native-tab-enabled')).toHaveTextContent('false')
    expect(screen.getByTestId('discord-native-tab-enabled')).toHaveTextContent('false')
    expect(screen.getByTestId('meta-ads-tab-enabled')).toHaveTextContent('false')
  })

  it('passes native tab enablement from roster system skills', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One', ['apollo_native', 'hubspot_native', 'discord_native', 'meta_ads_platform'])]
    window.history.pushState({}, '', '/app/agents/agent-1')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('apollo-native-tab-enabled')).toHaveTextContent('true')
    })
    expect(screen.getByTestId('hubspot-native-tab-enabled')).toHaveTextContent('true')
    expect(screen.getByTestId('discord-native-tab-enabled')).toHaveTextContent('true')
    expect(screen.getByTestId('meta-ads-tab-enabled')).toHaveTextContent('true')
  })

  it('passes native tab enablement from live tool search results', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    timelineState.flatEvents = [
      {
        kind: 'steps',
        cursor: 'step:1',
        entryCount: 1,
        collapsible: false,
        collapseThreshold: 4,
        earliestTimestamp: '2026-01-01T00:00:00Z',
        latestTimestamp: '2026-01-01T00:00:00Z',
        entries: [
          {
            id: 'search-1',
            cursor: 'step:1',
            timestamp: '2026-01-01T00:00:00Z',
            toolName: 'search_tools',
            parameters: { query: 'apollo' },
            result: {
              status: 'success',
              system_skills: {
                enabled: ['apollo_native', 'hubspot_native', 'discord_native', 'meta_ads_platform'],
              },
            },
            status: 'complete',
          },
        ],
      },
    ]
    window.history.pushState({}, '', '/app/agents/agent-1')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('apollo-native-tab-enabled')).toHaveTextContent('true')
    })
    expect(screen.getByTestId('hubspot-native-tab-enabled')).toHaveTextContent('true')
    expect(screen.getByTestId('discord-native-tab-enabled')).toHaveTextContent('true')
    expect(screen.getByTestId('meta-ads-tab-enabled')).toHaveTextContent('true')
  })

  it('opens embedded settings from the direct console shell settings route', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    window.history.pushState({}, '', '/console/agents/agent-1/chat/settings/')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
    })
  })

  it('switches active agents and opens embedded settings when configure is triggered', async () => {
    rosterState.agents = [
      buildRosterAgent('agent-1', 'Agent One'),
      buildRosterAgent('agent-2', 'Agent Two'),
    ]
    window.history.pushState({}, '', '/app/agents/agent-1')

    renderAgentChatPage({ agentId: 'agent-1' })

    fireEvent.click(screen.getByTestId('configure-agent'))

    await waitFor(() => {
      expect(screen.getByTestId('active-agent-id')).toHaveTextContent('agent-2')
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
      expect(window.location.pathname).toBe('/app/agents/agent-2/settings')
    })
  })

  it('returns from embedded settings to the active agent chat route', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    window.history.pushState({}, '', '/app/agents/agent-1/settings')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
    })

    fireEvent.click(screen.getByTestId('back-from-settings'))

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('false')
      expect(window.location.pathname).toBe('/app/agents/agent-1')
    })
  })

  it('exits nested embedded settings to chat when the sidebar back action is triggered', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    window.history.pushState({}, '', '/app/agents/agent-1/email')

    renderAgentChatPage({ agentId: 'agent-1' })

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
    })

    fireEvent.click(screen.getByTestId('back-from-settings'))

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('false')
      expect(window.location.pathname).toBe('/app/agents/agent-1')
    })
  })

  it('opens embedded settings from the quick settings callback', async () => {
    rosterState.agents = [buildRosterAgent('agent-1', 'Agent One')]
    window.history.pushState({}, '', '/app/agents/agent-1')

    renderAgentChatPage({ agentId: 'agent-1' })

    fireEvent.click(screen.getByTestId('open-full-settings'))

    await waitFor(() => {
      expect(screen.getByTestId('embedded-settings-open')).toHaveTextContent('true')
      expect(window.location.pathname).toBe('/app/agents/agent-1/settings')
    })
  })
})
