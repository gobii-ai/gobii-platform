import type { KeyboardEvent, MouseEvent, ReactNode, Ref } from 'react'
import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { Flag, Loader2, Zap } from 'lucide-react'
import '../../styles/agentChatLegacy.css'
import { deriveTypingStatusText } from './TypingIndicator'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import { AgentComposer } from './AgentComposer'
import { AgentTimelinePane } from './AgentTimelinePane'
import { ChatSidebar, type ChatSidebarProps } from './ChatSidebar'
import { AgentChatBanner, type DeveloperModeControlGroups } from './AgentChatBanner'
import { AgentChatSettingsPanel } from './AgentChatSettingsPanel'
import { AgentChatAddonsPanel } from './AgentChatAddonsPanel'
import { PlanPanel } from './PlanPanel'
import { HighPriorityBanner, type HighPriorityBannerConfig } from './HighPriorityBanner'
import { reportAgentMessageIssue, type PendingActionMutationResult } from '../../api/agentChat'
import { AgentSignupPreviewPanel } from './AgentSignupPreviewPanel'
import { AgentUpgradePlansPanel } from './AgentUpgradePlansPanel'
import type { AgentChatSidebarMode } from './sidebarMode'
import { useStarterPrompts } from './useStarterPrompts'
import { SubscriptionUpgradeModal } from '../common/SubscriptionUpgradeModal'
import { SubscriptionUpgradePlans } from '../common/SubscriptionUpgradePlans'
import { TextareaSubmitDialog } from '../common/TextareaSubmitDialog'
import { ImmersiveDialog } from '../common/ImmersiveDialog'
import { useIsMobile } from '../../hooks/useIsMobile'
import type { SelectionShellPage } from './SelectionShellPageSwitcher'
import type { SidebarSettingsInfo } from './SidebarSettingsMenu'
import type { AgentTimelineProps } from './types'
import type { PendingActionRequest, AgentMessage, PlanSnapshot } from '../../types/agentChat'
import type { SignupPreviewState } from '../../types/agentRoster'
import type { TemplateRecommendation } from '../../api/agentSpawnIntent'
import { isContinuationUpgradeModalSource, selectSubscriptionState, subscriptionActions, type PlanTier } from '../../store/subscriptionSlice'
import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from '../../types/dailyCredits'
import type { AddonPackOption, ContactCapInfo, ContactCapStatus, TrialInfo } from '../../types/agentAddons'
import type { LlmIntelligenceConfig } from '../../types/llmIntelligence'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { StatusExpansionTargets } from './statusExpansion'
import { addInferredPlanFiles, filterChangedPlanSnapshot, hasCompletedPlanDeliverables } from './planSnapshotUtils'
import type { AgentChatShellSubview } from '../../util/agentChatShellRoutes'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { selectActiveChatSession } from '../../store/chatSlice'
import { immersiveShellActions, selectImmersiveShellViewer, selectImmersiveSidebarMode } from '../../store/immersiveShellSlice'

type TaskQuotaInfo = {
  available: number
  total: number
  used: number
  used_pct: number
}

function normalizeAgentSettingsPathname(pathname: string): string {
  const trimmed = pathname.replace(/\/+$/, '')
  return trimmed || '/'
}

function hasAgentResponse(events: SimplifiedTimelineItem[]): boolean {
  return events.some((event) => (
    event.kind === 'message'
    && Boolean((event as { message?: { isOutbound?: boolean } }).message?.isOutbound)
  ))
}

type AgentChatMessageLinkSubview = Exclude<AgentChatShellSubview, 'chat'>
type AppMessageLinkShellPage = Exclude<SelectionShellPage, 'agents'>

function isAgentChatMessageLinkSubview(value?: string | null): value is AgentChatMessageLinkSubview {
  switch (value) {
    case 'settings':
    case 'secrets':
    case 'secret-requests':
    case 'email':
    case 'files':
    case 'contact-requests':
      return true
    default:
      return false
  }
}

function getCurrentAgentMessageLinkSubview(href: string, agentId: string): AgentChatMessageLinkSubview | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const url = new URL(href, window.location.origin)
    const pathname = normalizeAgentSettingsPathname(url.pathname)
    const parts = pathname.split('/').filter(Boolean)

    if (parts[0] === 'app' && parts[1] === 'agents' && parts[2] === agentId) {
      if (
        parts[3] === 'secrets'
        && parts[4] === 'request'
        && parts.length === 5
      ) {
        return 'secret-requests'
      }
      if (parts.length === 4 && isAgentChatMessageLinkSubview(parts[3])) {
        return parts[3]
      }
      return null
    }

    if (parts[0] === 'console' && parts[1] === 'agents' && parts[2] === agentId) {
      if (parts.length === 3) {
        return 'settings'
      }
      if (
        parts[3] === 'secrets'
        && parts[4] === 'request'
        && parts.length === 5
      ) {
        return 'secret-requests'
      }
      if (parts[3] === 'chat' && parts.length === 5 && isAgentChatMessageLinkSubview(parts[4])) {
        return parts[4]
      }
      if (parts.length === 4 && isAgentChatMessageLinkSubview(parts[3])) {
        return parts[3]
      }
    }

    return null
  } catch {
    return null
  }
}

function getAppMessageLinkShellPage(href: string): AppMessageLinkShellPage | null {
  if (typeof window === 'undefined') {
    return null
  }
  try {
    const url = new URL(href, window.location.origin)
    const pathname = normalizeAgentSettingsPathname(url.pathname)
    const parts = pathname.split('/').filter(Boolean)

    if (parts[0] !== 'app' || parts.length !== 2) {
      return null
    }

    switch (parts[1]) {
      case 'billing':
      case 'profile':
      case 'organization':
      case 'secrets':
      case 'usage':
      case 'integrations':
      case 'api-keys':
        return parts[1]
      default:
        return null
    }
  } catch {
    return null
  }
}

type AgentChatLayoutSidebarSettings = Omit<SidebarSettingsInfo, 'isProprietaryMode' | 'taskCredits' | 'viewerEmail'> & {
  todayCreditsUsed?: number | null
  creditsResetOn?: string | null
}

export type AgentChatLayoutSidebarConfig = Omit<
  ChatSidebarProps,
  'activeAgentId' | 'desktopMode' | 'onDesktopModeChange' | 'settings'
> & {
  settings?: AgentChatLayoutSidebarSettings
  insightsPanelExpandedPreference?: boolean | null
  insightsPanelPreferenceHydrated?: boolean
  onInsightsPanelExpandedPreferenceChange?: (expanded: boolean) => void
  suggestionsPreferenceHydrated?: boolean
}

type AgentChatLayoutProps = AgentTimelineProps & {
  displayEvents?: SimplifiedTimelineItem[]
  statusExpansionTargets?: StatusExpansionTargets
  agentId?: string | null
  bannerAgentName?: string | null
  sidebar?: AgentChatLayoutSidebarConfig
  autoFocusComposer?: boolean
  planSnapshot?: PlanSnapshot | null
  footer?: ReactNode
  dailyCredits?: DailyCreditsInfo | null
  dailyCreditsStatus?: DailyCreditsStatus | null
  dailyCreditsLoading?: boolean
  dailyCreditsError?: string | null
  onRefreshDailyCredits?: () => void
  onUpdateDailyCredits?: (payload: DailyCreditsUpdatePayload) => Promise<void>
  dailyCreditsUpdating?: boolean
  onOpenFullSettings?: () => void
  hardLimitUpgradeUrl?: string | null
  hardLimitShowUpsell?: boolean
  contactCap?: ContactCapInfo | null
  contactCapStatus?: ContactCapStatus | null
  contactPackOptions?: AddonPackOption[]
  contactPackCanManageBilling?: boolean
  contactPackShowUpgrade?: boolean
  contactPackUpdating?: boolean
  onUpdateContactPacks?: (quantities: Record<string, number>) => Promise<void>
  onRefreshAddons?: () => void
  contactPackManageUrl?: string | null
  taskPackOptions?: AddonPackOption[]
  taskPackCanManageBilling?: boolean
  taskPackUpdating?: boolean
  onUpdateTaskPacks?: (quantities: Record<string, number>) => Promise<void>
  onStopProcessing?: () => void | Promise<void>
  addonsTrial?: TrialInfo | null
  taskQuota?: TaskQuotaInfo | null
  showPurchaseSeatsPrompt?: boolean
  showTaskCreditsWarning?: boolean
  taskCreditsWarningVariant?: 'low' | 'out' | null
  showTaskCreditsUpgrade?: boolean
  taskCreditsDismissKey?: string | null
  highPriorityBanner?: HighPriorityBannerConfig | null
  onJumpToLatest?: () => void
  onClose?: () => void
  onShare?: () => void
  onPublicShare?: () => void
  onBlockedSettingsClick?: (location: 'banner_desktop' | 'banner_mobile') => void
  onBlockedCollaborate?: (location: 'banner_desktop' | 'banner_mobile' | 'insight_card') => void
  onSendMessage?: (
    body: string,
    attachments?: File[],
  ) => void | Promise<void>
  onSendSystemMessage?: (body: string) => void | Promise<void>
  normalSendDisabledReason?: string | null
  showComposerActionMenu?: boolean
  developerMode?: boolean
  showDeveloperMode?: boolean
  onDeveloperModeChange?: (enabled: boolean) => void
  developerControls?: DeveloperModeControlGroups | null
  onRetryMessage?: (message: AgentMessage) => void | Promise<void>
  onComposerFocus?: () => void
  onComposerRequestScrollToBottom?: () => void
  isNearBottom?: boolean
  timelineRef?: Ref<HTMLDivElement>
  timelineContentRef?: Ref<HTMLDivElement>
  composerShellRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
  insightsLoading?: boolean
  onUpgrade?: (plan: PlanTier, source?: string) => void
  llmIntelligence?: LlmIntelligenceConfig | null
  currentLlmTier?: string | null
  onLlmTierChange?: (tier: string) => Promise<boolean>
  allowLockedIntelligenceSelection?: boolean
  llmTierSaving?: boolean
  llmTierError?: string | null
  onOpenTaskPacks?: () => void
  spawnIntentLoading?: boolean
  templateRecommendations?: TemplateRecommendation[]
  templateRecommendationSubmittingId?: string | null
  onTemplateRecommendationCreate?: (template: TemplateRecommendation, position: number) => void | Promise<void>
  composerDisabled?: boolean
  composerDisabledReason?: string | null
  composerError?: string | null
  composerErrorShowUpgrade?: boolean
  showSubscriptionExpiredPanel?: boolean
  onSkipPlanning?: () => void | Promise<void>
  maxAttachmentBytes?: number | null
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
  nativeIntegrationsUrl?: string | null
  pendingActionRequests?: PendingActionRequest[]
  onRespondHumanInputRequest?: (
    response:
      | { requestId: string; selectedOptionKey?: string; freeText?: string }
      | { batchId: string; responses: Array<{ requestId: string; selectedOptionKey?: string; freeText?: string }> }
  ) => Promise<void>
  onDismissHumanInputRequest?: (requestId: string) => Promise<void>
  onResolveSpawnRequest?: (decisionApiUrl: string, decision: 'approve' | 'decline') => Promise<void>
  onFulfillRequestedSecrets?: (values: Record<string, string>, makeGlobal: boolean) => Promise<void>
  onRemoveRequestedSecrets?: (secretIds: string[]) => Promise<void>
  onOpenAgentSecrets?: () => void
  onOpenAgentSecretRequests?: () => void
  onOpenAgentEmailSettings?: () => void
  onOpenAgentFiles?: () => void
  onResolveContactRequests?: (
    responses: Array<{
      requestId: string
      decision: 'approve' | 'decline'
      allowInbound: boolean
      allowOutbound: boolean
      smsContactPermissionAttested?: boolean
    }>
  ) => Promise<PendingActionMutationResult | void>
  onViewAllContactRequests?: () => void
}

type PlanPanelMode = 'docked' | 'hidden'

export function AgentChatLayout({
  events,
  displayEvents,
  statusExpansionTargets,
  agentId,
  bannerAgentName = null,
  sidebar,
  autoFocusComposer = false,
  planSnapshot,
  footer,
  dailyCredits,
  dailyCreditsStatus,
  dailyCreditsLoading = false,
  dailyCreditsError = null,
  onRefreshDailyCredits,
  onUpdateDailyCredits,
  dailyCreditsUpdating = false,
  onOpenFullSettings,
  hardLimitUpgradeUrl = null,
  hardLimitShowUpsell = false,
  contactCap = null,
  contactCapStatus = null,
  contactPackOptions = [],
  contactPackCanManageBilling = false,
  contactPackShowUpgrade = false,
  contactPackUpdating = false,
  onUpdateContactPacks,
  onRefreshAddons,
  contactPackManageUrl = null,
  taskPackOptions = [],
  taskPackCanManageBilling = false,
  taskPackUpdating = false,
  onUpdateTaskPacks,
  onStopProcessing,
  addonsTrial = null,
  taskQuota = null,
  showPurchaseSeatsPrompt = false,
  showTaskCreditsWarning = false,
  taskCreditsWarningVariant = null,
  showTaskCreditsUpgrade = false,
  taskCreditsDismissKey = null,
  highPriorityBanner = null,
  hasMoreNewer,
  onJumpToLatest,
  onClose,
  onShare,
  onPublicShare,
  onBlockedSettingsClick,
  onBlockedCollaborate,
  onSendMessage,
  onSendSystemMessage,
  normalSendDisabledReason = null,
  showComposerActionMenu = true,
  developerMode = false,
  showDeveloperMode = false,
  onDeveloperModeChange,
  developerControls = null,
  onRetryMessage,
  onComposerFocus,
  onComposerRequestScrollToBottom,
  isNearBottom = true,
  timelineRef,
  timelineContentRef,
  composerShellRef,
  loadingOlder = false,
  loadingNewer = false,
  initialLoading = false,
  insightsLoading = false,
  onUpgrade,
  llmIntelligence = null,
  currentLlmTier = null,
  onLlmTierChange,
  allowLockedIntelligenceSelection = false,
  llmTierSaving = false,
  llmTierError = null,
  onOpenTaskPacks,
  spawnIntentLoading = false,
  templateRecommendations = [],
  templateRecommendationSubmittingId = null,
  onTemplateRecommendationCreate,
  composerDisabled = false,
  composerDisabledReason = null,
  composerError = null,
  composerErrorShowUpgrade = false,
  showSubscriptionExpiredPanel = false,
  onSkipPlanning,
  maxAttachmentBytes = null,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
  nativeIntegrationsUrl = null,
  pendingActionRequests = [],
  onRespondHumanInputRequest,
  onDismissHumanInputRequest,
  onResolveSpawnRequest,
  onFulfillRequestedSecrets,
  onRemoveRequestedSecrets,
  onOpenAgentSecrets,
  onOpenAgentSecretRequests,
  onOpenAgentEmailSettings,
  onOpenAgentFiles,
  onResolveContactRequests,
  onViewAllContactRequests,
}: AgentChatLayoutProps) {
  const timelineRenderEvents = displayEvents ?? (events as SimplifiedTimelineItem[])

  const dispatch = useAppDispatch()
  const sidebarMode = useAppSelector(selectImmersiveSidebarMode)
  const sidebarModeRef = useRef(sidebarMode)
  useEffect(() => {
    sidebarModeRef.current = sidebarMode
  }, [sidebarMode])
  const setSidebarMode = useCallback((nextMode: AgentChatSidebarMode | ((mode: AgentChatSidebarMode) => AgentChatSidebarMode)) => {
    const resolvedMode = typeof nextMode === 'function' ? nextMode(sidebarModeRef.current) : nextMode
    if (resolvedMode !== sidebarModeRef.current) {
      dispatch(immersiveShellActions.setSidebarMode(resolvedMode))
    }
  }, [dispatch])
  const preEmbeddedSidebarModeRef = useRef<'collapsed' | 'list' | 'gallery' | null>(null)
  const {
    currentPlan: subscriptionPlan,
    isLoading: subscriptionLoading,
    isUpgradeModalOpen,
    upgradeModalSource,
    upgradeModalDismissible,
    isProprietaryMode,
    ctaPickAPlan,
    personalSignupPreviewAvailable,
    trialDaysByPlan,
    trialEligible,
  } = useAppSelector(selectSubscriptionState)
  const maxTrialDays = Math.max(trialDaysByPlan.startup, trialDaysByPlan.scale)
  const useTrialUpgradeCopy = (
    trialEligible
    && maxTrialDays > 0
    && (upgradeModalSource === 'trial_onboarding' || subscriptionPlan === 'free')
  )
  const sidebarConfig = sidebar ?? {}
  const {
    settings: sidebarSettingsConfig,
    insightsPanelExpandedPreference = null,
    insightsPanelPreferenceHydrated = true,
    onInsightsPanelExpandedPreferenceChange,
    suggestionsPreferenceHydrated = true,
    ...chatSidebarProps
  } = sidebarConfig
  const {
    galleryShellPage = 'agents',
    showEmbeddedSettings = false,
    onGalleryShellPageChange,
    onBackFromEmbeddedSettings,
  } = chatSidebarProps
  const effectiveGalleryShellPage = galleryShellPage ?? 'agents'
  const currentContext = sidebarSettingsConfig?.context ?? null
  const sidebarBillingUrl = sidebarSettingsConfig?.billingUrl ?? null
  const onOpenBilling = sidebarSettingsConfig?.onOpenBilling ?? undefined
  const sidebarUsageUrl = sidebarSettingsConfig?.usageUrl ?? '/app/usage'
  const onOpenUsage = sidebarSettingsConfig?.onOpenUsage ?? undefined
  const sidebarApiKeysUrl = sidebarSettingsConfig?.apiKeysUrl ?? '/app/api-keys'
  const onOpenApiKeys = sidebarSettingsConfig?.onOpenApiKeys ?? undefined
  const sidebarProfileUrl = sidebarSettingsConfig?.profileUrl ?? '/app/profile'
  const onOpenProfile = sidebarSettingsConfig?.onOpenProfile ?? undefined
  const sidebarOrganizationUrl = sidebarSettingsConfig?.organizationUrl ?? null
  const onOpenOrganization = sidebarSettingsConfig?.onOpenOrganization ?? undefined
  const sidebarSecretsUrl = sidebarSettingsConfig?.secretsUrl ?? '/app/secrets'
  const onOpenSecrets = sidebarSettingsConfig?.onOpenSecrets ?? undefined
  const sidebarIntegrationsUrl = sidebarSettingsConfig?.integrationsUrl ?? '/app/integrations'
  const onOpenIntegrations = sidebarSettingsConfig?.onOpenIntegrations ?? undefined
  const onOpenHelp = sidebarSettingsConfig?.onOpenHelp ?? undefined
  const sidebarTodayCreditsUsed = sidebarSettingsConfig?.todayCreditsUsed ?? null
  const sidebarCreditsResetOn = sidebarSettingsConfig?.creditsResetOn ?? null
  const sidebarNotificationsEnabled = sidebarSettingsConfig?.notificationsEnabled ?? true
  const sidebarNotificationStatus = sidebarSettingsConfig?.notificationStatus ?? 'off'
  const onSidebarNotificationsEnabledChange = sidebarSettingsConfig?.onNotificationsEnabledChange
  const sidebarSuggestionsEnabled = sidebarSettingsConfig?.suggestionsEnabled ?? true
  const onSidebarSuggestionsEnabledChange = sidebarSettingsConfig?.onSuggestionsEnabledChange
  const runtimeSession = useAppSelector(selectActiveChatSession)
  const shellViewer = useAppSelector(selectImmersiveShellViewer)
  const activeAgentId = agentId ?? null
  const runtimeAgentName = runtimeSession.identity.agentName
  const runtimeAgentIsOrgOwned = runtimeSession.identity.agentIsOrgOwned
  const runtimeCanManageAgent = runtimeSession.identity.canManageAgent
  const runtimeIsCollaborator = runtimeSession.identity.isCollaborator
  const runtimeProcessingActive = runtimeSession.processing.processingActive
  const runtimeAwaitingResponse = runtimeSession.processing.awaitingResponse
  const runtimeProcessingWebTasks = runtimeSession.processing.processingWebTasks
  const runtimeNextScheduledAt = runtimeSession.processing.nextScheduledAt
    ?? runtimeSession.identity.agentNextScheduledAt
  const runtimeStopProcessingRequested = runtimeSession.processing.stopProcessingRequested
  const runtimeSkipPlanningBusy = runtimeSession.processing.skipPlanningBusy
  const runtimeStreaming = runtimeSession.stream.streaming
  const runtimeAutoScrollPinned = runtimeSession.timelineUi.autoScrollPinned
  const runtimeHasUnseenActivity = runtimeSession.timelineUi.hasUnseenActivity
  const runtimeSignupPreviewState = runtimeSession.identity.signupPreviewState
  const runtimePlanningState = runtimeSession.identity.planningState
  const runtimeInsights = useMemo(
    () => runtimeSession.insights.insightIds
      .map((id) => runtimeSession.insights.insightsById[id])
      .filter(Boolean),
    [runtimeSession.insights.insightIds, runtimeSession.insights.insightsById],
  )
  const runtimeDismissedInsightIds = useMemo(
    () => new Set(Object.keys(runtimeSession.insights.dismissedInsightIds)),
    [runtimeSession.insights.dismissedInsightIds],
  )
  const processingActive = runtimeProcessingActive
  const awaitingResponse = runtimeAwaitingResponse
  const processingWebTasks = runtimeProcessingWebTasks
  const nextScheduledAt = runtimeNextScheduledAt
  const stopProcessingRequested = runtimeStopProcessingRequested
  const skipPlanningBusy = runtimeSkipPlanningBusy
  const streaming = runtimeStreaming
  const autoScrollPinned = runtimeAutoScrollPinned
  const hasUnseenActivity = runtimeHasUnseenActivity
  const planningState = runtimePlanningState
  const storeSignupPreviewState = runtimeSignupPreviewState
  const agentName = runtimeAgentName ?? bannerAgentName
  const agentIsOrgOwned = runtimeAgentIsOrgOwned
  const canManageAgent = runtimeCanManageAgent
  const isCollaborator = runtimeIsCollaborator
  const viewerEmail = shellViewer.email
  const hasAgentReply = useMemo(() => hasAgentResponse(timelineRenderEvents), [timelineRenderEvents])
  const signupPreviewState = useMemo<SignupPreviewState>(() => {
    if (
      storeSignupPreviewState === 'awaiting_first_reply_pause'
      && !initialLoading
      && !processingActive
      && (!awaitingResponse || hasAgentReply)
    ) {
      return 'awaiting_signup_completion'
    }
    return storeSignupPreviewState
  }, [awaitingResponse, hasAgentReply, initialLoading, processingActive, storeSignupPreviewState])
  const availableInsights = useMemo(() => {
    return runtimeInsights.filter((insight) => !runtimeDismissedInsightIds.has(insight.insightId))
  }, [runtimeDismissedInsightIds, runtimeInsights])
  const effectiveInsightsLoading = insightsLoading && availableInsights.length === 0
  const useContinuationUpgradeTitle = (
    ctaPickAPlan
    && isContinuationUpgradeModalSource(upgradeModalSource)
  )
  const upgradeTitle = useContinuationUpgradeTitle
    ? 'Finish what you just started'
    : useTrialUpgradeCopy
      ? `Start ${maxTrialDays}-day Free Trial`
    : 'Upgrade your plan'
  const upgradeSubtitle = useTrialUpgradeCopy ? 'Choose your plan to continue' : 'Choose the plan that fits your needs'
  const isMobileViewport = useIsMobile()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [addonsMode, setAddonsMode] = useState<'contacts' | 'tasks' | null>(null)
  const [contactCapDismissed, setContactCapDismissed] = useState(false)
  const [taskCreditsDismissed, setTaskCreditsDismissed] = useState(false)
  const [highPriorityDismissed, setHighPriorityDismissed] = useState(false)
  const [quickIncreaseBusy, setQuickIncreaseBusy] = useState(false)
  const [planSheetOpen, setPlanSheetOpen] = useState(false)
  const [defaultPlanPanelMode, setDefaultPlanPanelMode] = useState<PlanPanelMode>('hidden')
  const [agentPlanPanelModes, setAgentPlanPanelModes] = useState<Record<string, PlanPanelMode>>({})
  const [planPreviewSnapshot, setPlanPreviewSnapshot] = useState<PlanSnapshot | null>(null)
  const [planPreviewExiting, setPlanPreviewExiting] = useState(false)
  const [planHoverPreviewVisible, setPlanHoverPreviewVisible] = useState(false)
  const [planHoverPreviewExiting, setPlanHoverPreviewExiting] = useState(false)
  const [reportMessage, setReportMessage] = useState<AgentMessage | null>(null)
  const [reportSubmitting, setReportSubmitting] = useState(false)
  const [reportError, setReportError] = useState<string | null>(null)
  const planPanelMode = agentId ? agentPlanPanelModes[agentId] ?? 'hidden' : defaultPlanPanelMode
  const hasStoredPlanPanelMode = agentId
    ? Object.prototype.hasOwnProperty.call(agentPlanPanelModes, agentId)
    : defaultPlanPanelMode !== 'hidden'
  const showGalleryShellPanel = effectiveGalleryShellPage !== 'agents'
  const showPlanInterface = sidebarMode !== 'gallery'
  const previousPlanStateRef = useRef<{ total: number; active: boolean } | null>(null)
  const previousPlanSnapshotRef = useRef<PlanSnapshot | null>(null)
  const planPreviewTimeoutRef = useRef<number | null>(null)
  const planPreviewExitTimeoutRef = useRef<number | null>(null)
  const planHoverExitTimeoutRef = useRef<number | null>(null)
  const suppressPlanHoverPreviewRef = useRef(false)
  const clearPlanPreviewTimers = useCallback(() => {
    if (planPreviewTimeoutRef.current !== null) {
      window.clearTimeout(planPreviewTimeoutRef.current)
      planPreviewTimeoutRef.current = null
    }
    if (planPreviewExitTimeoutRef.current !== null) {
      window.clearTimeout(planPreviewExitTimeoutRef.current)
      planPreviewExitTimeoutRef.current = null
    }
    if (planHoverExitTimeoutRef.current !== null) {
      window.clearTimeout(planHoverExitTimeoutRef.current)
      planHoverExitTimeoutRef.current = null
    }
  }, [])
  const resetPlanPreviewState = useCallback((options: { closeSheet?: boolean } = {}) => {
    if (options.closeSheet) {
      setPlanSheetOpen(false)
    }
    setPlanPreviewSnapshot(null)
    setPlanPreviewExiting(false)
    setPlanHoverPreviewVisible(false)
    setPlanHoverPreviewExiting(false)
    clearPlanPreviewTimers()
    suppressPlanHoverPreviewRef.current = false
  }, [clearPlanPreviewTimers])
  const contactCapLimitReachedRef = useRef<boolean | null>(null)
  const taskCreditsStorageKeyRef = useRef<string | null>(null)
  const addonsOpen = addonsMode !== null
  const contactCapDismissKey = useMemo(() => {
    return agentId ? `agent-chat-contact-cap-dismissed:${agentId}` : null
  }, [agentId])
  const taskCreditsStorageKey = useMemo(() => {
    if (!taskCreditsDismissKey || !taskCreditsWarningVariant) {
      return null
    }
    return `agent-chat-task-credits-dismissed:${taskCreditsDismissKey}:${taskCreditsWarningVariant}`
  }, [taskCreditsDismissKey, taskCreditsWarningVariant])
  const highPriorityBannerId = highPriorityBanner?.id ?? null
  const highPriorityBannerDismissible = Boolean(highPriorityBanner?.dismissible)
  const highPriorityDismissKey = useMemo(() => {
    if (!agentId || !highPriorityBannerDismissible || !highPriorityBannerId) {
      return null
    }
    return `agent-chat-high-priority-dismissed:${agentId}:${highPriorityBannerId}`
  }, [agentId, highPriorityBannerDismissible, highPriorityBannerId])

  const handleSidebarModeChange = useCallback((mode: 'collapsed' | 'list' | 'gallery') => {
    if (showGalleryShellPanel && mode !== 'gallery') {
      preEmbeddedSidebarModeRef.current = null
      setSidebarMode(mode)
      onGalleryShellPageChange?.('agents')
      return
    }
    if (showEmbeddedSettings && mode !== 'gallery') {
      preEmbeddedSidebarModeRef.current = null
      setSidebarMode(mode)
      onBackFromEmbeddedSettings?.()
      return
    }
    setSidebarMode(mode)
  }, [onBackFromEmbeddedSettings, onGalleryShellPageChange, setSidebarMode, showEmbeddedSettings, showGalleryShellPanel])

  const handleSettingsOpen = useCallback(() => {
    setSettingsOpen(true)
    onRefreshDailyCredits?.()
  }, [onRefreshDailyCredits])

  const handleSettingsClose = useCallback(() => {
    setSettingsOpen(false)
  }, [])

  const handleOpenFullSettingsFromQuickPanel = useCallback(() => {
    setSettingsOpen(false)
    onOpenFullSettings?.()
  }, [onOpenFullSettings])

  const handlePurchaseSeats = useCallback(() => {
    if (onOpenBilling) {
      onOpenBilling()
      return
    }
    if (typeof window !== 'undefined') {
      window.location.assign(sidebarBillingUrl ?? '/app/billing')
    }
  }, [onOpenBilling, sidebarBillingUrl])

  const handleAddonsOpen = useCallback((mode: 'contacts' | 'tasks') => {
    setAddonsMode(mode)
    onRefreshAddons?.()
  }, [onRefreshAddons])

  const handleAddonsClose = useCallback(() => {
    setAddonsMode(null)
  }, [])

  const handleReportMessage = useCallback((message: AgentMessage) => {
    setReportMessage(message)
    setReportError(null)
  }, [])

  const handleReportDialogClose = useCallback(() => {
    if (reportSubmitting) {
      return
    }
    setReportMessage(null)
    setReportError(null)
  }, [reportSubmitting])

  const handleReportSubmit = useCallback(async (comment: string) => {
    if (!agentId || !reportMessage) {
      return
    }
    setReportSubmitting(true)
    setReportError(null)
    try {
      await reportAgentMessageIssue(agentId, reportMessage.id, comment)
      setReportMessage(null)
    } catch {
      setReportError('Unable to submit the report. Please try again.')
    } finally {
      setReportSubmitting(false)
    }
  }, [agentId, reportMessage])

  useEffect(() => {
    if (showEmbeddedSettings || showGalleryShellPanel) {
      setSidebarMode((mode) => {
        if (mode !== 'gallery' && preEmbeddedSidebarModeRef.current === null) {
          preEmbeddedSidebarModeRef.current = mode
        }
        return 'gallery'
      })
      return
    }
    setSidebarMode((mode) => {
      const restoredMode = preEmbeddedSidebarModeRef.current
      preEmbeddedSidebarModeRef.current = null
      if (restoredMode && mode === 'gallery') {
        return restoredMode
      }
      return mode
    })
  }, [setSidebarMode, showEmbeddedSettings, showGalleryShellPanel])

  useEffect(() => {
    if (!isUpgradeModalOpen) {
      return
    }
    if (isCollaborator) {
      dispatch(subscriptionActions.closeUpgradeModal())
      return
    }
    if (subscriptionLoading) {
      return
    }
    if (!isProprietaryMode) {
      dispatch(subscriptionActions.closeUpgradeModal())
    }
  }, [dispatch, isCollaborator, isProprietaryMode, isUpgradeModalOpen, subscriptionLoading])

  const handleUpgradeModalDismiss = useCallback(() => {
    if (!upgradeModalDismissible) {
      return
    }
    track(AnalyticsEvent.UPGRADE_MODAL_DISMISSED, {
      currentPlan: subscriptionPlan,
      source: upgradeModalSource ?? 'unknown',
    })
    dispatch(subscriptionActions.closeUpgradeModal())
  }, [dispatch, subscriptionPlan, upgradeModalDismissible, upgradeModalSource])

  const handleUpgradeSelection = useCallback((plan: PlanTier) => {
    onUpgrade?.(plan)
    dispatch(subscriptionActions.closeUpgradeModal())
  }, [dispatch, onUpgrade])

  const resolvedOpenTaskPacks = useMemo(
    () =>
      onOpenTaskPacks ??
      (taskPackCanManageBilling
        ? () => handleAddonsOpen('tasks')
        : undefined),
    [handleAddonsOpen, onOpenTaskPacks, taskPackCanManageBilling],
  )

  useEffect(() => {
    setSettingsOpen(false)
    setAddonsMode(null)
    setContactCapDismissed(false)
  }, [agentId])

  useEffect(() => {
    if (!contactCapDismissKey || typeof window === 'undefined') {
      return
    }
    const stored = window.localStorage.getItem(contactCapDismissKey)
    setContactCapDismissed(stored === 'true')
  }, [contactCapDismissKey])

  useEffect(() => {
    if (!taskCreditsStorageKey || typeof window === 'undefined') {
      setTaskCreditsDismissed(false)
      return
    }
    const stored = window.localStorage.getItem(taskCreditsStorageKey)
    setTaskCreditsDismissed(stored === 'true')
  }, [taskCreditsStorageKey])

  useEffect(() => {
    if (!contactCapDismissKey || typeof window === 'undefined') {
      return
    }
    const currentLimitReached = contactCapStatus?.limitReached
    const previousLimitReached = contactCapLimitReachedRef.current
    contactCapLimitReachedRef.current = currentLimitReached ?? null
    if (previousLimitReached && currentLimitReached === false) {
      window.localStorage.removeItem(contactCapDismissKey)
      setContactCapDismissed(false)
    }
  }, [contactCapDismissKey, contactCapStatus?.limitReached])

  useEffect(() => {
    if (typeof window === 'undefined') {
      taskCreditsStorageKeyRef.current = taskCreditsStorageKey
      if (!showTaskCreditsWarning) {
        setTaskCreditsDismissed(false)
      }
      return
    }
    if (!showTaskCreditsWarning) {
      if (taskCreditsStorageKeyRef.current) {
        window.localStorage.removeItem(taskCreditsStorageKeyRef.current)
      }
      taskCreditsStorageKeyRef.current = taskCreditsStorageKey
      setTaskCreditsDismissed(false)
      return
    }
    taskCreditsStorageKeyRef.current = taskCreditsStorageKey
  }, [showTaskCreditsWarning, taskCreditsStorageKey])

  useEffect(() => {
    if (typeof window === 'undefined' || !highPriorityDismissKey) {
      setHighPriorityDismissed(false)
      return
    }
    const stored = window.localStorage.getItem(highPriorityDismissKey)
    setHighPriorityDismissed(stored === 'true')
  }, [highPriorityDismissKey])

  // Track upsell message visibility with sessionStorage deduplication
  useEffect(() => {
    if (typeof window === 'undefined' || !agentId) return
    const showHardLimit = Boolean(
      (dailyCreditsStatus?.hardLimitReached || dailyCreditsStatus?.hardLimitBlocked) && onUpdateDailyCredits,
    )
    if (!showHardLimit) return
    const storageKey = `upsell-tracked:${agentId}:daily_hard_limit`
    if (window.sessionStorage.getItem(storageKey)) return
    window.sessionStorage.setItem(storageKey, 'true')
    track(AnalyticsEvent.UPSELL_MESSAGE_SHOWN, {
      agent_id: agentId,
      message_type: 'daily_hard_limit',
      medium: 'web_card',
      recipient_type: 'owner',
      upsell_shown: hardLimitShowUpsell,
    })
  }, [agentId, dailyCreditsStatus?.hardLimitReached, dailyCreditsStatus?.hardLimitBlocked, onUpdateDailyCredits, hardLimitShowUpsell])

  useEffect(() => {
    if (typeof window === 'undefined' || !agentId) return
    const showTaskCredits = Boolean(showTaskCreditsWarning && !showPurchaseSeatsPrompt && !taskCreditsDismissed)
    if (!showTaskCredits) return
    const messageType = taskCreditsWarningVariant === 'out' ? 'task_credits_exhausted' : 'task_credits_low'
    const storageKey = `upsell-tracked:${agentId}:${messageType}`
    if (window.sessionStorage.getItem(storageKey)) return
    window.sessionStorage.setItem(storageKey, 'true')
    track(AnalyticsEvent.UPSELL_MESSAGE_SHOWN, {
      agent_id: agentId,
      message_type: messageType,
      medium: 'web_card',
      recipient_type: 'owner',
      upsell_shown: showTaskCreditsUpgrade,
    })
  }, [agentId, showPurchaseSeatsPrompt, showTaskCreditsWarning, taskCreditsDismissed, taskCreditsWarningVariant, showTaskCreditsUpgrade])

  useEffect(() => {
    if (typeof window === 'undefined' || !agentId) return
    const showContactCap = Boolean(contactCapStatus?.limitReached && !contactCapDismissed)
    if (!showContactCap) return
    const storageKey = `upsell-tracked:${agentId}:contact_cap_reached`
    if (window.sessionStorage.getItem(storageKey)) return
    window.sessionStorage.setItem(storageKey, 'true')
    track(AnalyticsEvent.UPSELL_MESSAGE_SHOWN, {
      agent_id: agentId,
      message_type: 'contact_cap_reached',
      medium: 'web_card',
      recipient_type: 'owner',
      upsell_shown: contactPackShowUpgrade,
    })
  }, [agentId, contactCapStatus?.limitReached, contactCapDismissed, contactPackShowUpgrade])

  const isStreaming = Boolean(streaming && !streaming.done)
  const isWorkingNow = Boolean(processingActive || isStreaming || awaitingResponse)
  const hasStreamingContent = Boolean(streaming?.content?.trim())
  // Un-suppress the static thinking entry once streaming completes so it appears in its chronological position
  const suppressedThinkingCursor = streaming && !streaming.done ? streaming.cursor ?? null : null
  const showStreamingSlot = hasStreamingContent && isStreaming
  const showStreamingThinking = Boolean(
    isStreaming && streaming?.reasoning?.trim() && !hasStreamingContent && !hasMoreNewer,
  )

  // Show progress bar whenever processing is active.
  // Keep it mounted but hide visually while actively streaming message content or when newer messages are waiting
  const isActivelyStreamingContent = hasStreamingContent && isStreaming
  const showTypingIndicator = Boolean(awaitingResponse || processingActive || isStreaming)
  const hideTypingIndicator = isActivelyStreamingContent || hasMoreNewer
  const typingStatusText = stopProcessingRequested
    ? 'Stopping...'
    : planningState === 'planning'
      ? 'Planning...'
      : deriveTypingStatusText({ streaming: streaming ?? null, processingWebTasks, awaitingResponse })

  const showProcessingIndicator = Boolean((processingActive || isStreaming || awaitingResponse) && !hasMoreNewer)
  const showScheduledResumeEvent = Boolean(
    !initialLoading
    && !processingActive
    && !awaitingResponse
    && !isStreaming
    && !hasMoreNewer
    && nextScheduledAt,
  )
  const showTemplateRecommendations = Boolean(
    !activeAgentId
    && timelineRenderEvents.length === 0
    && !initialLoading
    && !spawnIntentLoading
    && templateRecommendations.length > 0
  )
  const starterPromptCount = typeof window !== 'undefined' && window.innerWidth < 768 ? 2 : 3
  const {
    starterPrompts,
    starterPromptsLoading,
    starterPromptSubmitting,
    handleStarterPromptDismiss,
    handleStarterPromptSelect,
  } = useStarterPrompts({
    agentId,
    enabled: suggestionsPreferenceHydrated && sidebarSuggestionsEnabled,
    events,
    initialLoading,
    spawnIntentLoading,
    isWorkingNow,
    onSendMessage,
    promptCount: starterPromptCount,
    hasPendingHumanInput: pendingActionRequests.length > 0,
  })
  const hasTimelineEvents = timelineRenderEvents.length > 0
  const showJumpButton = !initialLoading
    && hasTimelineEvents
    && (
      hasMoreNewer
      || (!autoScrollPinned && (hasUnseenActivity || !isNearBottom))
    )

  const showBanner = Boolean(agentName)
  const showHardLimitCallout = Boolean(
    (dailyCreditsStatus?.hardLimitReached || dailyCreditsStatus?.hardLimitBlocked) && onUpdateDailyCredits,
  )
  const quickIncreaseTarget = useMemo(() => {
    if (!dailyCredits || !onUpdateDailyCredits || dailyCredits.unlimited) {
      return null
    }
    if (!Number.isFinite(dailyCredits.limit ?? NaN) || !Number.isFinite(dailyCredits.sliderLimitMax)) {
      return null
    }

    const currentLimit = Math.round(dailyCredits.limit as number)
    const maxLimit = Math.round(dailyCredits.sliderLimitMax)
    const step = Number.isFinite(dailyCredits.sliderStep) && dailyCredits.sliderStep > 0
      ? Math.round(dailyCredits.sliderStep)
      : 1
    const standardLimit = Number.isFinite(dailyCredits.standardSliderLimit)
      ? Math.round(dailyCredits.standardSliderLimit)
      : currentLimit + step
    const target = Math.min(maxLimit, Math.max(currentLimit + step, standardLimit))

    if (target <= currentLimit) {
      return null
    }
    return target
  }, [dailyCredits, onUpdateDailyCredits])
  const quickIncreaseLabel = useMemo(() => {
    if (quickIncreaseTarget === null) {
      return null
    }
    return `Increase to ${quickIncreaseTarget}/day`
  }, [quickIncreaseTarget])
  const handleQuickIncreaseLimit = useCallback(async () => {
    if (!onUpdateDailyCredits || quickIncreaseTarget === null || quickIncreaseBusy) {
      return
    }
    setQuickIncreaseBusy(true)
    try {
      await onUpdateDailyCredits({ daily_credit_limit: quickIncreaseTarget })
      onRefreshDailyCredits?.()
    } finally {
      setQuickIncreaseBusy(false)
    }
  }, [onUpdateDailyCredits, quickIncreaseTarget, quickIncreaseBusy, onRefreshDailyCredits])
  const showContactCapCallout = Boolean(contactCapStatus?.limitReached && !contactCapDismissed)
  const showNoSeatsCallout = Boolean(showPurchaseSeatsPrompt)
  const showTaskCreditsCallout = Boolean(showTaskCreditsWarning && !showNoSeatsCallout && !taskCreditsDismissed)
  const showHighPriorityBanner = Boolean(
    highPriorityBanner && (!highPriorityBannerDismissible || !highPriorityDismissed),
  )

  const handleContactCapDismiss = useCallback(() => {
    if (agentId) {
      track(AnalyticsEvent.UPSELL_MESSAGE_DISMISSED, {
        agent_id: agentId,
        message_type: 'contact_cap_reached',
        medium: 'web_card',
        recipient_type: 'owner',
      })
    }
    if (!contactCapDismissKey || typeof window === 'undefined') {
      setContactCapDismissed(true)
      return
    }
    window.localStorage.setItem(contactCapDismissKey, 'true')
    setContactCapDismissed(true)
  }, [contactCapDismissKey, agentId])
  const handleTaskCreditsDismiss = useCallback(() => {
    if (agentId) {
      const messageType = taskCreditsWarningVariant === 'out' ? 'task_credits_exhausted' : 'task_credits_low'
      track(AnalyticsEvent.UPSELL_MESSAGE_DISMISSED, {
        agent_id: agentId,
        message_type: messageType,
        medium: 'web_card',
        recipient_type: 'owner',
      })
    }
    if (!taskCreditsStorageKey || typeof window === 'undefined') {
      setTaskCreditsDismissed(true)
      return
    }
    window.localStorage.setItem(taskCreditsStorageKey, 'true')
    setTaskCreditsDismissed(true)
  }, [taskCreditsStorageKey, agentId, taskCreditsWarningVariant])
  const handleHighPriorityDismiss = useCallback(() => {
    if (!highPriorityBannerDismissible || !highPriorityDismissKey || typeof window === 'undefined') {
      setHighPriorityDismissed(true)
      return
    }
    window.localStorage.setItem(highPriorityDismissKey, 'true')
    setHighPriorityDismissed(true)
  }, [highPriorityBannerDismissible, highPriorityDismissKey])
  const previewActionsDisabled = signupPreviewState !== 'none'
  const previewActionsDisabledReason = previewActionsDisabled
    ? 'Finish signup to manage settings and collaborate.'
    : null
  const effectiveShowSignupPreviewPanel = (
    Boolean(activeAgentId)
    && !agentIsOrgOwned
    && personalSignupPreviewAvailable
    && signupPreviewState !== 'none'
    && planningState !== 'planning'
  )
  const effectiveShowSubscriptionExpiredPanel = showSubscriptionExpiredPanel && planningState !== 'planning'
  const composerUnavailable = spawnIntentLoading || effectiveShowSignupPreviewPanel || effectiveShowSubscriptionExpiredPanel
  const showComposerUnavailableSkipPlanning = composerUnavailable && planningState === 'planning'
  const skipPlanningDisabled = !canManageAgent || !onSkipPlanning || skipPlanningBusy
  const canOpenQuickSettings = Boolean(onUpdateDailyCredits || (llmIntelligence && onLlmTierChange))

  const handleMessageLinkClick = useCallback((href: string) => {
    const linkedShellPage = getAppMessageLinkShellPage(href)
    if (linkedShellPage) {
      const openShellPage = (() => {
        switch (linkedShellPage) {
          case 'billing':
            return onOpenBilling
          case 'profile':
            return onOpenProfile
          case 'organization':
            return onOpenOrganization
          case 'secrets':
            return onOpenSecrets
          case 'usage':
            return onOpenUsage
          case 'integrations':
            return onOpenIntegrations
          case 'api-keys':
            return onOpenApiKeys
        }
      })()
      if (!openShellPage) {
        return false
      }
      openShellPage()
      return true
    }

    if (!agentId) {
      return false
    }
    const linkedSubview = getCurrentAgentMessageLinkSubview(href, agentId)
    if (!linkedSubview) {
      return false
    }
    if (linkedSubview === 'contact-requests') {
      if (!onViewAllContactRequests) {
        return false
      }
      onViewAllContactRequests()
      return true
    }
    if (linkedSubview === 'secrets') {
      if (!onOpenAgentSecrets) {
        return false
      }
      onOpenAgentSecrets()
      return true
    }
    if (linkedSubview === 'secret-requests') {
      if (!onOpenAgentSecretRequests) {
        return false
      }
      onOpenAgentSecretRequests()
      return true
    }
    if (linkedSubview === 'email') {
      if (!onOpenAgentEmailSettings) {
        return false
      }
      onOpenAgentEmailSettings()
      return true
    }
    if (linkedSubview === 'files') {
      if (!onOpenAgentFiles) {
        return false
      }
      onOpenAgentFiles()
      return true
    }
    if (previewActionsDisabled && onBlockedSettingsClick) {
      onBlockedSettingsClick('banner_desktop')
      return true
    }
    if (onOpenFullSettings) {
      onOpenFullSettings()
      return true
    }
    if (!canOpenQuickSettings) {
      return false
    }
    handleSettingsOpen()
    return true
  }, [
    agentId,
    canOpenQuickSettings,
    handleSettingsOpen,
    onBlockedSettingsClick,
    onOpenApiKeys,
    onOpenAgentEmailSettings,
    onOpenAgentFiles,
    onOpenAgentSecretRequests,
    onOpenAgentSecrets,
    onOpenBilling,
    onOpenFullSettings,
    onOpenIntegrations,
    onOpenOrganization,
    onOpenProfile,
    onOpenSecrets,
    onOpenUsage,
    onViewAllContactRequests,
    previewActionsDisabled,
  ])

  const setCurrentPlanPanelMode = useCallback((resolveMode: (mode: PlanPanelMode) => PlanPanelMode) => {
    if (agentId) {
      setAgentPlanPanelModes((modes) => {
        const currentMode = modes[agentId] ?? 'hidden'
        const nextMode = resolveMode(currentMode)
        if (nextMode === currentMode) {
          return modes
        }
        return { ...modes, [agentId]: nextMode }
      })
      return
    }
    setDefaultPlanPanelMode((mode) => {
      const nextMode = resolveMode(mode)
      return nextMode === mode ? mode : nextMode
    })
  }, [agentId])

  const handleOpenPlan = useCallback(() => {
    if (!showPlanInterface) {
      return
    }
    if (typeof window !== 'undefined' && window.innerWidth < 1024) {
      setPlanSheetOpen(true)
      return
    }
    suppressPlanHoverPreviewRef.current = planPanelMode === 'docked'
    setCurrentPlanPanelMode((mode) => (mode === 'docked' ? 'hidden' : 'docked'))
    setPlanPreviewSnapshot(null)
    setPlanPreviewExiting(false)
    setPlanHoverPreviewVisible(false)
    setPlanHoverPreviewExiting(false)
  }, [planPanelMode, setCurrentPlanPanelMode, showPlanInterface])

  const displayPlanSnapshot = useMemo(
    () => addInferredPlanFiles(planSnapshot, events),
    [events, planSnapshot],
  )

  const handlePlanHoverChange = useCallback((hovered: boolean) => {
    const wasSuppressed = suppressPlanHoverPreviewRef.current
    if (!hovered) {
      suppressPlanHoverPreviewRef.current = false
      if (wasSuppressed || (!planHoverPreviewVisible && !planHoverPreviewExiting)) {
        return
      }
    }
    if (!showPlanInterface || planPanelMode !== 'hidden') {
      return
    }
    if (typeof window !== 'undefined' && window.innerWidth < 1024) {
      return
    }
    const total = (displayPlanSnapshot?.todoCount ?? 0) + (displayPlanSnapshot?.doingCount ?? 0) + (displayPlanSnapshot?.doneCount ?? 0)
    if (total === 0) {
      return
    }

    if (planHoverExitTimeoutRef.current !== null) {
      window.clearTimeout(planHoverExitTimeoutRef.current)
      planHoverExitTimeoutRef.current = null
    }

    if (hovered) {
      if (wasSuppressed) {
        return
      }
      if (planPreviewExitTimeoutRef.current !== null) {
        window.clearTimeout(planPreviewExitTimeoutRef.current)
        planPreviewExitTimeoutRef.current = null
      }
      setPlanPreviewExiting(false)
      setPlanHoverPreviewVisible(true)
      setPlanHoverPreviewExiting(false)
      return
    }

    setPlanHoverPreviewVisible(false)
    setPlanHoverPreviewExiting(true)
    planHoverExitTimeoutRef.current = window.setTimeout(() => {
      setPlanHoverPreviewExiting(false)
      planHoverExitTimeoutRef.current = null
    }, 180)
  }, [displayPlanSnapshot, planHoverPreviewExiting, planHoverPreviewVisible, planPanelMode, showPlanInterface])

  const handleFloatingPlanOpen = useCallback(() => {
    if (!showPlanInterface || planPanelMode !== 'hidden') {
      return
    }
    setCurrentPlanPanelMode(() => 'docked')
    resetPlanPreviewState()
  }, [planPanelMode, resetPlanPreviewState, setCurrentPlanPanelMode, showPlanInterface])

  const handleFloatingPlanClick = useCallback((event: MouseEvent<HTMLDivElement>) => {
    event.preventDefault()
    event.stopPropagation()
    handleFloatingPlanOpen()
  }, [handleFloatingPlanOpen])

  const handleFloatingPlanKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    handleFloatingPlanOpen()
  }, [handleFloatingPlanOpen])

  useEffect(() => {
    if (!showPlanInterface) {
      resetPlanPreviewState({ closeSheet: true })
    }
  }, [resetPlanPreviewState, showPlanInterface])

  useEffect(() => {
    resetPlanPreviewState({ closeSheet: true })
    previousPlanStateRef.current = null
    previousPlanSnapshotRef.current = null
  }, [agentId, resetPlanPreviewState])

  useEffect(() => {
    const total = (displayPlanSnapshot?.todoCount ?? 0) + (displayPlanSnapshot?.doingCount ?? 0) + (displayPlanSnapshot?.doneCount ?? 0)
    const active = (displayPlanSnapshot?.todoCount ?? 0) + (displayPlanSnapshot?.doingCount ?? 0) > 0
    const previous = previousPlanStateRef.current
    previousPlanStateRef.current = { total, active }

    const previousSnapshot = previousPlanSnapshotRef.current
    previousPlanSnapshotRef.current = displayPlanSnapshot ?? null

    if (!previous) {
      if (
        showPlanInterface
        && !hasStoredPlanPanelMode
        && (active || hasCompletedPlanDeliverables(displayPlanSnapshot))
      ) {
        setCurrentPlanPanelMode(() => 'docked')
      }
      return
    }

    if (previous.total === 0 && total > 0) {
      resetPlanPreviewState()
      if (active || hasCompletedPlanDeliverables(displayPlanSnapshot)) {
        setCurrentPlanPanelMode(() => 'docked')
      }
      return
    }

    if (!showPlanInterface || planPanelMode !== 'hidden') {
      return
    }

    const changedSnapshot = filterChangedPlanSnapshot(previousSnapshot, displayPlanSnapshot)
    if (!changedSnapshot) {
      return
    }

    setPlanPreviewSnapshot(changedSnapshot)
    setPlanPreviewExiting(false)
    if (planPreviewTimeoutRef.current !== null) {
      window.clearTimeout(planPreviewTimeoutRef.current)
    }
    if (planPreviewExitTimeoutRef.current !== null) {
      window.clearTimeout(planPreviewExitTimeoutRef.current)
      planPreviewExitTimeoutRef.current = null
    }
    planPreviewTimeoutRef.current = window.setTimeout(() => {
      setPlanPreviewExiting(true)
      planPreviewTimeoutRef.current = null
      planPreviewExitTimeoutRef.current = window.setTimeout(() => {
        setPlanPreviewSnapshot(null)
        setPlanPreviewExiting(false)
        planPreviewExitTimeoutRef.current = null
      }, 180)
    }, 5000)
  }, [displayPlanSnapshot, hasStoredPlanPanelMode, planPanelMode, resetPlanPreviewState, setCurrentPlanPanelMode, showPlanInterface])

  useEffect(() => {
    return () => {
      clearPlanPreviewTimers()
      suppressPlanHoverPreviewRef.current = false
    }
  }, [clearPlanPreviewTimers])

  const handlePlanMessageClick = useCallback((messageId: string) => {
    if (typeof document === 'undefined') {
      return
    }
    const escaped = typeof window !== 'undefined' && window.CSS?.escape ? window.CSS.escape(messageId) : messageId.replace(/"/g, '\\"')
    const target = document.querySelector<HTMLElement>(`[data-message-id="${escaped}"]`)
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    setPlanSheetOpen(false)
  }, [])

  const mainClassName = 'agent-chat-main'
  const sidebarSettings = useMemo(() => ({
    context: currentContext,
    viewerEmail: viewerEmail ?? null,
    isProprietaryMode,
    billingUrl: sidebarBillingUrl,
    usageUrl: sidebarUsageUrl,
    apiKeysUrl: sidebarApiKeysUrl,
    profileUrl: sidebarProfileUrl,
    organizationUrl: sidebarOrganizationUrl,
    secretsUrl: sidebarSecretsUrl,
    integrationsUrl: sidebarIntegrationsUrl,
    notificationsEnabled: sidebarNotificationsEnabled,
    notificationStatus: sidebarNotificationStatus,
    onNotificationsEnabledChange: onSidebarNotificationsEnabledChange,
    suggestionsEnabled: sidebarSuggestionsEnabled,
    onSuggestionsEnabledChange: onSidebarSuggestionsEnabledChange,
    onOpenBilling,
    onOpenUsage,
    onOpenApiKeys,
    onOpenProfile,
    onOpenOrganization,
    onOpenSecrets,
    onOpenIntegrations,
    onOpenHelp,
    taskCredits: taskQuota
      ? {
          usedToday: sidebarTodayCreditsUsed,
          remaining: taskQuota.available,
          resetOn: sidebarCreditsResetOn,
          unlimited: Boolean(taskQuota.total < 0 || taskQuota.available < 0),
        }
      : null,
  }), [
    currentContext,
    isProprietaryMode,
    onSidebarNotificationsEnabledChange,
    onSidebarSuggestionsEnabledChange,
    onOpenBilling,
    onOpenUsage,
    onOpenApiKeys,
    onOpenProfile,
    onOpenOrganization,
    onOpenSecrets,
    onOpenIntegrations,
    onOpenHelp,
    sidebarBillingUrl,
    sidebarUsageUrl,
    sidebarApiKeysUrl,
    sidebarProfileUrl,
    sidebarOrganizationUrl,
    sidebarSecretsUrl,
    sidebarIntegrationsUrl,
    sidebarCreditsResetOn,
    sidebarNotificationStatus,
    sidebarNotificationsEnabled,
    sidebarSuggestionsEnabled,
    sidebarTodayCreditsUsed,
    taskQuota,
    viewerEmail,
  ])
  const showHoverPlanPreview = planPanelMode === 'hidden' && (planHoverPreviewVisible || planHoverPreviewExiting)
  const renderedPlanSnapshot = planPanelMode === 'docked'
    ? displayPlanSnapshot
    : showHoverPlanPreview
      ? displayPlanSnapshot
      : planPreviewSnapshot
  const floatingPlanExiting = !planHoverPreviewVisible && (
    planPreviewExiting || (planHoverPreviewExiting && !planPreviewSnapshot)
  )
  const workspacePlanMode = !showPlanInterface
    ? 'hidden'
    : planPanelMode === 'docked'
      ? 'docked'
      : renderedPlanSnapshot
        ? 'floating'
        : 'hidden'
  const isFloatingPlanPreview = workspacePlanMode === 'floating'
  const isHoverPlanPreview = isFloatingPlanPreview && showHoverPlanPreview
  const showDesktopPlanFrame = showPlanInterface
  const showDesktopPlanPanel = workspacePlanMode !== 'hidden' && (
    planPanelMode === 'docked' || Boolean(renderedPlanSnapshot)
  )

  return (
    <>
      <ChatSidebar
        {...chatSidebarProps}
        desktopMode={sidebarMode}
        onDesktopModeChange={handleSidebarModeChange}
        settings={sidebarSettings}
      />
      {showBanner && (
        <AgentChatBanner
          agentNameOverride={bannerAgentName}
          planSnapshot={showPlanInterface ? displayPlanSnapshot : null}
          planPanelMode={planPanelMode}
          onPlanOpen={showPlanInterface ? handleOpenPlan : undefined}
          onPlanHoverChange={showPlanInterface ? handlePlanHoverChange : undefined}
          dailyCreditsStatus={dailyCreditsStatus}
          showPurchaseSeatsButton={showPurchaseSeatsPrompt}
          onPurchaseSeats={handlePurchaseSeats}
          onSettingsOpen={canOpenQuickSettings ? handleSettingsOpen : undefined}
          onIdentitySettingsOpen={onOpenFullSettings}
          settingsDisabled={previewActionsDisabled}
          settingsDisabledReason={previewActionsDisabledReason}
          onBlockedSettingsClick={onBlockedSettingsClick}
          onClose={onClose}
          onShare={onShare}
          shareDisabled={previewActionsDisabled}
          shareDisabledReason={previewActionsDisabledReason}
          onBlockedShareClick={onBlockedCollaborate}
          onPublicShare={onPublicShare}
          publicShareDisabled={previewActionsDisabled}
          publicShareDisabledReason={previewActionsDisabledReason}
          sidebarMode={sidebarMode}
          developerMode={developerMode}
          showDeveloperMode={showDeveloperMode}
          onDeveloperModeChange={onDeveloperModeChange}
          developerControls={developerControls}
        >
          {showHighPriorityBanner && highPriorityBanner ? (
            <HighPriorityBanner
              title={highPriorityBanner.title}
              message={highPriorityBanner.message}
              actionLabel={highPriorityBanner.actionLabel}
              actionHref={highPriorityBanner.actionHref}
              onAction={highPriorityBanner.onAction}
              dismissible={highPriorityBannerDismissible}
              tone={highPriorityBanner.tone}
              onDismiss={highPriorityBannerDismissible ? handleHighPriorityDismiss : undefined}
            />
          ) : null}
        </AgentChatBanner>
      )}
      <AgentChatSettingsPanel
        open={settingsOpen}
        onClose={handleSettingsClose}
        agentId={agentId}
        dailyCredits={dailyCredits}
        status={dailyCreditsStatus}
        loading={dailyCreditsLoading}
        error={dailyCreditsError}
        updating={dailyCreditsUpdating}
        onSave={onUpdateDailyCredits}
        llmIntelligence={llmIntelligence}
        currentLlmTier={currentLlmTier}
        onLlmTierChange={onLlmTierChange}
        llmTierSaving={llmTierSaving}
        llmTierError={llmTierError}
        canManageAgent={canManageAgent}
        context={currentContext}
        onOpenFullSettings={onOpenFullSettings ? handleOpenFullSettingsFromQuickPanel : undefined}
      />
      <AgentChatAddonsPanel
        open={addonsOpen}
        mode={addonsMode ?? 'contacts'}
        onClose={handleAddonsClose}
        trial={addonsTrial}
        contactCap={contactCap}
        contactPackOptions={contactPackOptions}
        contactPackUpdating={contactPackUpdating}
        onUpdateContactPacks={onUpdateContactPacks}
        taskPackOptions={taskPackOptions}
        taskPackUpdating={taskPackUpdating}
        onUpdateTaskPacks={onUpdateTaskPacks}
        taskQuota={taskQuota}
        manageBillingUrl={contactPackManageUrl}
      />
      <main className={mainClassName} data-sidebar-mode={sidebarMode} data-plan-mode={workspacePlanMode}>
        <div
          id="agent-workspace-root"
          data-plan-mode={workspacePlanMode}
        >
          <div className="agent-chat-workspace-main">
          <AgentTimelinePane
            composerDisabled={composerDisabled}
            contactCapOpenPacks={contactPackCanManageBilling ? () => handleAddonsOpen('contacts') : undefined}
            contactCapShowUpgrade={contactPackShowUpgrade}
            events={timelineRenderEvents}
            hardLimitShowUpsell={hardLimitShowUpsell}
            hardLimitUpgradeUrl={hardLimitUpgradeUrl}
            hasMoreNewer={hasMoreNewer}
            hasStreamingContent={hasStreamingContent}
            hideTypingIndicator={hideTypingIndicator}
            initialLoading={initialLoading}
            isStreaming={isStreaming}
            loadingNewer={loadingNewer}
            loadingOlder={loadingOlder}
            onContactCapDismiss={handleContactCapDismiss}
            onHardLimitOpenSettings={handleSettingsOpen}
            onHardLimitQuickIncrease={quickIncreaseTarget !== null ? handleQuickIncreaseLimit : undefined}
            onJumpToLatest={onJumpToLatest}
            onMessageLinkClick={handleMessageLinkClick}
            onPurchaseSeats={handlePurchaseSeats}
            onReportMessage={handleReportMessage}
            onRetryMessage={onRetryMessage}
            onStarterPromptDismiss={handleStarterPromptDismiss}
            onStarterPromptSelect={handleStarterPromptSelect}
            onTaskCreditsDismiss={handleTaskCreditsDismiss}
            onTaskCreditsOpenPacks={taskPackCanManageBilling ? () => handleAddonsOpen('tasks') : undefined}
            onTemplateRecommendationCreate={onTemplateRecommendationCreate}
            quickIncreaseBusy={quickIncreaseBusy}
            quickIncreaseLabel={quickIncreaseLabel ?? undefined}
            showContactCapCallout={showContactCapCallout}
            showHardLimitCallout={showHardLimitCallout}
            showJumpButton={showJumpButton}
            showNoSeatsCallout={showNoSeatsCallout}
            showProcessingIndicator={showProcessingIndicator}
            showScheduledResumeEvent={showScheduledResumeEvent}
            showStarterPrompts={pendingActionRequests.length === 0 && signupPreviewState === 'none' && (starterPromptsLoading || starterPrompts.length > 0)}
            showStreamingSlot={showStreamingSlot}
            showStreamingThinking={showStreamingThinking}
            showTemplateRecommendations={showTemplateRecommendations}
            showTaskCreditsCallout={showTaskCreditsCallout}
            showTaskCreditsUpgrade={showTaskCreditsUpgrade}
            showTypingIndicator={showTypingIndicator}
            starterPromptCount={starterPromptCount}
            starterPromptSubmitting={starterPromptSubmitting}
            starterPrompts={starterPrompts}
            starterPromptsDisabled={composerDisabled}
            starterPromptsLoading={starterPromptsLoading}
            statusExpansionTargets={statusExpansionTargets}
            suppressedThinkingCursor={suppressedThinkingCursor}
            templateRecommendations={templateRecommendations}
            templateRecommendationSubmittingId={templateRecommendationSubmittingId}
            taskCreditsWarningVariant={taskCreditsWarningVariant}
            timelineContentRef={timelineContentRef}
            timelineRef={timelineRef}
            typingStatusText={typingStatusText}
          />

          {/* Composer at bottom of flex layout */}
          {spawnIntentLoading ? (
            <div
              ref={composerShellRef}
              className="flex items-center justify-center py-10"
              aria-live="polite"
              aria-busy="true"
            >
              <div className="flex flex-col items-center gap-3 text-center">
                <Loader2 size={28} className="animate-spin text-blue-600" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-slate-700">Preparing your agent…</p>
                </div>
                {showComposerUnavailableSkipPlanning ? (
                  <button
                    type="button"
                    className="composer-skip-planning-button"
                    onClick={() => void onSkipPlanning?.()}
                    disabled={skipPlanningDisabled}
                    title={canManageAgent ? 'Skip Planning' : 'Only managers can skip planning'}
                  >
                    {skipPlanningBusy ? 'Skipping...' : 'Skip Planning'}
                  </button>
                ) : null}
              </div>
            </div>
          ) : effectiveShowSignupPreviewPanel ? (
            <div ref={composerShellRef}>
              <AgentSignupPreviewPanel
                onUpgrade={onUpgrade}
              />
            </div>
          ) : effectiveShowSubscriptionExpiredPanel ? (
            <div ref={composerShellRef}>
              <AgentUpgradePlansPanel
                title="Choose a plan to continue"
                body="Your agents are still here. Start a plan to resume messaging and create new agents."
                onUpgrade={onUpgrade}
                source="subscription_expired_panel"
              />
            </div>
          ) : (
            <AgentComposer
              onSubmit={onSendMessage}
              onSystemSubmit={onSendSystemMessage}
              normalSendDisabledReason={normalSendDisabledReason}
              showActionMenu={showComposerActionMenu}
              pendingActionRequests={pendingActionRequests}
              onSkipPlanning={onSkipPlanning}
              onRespondHumanInput={onRespondHumanInputRequest}
              onDismissHumanInput={onDismissHumanInputRequest}
              onResolveSpawnRequest={onResolveSpawnRequest}
              onFulfillRequestedSecrets={onFulfillRequestedSecrets}
              onRemoveRequestedSecrets={onRemoveRequestedSecrets}
              onResolveContactRequests={onResolveContactRequests}
              onViewAllContactRequests={onViewAllContactRequests}
              onFocus={onComposerFocus}
              onRequestScrollToBottom={onComposerRequestScrollToBottom}
              externalShellRef={composerShellRef}
              isProcessing={showProcessingIndicator}
              autoFocus={autoFocusComposer}
              insightsPanelExpandedPreference={insightsPanelExpandedPreference}
              insightsPanelPreferenceHydrated={insightsPanelPreferenceHydrated}
              onInsightsPanelExpandedPreferenceChange={onInsightsPanelExpandedPreferenceChange}
              insightsLoading={effectiveInsightsLoading}
              onOpenUsage={onOpenUsage}
              onOpenQuickSettings={canOpenQuickSettings ? handleSettingsOpen : undefined}
              usageUrl={sidebarUsageUrl}
              intelligenceConfig={llmIntelligence}
              intelligenceTier={currentLlmTier}
              onIntelligenceChange={onLlmTierChange}
              allowLockedIntelligenceSelection={allowLockedIntelligenceSelection}
              intelligenceBusy={llmTierSaving}
              intelligenceError={llmTierError}
              onOpenTaskPacks={resolvedOpenTaskPacks}
              onStopProcessing={onStopProcessing}
              disabled={composerDisabled}
              disabledReason={composerDisabledReason}
              submitError={composerError}
              showSubmitErrorUpgrade={composerErrorShowUpgrade}
              maxAttachmentBytes={maxAttachmentBytes}
              pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
              pipedreamAppSearchUrl={pipedreamAppSearchUrl}
              nativeIntegrationsUrl={nativeIntegrationsUrl}
              compact={sidebarMode === 'gallery'}
            />
          )}
          </div>
          {showDesktopPlanFrame ? (
            <div
              className={`agent-chat-plan-frame${floatingPlanExiting ? ' agent-chat-plan-frame--exiting' : ''}`}
              aria-label={showDesktopPlanPanel ? (isFloatingPlanPreview ? 'Open plan panel' : 'Plan panel') : undefined}
              aria-hidden={showDesktopPlanPanel ? undefined : true}
              role={isFloatingPlanPreview ? 'button' : undefined}
              tabIndex={isFloatingPlanPreview ? 0 : undefined}
              onClickCapture={isFloatingPlanPreview ? handleFloatingPlanClick : undefined}
              onKeyDown={isFloatingPlanPreview ? handleFloatingPlanKeyDown : undefined}
              onMouseEnter={isHoverPlanPreview ? () => handlePlanHoverChange(true) : undefined}
              onMouseLeave={isHoverPlanPreview ? () => handlePlanHoverChange(false) : undefined}
            >
              {showDesktopPlanPanel ? (
                <PlanPanel
                  plan={renderedPlanSnapshot}
                  onMessageClick={handlePlanMessageClick}
                  isAgentWorking={isWorkingNow}
                />
              ) : null}
            </div>
          ) : null}
        </div>
        {footer ? <div className="mt-6 px-4 sm:px-6 lg:px-10">{footer}</div> : null}
      </main>
      <ImmersiveDialog
        open={showPlanInterface && planSheetOpen}
        onClose={() => setPlanSheetOpen(false)}
        title="Plan"
        ariaLabel="Plan"
        bodyPadding={false}
        tone="plan"
        forceMode="sheet"
      >
        <PlanPanel
          plan={displayPlanSnapshot}
          onMessageClick={handlePlanMessageClick}
          compact
          isAgentWorking={isWorkingNow}
        />
      </ImmersiveDialog>
      <TextareaSubmitDialog
        open={Boolean(reportMessage)}
        title="Report message"
        subtitle="Tell us what went wrong so we can review this agent response."
        icon={Flag}
        textareaId="agent-message-report-comment"
        label="What should we know?"
        placeholder="Optional details about what was incorrect, unhelpful, or concerning."
        maxLength={2000}
        valueResetKey={reportMessage?.id ?? null}
        busy={reportSubmitting}
        error={reportError}
        onClose={handleReportDialogClose}
        onSubmit={handleReportSubmit}
        submitLabel="Submit report"
        busyLabel="Submitting..."
      />
      {isUpgradeModalOpen && isProprietaryMode && !isCollaborator ? (
        isMobileViewport && upgradeModalDismissible ? (
          <ImmersiveDialog
            open={isUpgradeModalOpen}
            onClose={handleUpgradeModalDismiss}
            title={upgradeTitle}
            subtitle={upgradeSubtitle}
            icon={Zap}
            ariaLabel={upgradeTitle}
            bodyPadding={false}
            forceMode="sheet"
          >
            <SubscriptionUpgradePlans
              onUpgrade={handleUpgradeSelection}
              source={upgradeModalSource ?? undefined}
            />
          </ImmersiveDialog>
        ) : (
          <SubscriptionUpgradeModal
            onUpgrade={handleUpgradeSelection}
          />
        )
      ) : null}
    </>
  )
}
