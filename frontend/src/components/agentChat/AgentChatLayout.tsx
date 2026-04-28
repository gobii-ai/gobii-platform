import type { ReactNode, Ref } from 'react'
import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import { Loader2, Zap } from 'lucide-react'
import '../../styles/agentChatLegacy.css'
import { TypingIndicator, deriveTypingStatusText } from './TypingIndicator'
import { track } from '../../util/analytics'
import { AnalyticsEvent } from '../../constants/analyticsEvents'
import { AgentComposer } from './AgentComposer'
import { TimelineVirtualItem } from './TimelineVirtualItem'
import { StreamingReplyCard } from './StreamingReplyCard'
import { StreamingThinkingCard } from './StreamingThinkingCard'
import { ChatSidebar } from './ChatSidebar'
import { AgentChatBanner, type ConnectionStatusTone } from './AgentChatBanner'
import { AgentChatMobileSheet } from './AgentChatMobileSheet'
import { AgentChatSettingsPanel } from './AgentChatSettingsPanel'
import { AgentChatAddonsPanel } from './AgentChatAddonsPanel'
import { HighPriorityBanner, type HighPriorityBannerConfig } from './HighPriorityBanner'
import { HardLimitCalloutCard } from './HardLimitCalloutCard'
import { ContactCapCalloutCard } from './ContactCapCalloutCard'
import { TaskCreditsCalloutCard } from './TaskCreditsCalloutCard'
import { ScheduledResumeCard } from './ScheduledResumeCard'
import { StarterPromptSuggestions } from './StarterPromptSuggestions'
import { AgentSignupPreviewPanel } from './AgentSignupPreviewPanel'
import { PlanningModeStrip } from './PlanningModeStrip'
import { getInitialAgentChatSidebarMode } from './sidebarMode'
import { useStarterPrompts } from './useStarterPrompts'
import { SubscriptionUpgradeModal } from '../common/SubscriptionUpgradeModal'
import { SubscriptionUpgradePlans } from '../common/SubscriptionUpgradePlans'
import type { AgentChatContextSwitcherData } from './AgentChatContextSwitcher'
import type { AgentTimelineProps } from './types'
import type {
  PendingActionRequest,
  ProcessingWebTask,
  StreamState,
  KanbanBoardSnapshot,
} from '../../types/agentChat'
import type { InsightEvent } from '../../types/insight'
import type { AgentRosterEntry, AgentRosterSortMode } from '../../types/agentRoster'
import type { PlanningState, SignupPreviewState } from '../../types/agentRoster'
import type { ConsoleContext } from '../../api/context'
import {
  isContinuationUpgradeModalSource,
  useSubscriptionStore,
  type PlanTier,
} from '../../stores/subscriptionStore'
import { buildAgentComposerPalette } from '../../util/color'
import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from '../../types/dailyCredits'
import type { AddonPackOption, ContactCapInfo, ContactCapStatus, TrialInfo } from '../../types/agentAddons'
import type { LlmIntelligenceConfig } from '../../types/llmIntelligence'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { StatusExpansionTargets } from './statusExpansion'

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

function isCurrentAgentSettingsHref(href: string, agentId: string): boolean {
  if (typeof window === 'undefined') {
    return false
  }
  try {
    const url = new URL(href, window.location.origin)
    return normalizeAgentSettingsPathname(url.pathname) === `/console/agents/${agentId}`
  } catch {
    return false
  }
}

function timelineEventKey(event: SimplifiedTimelineItem): string {
  if (event.kind === 'collapsed-group') {
    return `collapsed:${event.cursor}`
  }
  if (event.kind === 'steps' && event.entries.length > 0) {
    return `cluster:${event.entries[0].id}`
  }
  return event.cursor
}

type AgentChatLayoutProps = AgentTimelineProps & {
  displayEvents?: SimplifiedTimelineItem[]
  statusExpansionTargets?: StatusExpansionTargets
  realtimeEventCursors?: Set<string>
  onRealtimeEventAnimationConsumed?: (cursor: string) => void
  agentId?: string | null
  agentColorHex?: string | null
  agentAvatarUrl?: string | null
  agentEmail?: string | null
  agentSms?: string | null
  agentName?: string | null
  auditUrl?: string | null
  agentIsOrgOwned?: boolean
  canManageAgent?: boolean
  isCollaborator?: boolean
  hideInsightsPanel?: boolean
  viewerUserId?: number | null
  viewerEmail?: string | null
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  agentRoster?: AgentRosterEntry[]
  favoriteAgentIds?: string[]
  activeAgentId?: string | null
  insightsPanelExpandedPreference?: boolean | null
  switchingAgentId?: string | null
  rosterLoading?: boolean
  rosterError?: string | null
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onConfigureAgent?: (agent: AgentRosterEntry) => void
  onToggleAgentFavorite?: (agentId: string) => void
  onCreateAgent?: () => void
  createAgentDisabledReason?: string | null
  onBlockedCreateAgent?: (location: 'sidebar') => void
  agentRosterSortMode?: AgentRosterSortMode
  onAgentRosterSortModeChange?: (mode: AgentRosterSortMode) => void
  onInsightsPanelExpandedPreferenceChange?: (expanded: boolean) => void
  contextSwitcher?: AgentChatContextSwitcherData
  currentContext?: ConsoleContext | null
  sidebarBillingUrl?: string | null
  sidebarTodayCreditsUsed?: number | null
  sidebarCreditsResetOn?: string | null
  sidebarNotificationsEnabled?: boolean
  sidebarNotificationStatus?: 'off' | 'on' | 'needs_permission' | 'blocked'
  onSidebarNotificationsEnabledChange?: (enabled: boolean) => void
  autoFocusComposer?: boolean
  kanbanSnapshot?: KanbanBoardSnapshot | null
  footer?: ReactNode
  showEmbeddedSettings?: boolean
  embeddedSettingsPanel?: ReactNode
  embeddedSettingsTitle?: string
  onBackFromEmbeddedSettings?: () => void
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
  stopProcessingBusy?: boolean
  stopProcessingRequested?: boolean
  addonsTrial?: TrialInfo | null
  taskQuota?: TaskQuotaInfo | null
  showTaskCreditsWarning?: boolean
  taskCreditsWarningVariant?: 'low' | 'out' | null
  showTaskCreditsUpgrade?: boolean
  taskCreditsDismissKey?: string | null
  highPriorityBanner?: HighPriorityBannerConfig | null
  showOlderLoadButton?: boolean
  onLoadOlder?: () => void
  onLoadNewer?: () => void
  onJumpToLatest?: () => void
  onClose?: () => void
  onShare?: () => void
  onBlockedSettingsClick?: (location: 'banner_desktop' | 'banner_mobile') => void
  onBlockedCollaborate?: (location: 'banner_desktop' | 'banner_mobile' | 'insight_card') => void
  onSendMessage?: (
    body: string,
    attachments?: File[],
  ) => void | Promise<void>
  onComposerFocus?: () => void
  autoScrollPinned?: boolean
  isNearBottom?: boolean
  hasUnseenActivity?: boolean
  timelineRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
  processingWebTasks?: ProcessingWebTask[]
  nextScheduledAt?: string | null
  processingStartedAt?: number | null
  awaitingResponse?: boolean
  streaming?: StreamState | null
  insights?: InsightEvent[]
  insightsLoading?: boolean
  currentInsightIndex?: number
  onDismissInsight?: (insightId: string) => void
  onInsightIndexChange?: (index: number) => void
  onPauseChange?: (paused: boolean) => void
  isInsightsPaused?: boolean
  onUpgrade?: (plan: PlanTier, source?: string) => void
  llmIntelligence?: LlmIntelligenceConfig | null
  currentLlmTier?: string | null
  onLlmTierChange?: (tier: string) => Promise<boolean>
  allowLockedIntelligenceSelection?: boolean
  llmTierSaving?: boolean
  llmTierError?: string | null
  onOpenTaskPacks?: () => void
  spawnIntentLoading?: boolean
  starterPromptsDisabled?: boolean
  composerDisabled?: boolean
  composerDisabledReason?: string | null
  composerError?: string | null
  composerErrorShowUpgrade?: boolean
  showSignupPreviewPanel?: boolean
  signupPreviewState?: SignupPreviewState
  planningState?: PlanningState
  onSkipPlanning?: () => void | Promise<void>
  skipPlanningBusy?: boolean
  maxAttachmentBytes?: number | null
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
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
  onResolveContactRequests?: (
    responses: Array<{
      requestId: string
      decision: 'approve' | 'decline'
      allowInbound: boolean
      allowOutbound: boolean
      canConfigure: boolean
    }>
  ) => Promise<void>
}

export function AgentChatLayout({
  agentFirstName,
  events,
  displayEvents,
  statusExpansionTargets,
  realtimeEventCursors,
  onRealtimeEventAnimationConsumed,
  agentId,
  agentColorHex,
  agentAvatarUrl,
  agentEmail,
  agentSms,
  agentName,
  auditUrl,
  agentIsOrgOwned = false,
  canManageAgent = true,
  isCollaborator = false,
  hideInsightsPanel = false,
  viewerUserId,
  viewerEmail,
  connectionStatus,
  connectionLabel,
  connectionDetail,
  agentRoster,
  favoriteAgentIds,
  activeAgentId,
  insightsPanelExpandedPreference = null,
  switchingAgentId,
  rosterLoading,
  rosterError,
  onSelectAgent,
  onConfigureAgent,
  onToggleAgentFavorite,
  onCreateAgent,
  createAgentDisabledReason = null,
  onBlockedCreateAgent,
  agentRosterSortMode = 'recent',
  onAgentRosterSortModeChange,
  onInsightsPanelExpandedPreferenceChange,
  contextSwitcher,
  currentContext = null,
  sidebarBillingUrl = null,
  sidebarTodayCreditsUsed = null,
  sidebarCreditsResetOn = null,
  sidebarNotificationsEnabled = true,
  sidebarNotificationStatus = 'off',
  onSidebarNotificationsEnabledChange,
  autoFocusComposer = false,
  kanbanSnapshot,
  footer,
  showEmbeddedSettings = false,
  embeddedSettingsPanel,
  embeddedSettingsTitle = 'Agent Settings',
  onBackFromEmbeddedSettings,
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
  stopProcessingBusy = false,
  stopProcessingRequested = false,
  addonsTrial = null,
  taskQuota = null,
  showTaskCreditsWarning = false,
  taskCreditsWarningVariant = null,
  showTaskCreditsUpgrade = false,
  taskCreditsDismissKey = null,
  highPriorityBanner = null,
  showOlderLoadButton = false,
  onLoadOlder,
  hasMoreNewer,
  processingActive,
  awaitingResponse = false,
  processingWebTasks = [],
  nextScheduledAt = null,
  streaming,
  onJumpToLatest,
  onClose,
  onShare,
  onBlockedSettingsClick,
  onBlockedCollaborate,
  onSendMessage,
  onComposerFocus,
  autoScrollPinned = true,
  isNearBottom = true,
  hasUnseenActivity = false,
  timelineRef,
  loadingOlder = false,
  loadingNewer = false,
  initialLoading = false,
  insights,
  insightsLoading = false,
  currentInsightIndex,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange,
  isInsightsPaused,
  onUpgrade,
  llmIntelligence = null,
  currentLlmTier = null,
  onLlmTierChange,
  allowLockedIntelligenceSelection = false,
  llmTierSaving = false,
  llmTierError = null,
  onOpenTaskPacks,
  spawnIntentLoading = false,
  starterPromptsDisabled = false,
  composerDisabled = false,
  composerDisabledReason = null,
  composerError = null,
  composerErrorShowUpgrade = false,
  showSignupPreviewPanel = false,
  signupPreviewState = 'none',
  planningState = 'skipped',
  onSkipPlanning,
  skipPlanningBusy = false,
  maxAttachmentBytes = null,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
  pendingActionRequests = [],
  onRespondHumanInputRequest,
  onDismissHumanInputRequest,
  onResolveSpawnRequest,
  onFulfillRequestedSecrets,
  onRemoveRequestedSecrets,
  onResolveContactRequests,
}: AgentChatLayoutProps) {
  const timelineRenderEvents = displayEvents ?? (events as SimplifiedTimelineItem[])

  const [sidebarMode, setSidebarMode] = useState(getInitialAgentChatSidebarMode)
  const {
    currentPlan: subscriptionPlan,
    isLoading: subscriptionLoading,
    isUpgradeModalOpen,
    closeUpgradeModal,
    upgradeModalSource,
    upgradeModalDismissible,
    isProprietaryMode,
    ctaPickAPlan,
    trialDaysByPlan,
    trialEligible,
  } = useSubscriptionStore()
  const maxTrialDays = Math.max(trialDaysByPlan.startup, trialDaysByPlan.scale)
  const useTrialUpgradeCopy = (
    trialEligible
    && maxTrialDays > 0
    && (upgradeModalSource === 'trial_onboarding' || subscriptionPlan === 'free')
  )
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
  const [isMobileUpgrade, setIsMobileUpgrade] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth < 768
  })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [addonsMode, setAddonsMode] = useState<'contacts' | 'tasks' | null>(null)
  const [contactCapDismissed, setContactCapDismissed] = useState(false)
  const [taskCreditsDismissed, setTaskCreditsDismissed] = useState(false)
  const [highPriorityDismissed, setHighPriorityDismissed] = useState(false)
  const [quickIncreaseBusy, setQuickIncreaseBusy] = useState(false)
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
    if (showEmbeddedSettings && mode !== 'gallery') {
      setSidebarMode(mode)
      onBackFromEmbeddedSettings?.()
      return
    }
    setSidebarMode(mode)
  }, [onBackFromEmbeddedSettings, showEmbeddedSettings])

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

  const handleAddonsOpen = useCallback((mode: 'contacts' | 'tasks') => {
    setAddonsMode(mode)
    onRefreshAddons?.()
  }, [onRefreshAddons])

  const handleAddonsClose = useCallback(() => {
    setAddonsMode(null)
  }, [])

  useEffect(() => {
    const checkMobile = () => {
      setIsMobileUpgrade(window.innerWidth < 768)
    }
    checkMobile()
    window.addEventListener('resize', checkMobile)
    return () => window.removeEventListener('resize', checkMobile)
  }, [])

  useEffect(() => {
    if (showEmbeddedSettings) {
      setSidebarMode('gallery')
    }
  }, [showEmbeddedSettings])

  useEffect(() => {
    if (!isUpgradeModalOpen) {
      return
    }
    if (isCollaborator) {
      closeUpgradeModal()
      return
    }
    if (subscriptionLoading) {
      return
    }
    if (!isProprietaryMode) {
      closeUpgradeModal()
    }
  }, [closeUpgradeModal, isCollaborator, isProprietaryMode, isUpgradeModalOpen, subscriptionLoading])

  const handleUpgradeModalDismiss = useCallback(() => {
    if (!upgradeModalDismissible) {
      return
    }
    track(AnalyticsEvent.UPGRADE_MODAL_DISMISSED, {
      currentPlan: subscriptionPlan,
      source: upgradeModalSource ?? 'unknown',
    })
    closeUpgradeModal()
  }, [closeUpgradeModal, subscriptionPlan, upgradeModalDismissible, upgradeModalSource])

  const handleUpgradeSelection = useCallback((plan: PlanTier) => {
    onUpgrade?.(plan)
    closeUpgradeModal()
  }, [closeUpgradeModal, onUpgrade])

  const resolvedOpenTaskPacks = useMemo(
    () =>
      onOpenTaskPacks ??
      (taskPackCanManageBilling && taskPackOptions.length > 0
        ? () => handleAddonsOpen('tasks')
        : undefined),
    [handleAddonsOpen, onOpenTaskPacks, taskPackCanManageBilling, taskPackOptions.length],
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
    const showTaskCredits = Boolean(showTaskCreditsWarning && !taskCreditsDismissed)
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
  }, [agentId, showTaskCreditsWarning, taskCreditsDismissed, taskCreditsWarningVariant, showTaskCreditsUpgrade])

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
  const showBottomSentinel = !initialLoading && !hasMoreNewer
  const starterPromptCount = typeof window !== 'undefined' && window.innerWidth < 768 ? 2 : 3
  const {
    starterPrompts,
    starterPromptsLoading,
    starterPromptSubmitting,
    handleStarterPromptSelect,
  } = useStarterPrompts({
    agentId,
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
  const composerPalette = useMemo(() => buildAgentComposerPalette(agentColorHex), [agentColorHex])
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
  const showTaskCreditsCallout = Boolean(showTaskCreditsWarning && !taskCreditsDismissed)
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
  const effectiveShowSignupPreviewPanel = showSignupPreviewPanel && planningState !== 'planning'
  const canOpenQuickSettings = Boolean(onUpdateDailyCredits || (llmIntelligence && onLlmTierChange))

  const handleMessageLinkClick = useCallback((href: string) => {
    if (!agentId || !isCurrentAgentSettingsHref(href, agentId)) {
      return false
    }
    if (previewActionsDisabled && onBlockedSettingsClick) {
      onBlockedSettingsClick('banner_desktop')
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
    previewActionsDisabled,
  ])

  const mainClassName = 'agent-chat-main'
  const sidebarSettings = useMemo(() => ({
    context: currentContext,
    viewerEmail: viewerEmail ?? null,
    isProprietaryMode,
    billingUrl: sidebarBillingUrl,
    notificationsEnabled: sidebarNotificationsEnabled,
    notificationStatus: sidebarNotificationStatus,
    onNotificationsEnabledChange: onSidebarNotificationsEnabledChange,
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
    sidebarBillingUrl,
    sidebarCreditsResetOn,
    sidebarNotificationStatus,
    sidebarNotificationsEnabled,
    sidebarTodayCreditsUsed,
    taskQuota,
    viewerEmail,
  ])

  return (
    <>
      <ChatSidebar
        desktopMode={sidebarMode}
        onDesktopModeChange={handleSidebarModeChange}
        agents={agentRoster}
        favoriteAgentIds={favoriteAgentIds}
        activeAgentId={activeAgentId}
        switchingAgentId={switchingAgentId}
        loading={rosterLoading}
        errorMessage={rosterError}
        onSelectAgent={onSelectAgent}
        onConfigureAgent={onConfigureAgent}
        onToggleAgentFavorite={onToggleAgentFavorite}
        onCreateAgent={onCreateAgent}
        createAgentDisabledReason={createAgentDisabledReason}
        onBlockedCreateAgent={onBlockedCreateAgent}
        rosterSortMode={agentRosterSortMode}
        onRosterSortModeChange={onAgentRosterSortModeChange}
        contextSwitcher={contextSwitcher}
        settings={sidebarSettings}
        showEmbeddedSettings={showEmbeddedSettings}
        embeddedSettingsPanel={embeddedSettingsPanel}
        embeddedSettingsTitle={embeddedSettingsTitle}
        onBackFromEmbeddedSettings={onBackFromEmbeddedSettings}
      />
	      {showBanner && (
	        <AgentChatBanner
	          agentId={agentId}
	          agentName={agentName || 'Agent'}
	          agentAvatarUrl={agentAvatarUrl}
	          agentColorHex={agentColorHex}
	          agentEmail={agentEmail}
	          agentSms={agentSms}
	          auditUrl={auditUrl}
	          isOrgOwned={agentIsOrgOwned}
	          canManageAgent={canManageAgent}
	          isCollaborator={isCollaborator}
	          connectionStatus={connectionStatus}
	          connectionLabel={connectionLabel}
          connectionDetail={connectionDetail}
          kanbanSnapshot={kanbanSnapshot}
          processingActive={processingActive}
          dailyCreditsStatus={dailyCreditsStatus}
          onSettingsOpen={canOpenQuickSettings ? handleSettingsOpen : undefined}
          settingsDisabled={previewActionsDisabled}
          settingsDisabledReason={previewActionsDisabledReason}
          onBlockedSettingsClick={onBlockedSettingsClick}
          onClose={onClose}
          onShare={onShare}
          shareDisabled={previewActionsDisabled}
          shareDisabledReason={previewActionsDisabledReason}
          onBlockedShareClick={onBlockedCollaborate}
          signupPreviewState={signupPreviewState}
	          sidebarMode={sidebarMode}
	        >
            {showHighPriorityBanner && highPriorityBanner ? (
              <HighPriorityBanner
                title={highPriorityBanner.title}
                message={highPriorityBanner.message}
                actionLabel={highPriorityBanner.actionLabel}
                actionHref={highPriorityBanner.actionHref}
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
      <main className={mainClassName} data-sidebar-mode={sidebarMode}>
        <div
          id="agent-workspace-root"
          style={composerPalette.cssVars}
        >
          {/* Scrollable timeline container */}
          <div ref={timelineRef} id="timeline-shell" data-scroll-pinned={autoScrollPinned ? 'true' : 'false'}>
            {/* Spacer pushes content to bottom when there's extra space */}
            <div id="timeline-spacer" aria-hidden="true" />
            <div id="timeline-inner">
              <div id="timeline-events" className="flex flex-col" data-has-jump-button={showJumpButton ? 'true' : 'false'} data-has-working-panel={showProcessingIndicator ? 'true' : 'false'}>
                {loadingOlder ? (
                  <div className="timeline-load-control" data-side="older" data-state="loading">
                    <div className="timeline-load-button" role="status">
                      <span className="timeline-load-indicator" data-loading="true" aria-hidden="true" />
                      <span className="timeline-load-label">Loading…</span>
                    </div>
                  </div>
                ) : showOlderLoadButton && onLoadOlder ? (
                  <div className="timeline-load-control" data-side="older" data-state="ready">
                    <button type="button" className="timeline-load-button" onClick={onLoadOlder}>
                      <span className="timeline-load-indicator" aria-hidden="true" />
                      <span className="timeline-load-label">Load older activity</span>
                    </button>
                  </div>
                ) : null}

                {initialLoading ? (
                  <div className="flex items-center justify-center py-10" aria-live="polite" aria-busy="true">
                    <div className="flex flex-col items-center gap-3 text-center">
                      <Loader2 size={28} className="animate-spin text-blue-600" aria-hidden="true" />
                      <div>
                        <p className="text-sm font-semibold text-slate-700">Loading conversation…</p>
                      </div>
                    </div>
                  </div>
                ) : timelineRenderEvents.map((event, index) => (
                  <TimelineVirtualItem
                    key={timelineEventKey(event)}
                    event={event}
                    isLatestEvent={index === timelineRenderEvents.length - 1}
                    agentFirstName={agentFirstName}
                    agentColorHex={agentColorHex || undefined}
                    agentAvatarUrl={agentAvatarUrl}
                    viewerUserId={viewerUserId ?? null}
                    viewerEmail={viewerEmail ?? null}
                    suppressedThinkingCursor={suppressedThinkingCursor}
                    statusExpansionTargets={statusExpansionTargets}
                    animateIncoming={realtimeEventCursors?.has(event.cursor) ?? false}
                    onIncomingAnimationConsumed={onRealtimeEventAnimationConsumed}
                    onMessageLinkClick={handleMessageLinkClick}
                  />
                ))}
                {showScheduledResumeEvent ? (
                  <ScheduledResumeCard nextScheduledAt={nextScheduledAt} />
                ) : null}
                {showHardLimitCallout ? (
                  <HardLimitCalloutCard
                    onOpenSettings={handleSettingsOpen}
                    onQuickIncrease={quickIncreaseTarget !== null ? handleQuickIncreaseLimit : undefined}
                    quickIncreaseLabel={quickIncreaseLabel ?? undefined}
                    quickIncreaseBusy={quickIncreaseBusy}
                    upgradeUrl={hardLimitUpgradeUrl}
                    showUpsell={hardLimitShowUpsell}
                  />
                ) : null}
                {showTaskCreditsCallout ? (
                  <TaskCreditsCalloutCard
                    onOpenPacks={taskPackCanManageBilling && (taskPackOptions?.length ?? 0) > 0
                      ? () => handleAddonsOpen('tasks')
                      : undefined}
                    showUpgrade={showTaskCreditsUpgrade}
                    onDismiss={handleTaskCreditsDismiss}
                    variant={taskCreditsWarningVariant === 'out' ? 'out' : 'low'}
                  />
                ) : null}
                {showContactCapCallout ? (
                  <ContactCapCalloutCard
                    onOpenPacks={contactPackCanManageBilling && contactPackOptions.length > 0
                      ? () => handleAddonsOpen('contacts')
                      : undefined}
                    showUpgrade={contactPackShowUpgrade}
                    onDismiss={handleContactCapDismiss}
                  />
                ) : null}
                {pendingActionRequests.length === 0
                && signupPreviewState === 'none'
                && (starterPromptsLoading || starterPrompts.length > 0) ? (
                  <StarterPromptSuggestions
                    prompts={starterPrompts}
                    loading={starterPromptsLoading}
                    loadingCount={starterPromptCount}
                    disabled={starterPromptSubmitting || starterPromptsDisabled || composerDisabled}
                    onSelect={handleStarterPromptSelect}
                  />
                ) : null}

                {showStreamingThinking ? (
                  <StreamingThinkingCard
                    reasoning={streaming?.reasoning || ''}
                    isStreaming={isStreaming}
                  />
                ) : null}

                {showStreamingSlot && !hasMoreNewer ? (
                  <div id="streaming-response-slot" className="streaming-response-slot flex flex-col">
                    {hasStreamingContent ? (
                      <StreamingReplyCard
                        content={streaming?.content || ''}
                        agentFirstName={agentFirstName}
                        agentAvatarUrl={agentAvatarUrl}
                        agentColorHex={agentColorHex}
                        isStreaming={isStreaming}
                      />
                    ) : null}
                  </div>
                ) : null}

                {showTypingIndicator ? (
                  <TypingIndicator
                    statusText={typingStatusText}
                    agentColorHex={agentColorHex || undefined}
                    agentAvatarUrl={agentAvatarUrl}
                    agentFirstName={agentFirstName}
                    hidden={hideTypingIndicator}
                  />
                ) : null}

                {showBottomSentinel ? (
                  <div id="timeline-bottom-sentinel" className="timeline-bottom-sentinel" aria-hidden="true" />
                ) : null}

                {loadingNewer ? (
                  <div className="timeline-load-control" data-side="newer" data-state="loading">
                    <div className="timeline-load-button" role="status">
                      <span className="timeline-load-indicator" data-loading="true" aria-hidden="true" />
                      <span className="timeline-load-label">Loading…</span>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

          </div>

          {/* Jump button outside scroll container so position:fixed works on iOS Safari */}
          <button
            id="jump-to-latest"
            className="jump-to-latest"
            type="button"
            aria-label="Jump to latest"
            aria-hidden={showJumpButton ? 'false' : 'true'}
            onClick={onJumpToLatest}
            data-has-activity={hasUnseenActivity ? 'true' : 'false'}
            data-visible={showJumpButton ? 'true' : 'false'}
          >
            <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 5v14m0 0-5-5m5 5 5-5" />
            </svg>
            <span className="sr-only">Jump to latest</span>
          </button>

          {/* Composer at bottom of flex layout */}
          {planningState === 'planning' && (spawnIntentLoading || effectiveShowSignupPreviewPanel) ? (
            <PlanningModeStrip
              canManageAgent={canManageAgent}
              onSkipPlanning={onSkipPlanning}
              skipPlanningBusy={skipPlanningBusy}
              className="mx-4 mb-3 rounded-lg border border-sky-100 sm:mx-6 lg:mx-10"
            />
          ) : null}
          {spawnIntentLoading ? (
            <div className="flex items-center justify-center py-10" aria-live="polite" aria-busy="true">
              <div className="flex flex-col items-center gap-3 text-center">
                <Loader2 size={28} className="animate-spin text-blue-600" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-slate-700">Preparing your agent…</p>
                </div>
              </div>
            </div>
          ) : effectiveShowSignupPreviewPanel ? (
            <AgentSignupPreviewPanel
              status={signupPreviewState}
              agentId={agentId}
              agentName={agentName}
              currentPlan={subscriptionPlan}
              onUpgrade={onUpgrade}
            />
          ) : (
            <AgentComposer
              agentId={activeAgentId ?? agentId ?? null}
              agentName={agentName ?? null}
              onSubmit={onSendMessage}
              pendingActionRequests={pendingActionRequests}
              planningState={planningState}
              onSkipPlanning={onSkipPlanning}
              skipPlanningBusy={skipPlanningBusy}
              onRespondHumanInput={onRespondHumanInputRequest}
              onDismissHumanInput={onDismissHumanInputRequest}
              onResolveSpawnRequest={onResolveSpawnRequest}
              onFulfillRequestedSecrets={onFulfillRequestedSecrets}
              onRemoveRequestedSecrets={onRemoveRequestedSecrets}
              onResolveContactRequests={onResolveContactRequests}
              onFocus={onComposerFocus}
              agentFirstName={agentFirstName}
              isProcessing={showProcessingIndicator}
              processingTasks={processingWebTasks}
              autoFocus={autoFocusComposer}
              focusKey={activeAgentId}
              insightsPanelExpandedPreference={insightsPanelExpandedPreference}
              onInsightsPanelExpandedPreferenceChange={onInsightsPanelExpandedPreferenceChange}
              insights={insights}
              insightsLoading={insightsLoading}
              currentInsightIndex={currentInsightIndex}
              onDismissInsight={onDismissInsight}
              onInsightIndexChange={onInsightIndexChange}
              onPauseChange={onPauseChange}
              isInsightsPaused={isInsightsPaused}
              onCollaborate={onShare}
              collaborateDisabled={previewActionsDisabled}
              collaborateDisabledReason={previewActionsDisabledReason}
              onBlockedCollaborate={onBlockedCollaborate}
              hideInsightsPanel={hideInsightsPanel}
              intelligenceConfig={llmIntelligence}
              intelligenceTier={currentLlmTier}
              onIntelligenceChange={onLlmTierChange}
              allowLockedIntelligenceSelection={allowLockedIntelligenceSelection}
              intelligenceBusy={llmTierSaving}
              intelligenceError={llmTierError}
              onOpenTaskPacks={resolvedOpenTaskPacks}
              canManageAgent={canManageAgent}
              onStopProcessing={onStopProcessing}
              stopProcessingBusy={stopProcessingBusy}
              stopProcessingRequested={stopProcessingRequested}
              disabled={composerDisabled}
              disabledReason={composerDisabledReason}
              submitError={composerError}
              showSubmitErrorUpgrade={composerErrorShowUpgrade}
              maxAttachmentBytes={maxAttachmentBytes}
              pipedreamAppsSettingsUrl={pipedreamAppsSettingsUrl}
              pipedreamAppSearchUrl={pipedreamAppSearchUrl}
            />
          )}
        </div>
        {footer ? <div className="mt-6 px-4 sm:px-6 lg:px-10">{footer}</div> : null}
      </main>
      {isUpgradeModalOpen && isProprietaryMode && !isCollaborator ? (
        isMobileUpgrade && upgradeModalDismissible ? (
          <AgentChatMobileSheet
            open={isUpgradeModalOpen}
            onClose={handleUpgradeModalDismiss}
            title={upgradeTitle}
            subtitle={upgradeSubtitle}
            icon={Zap}
            ariaLabel={upgradeTitle}
            bodyPadding={false}
          >
            <SubscriptionUpgradePlans
              currentPlan={subscriptionPlan}
              onUpgrade={handleUpgradeSelection}
              source={upgradeModalSource ?? undefined}
            />
          </AgentChatMobileSheet>
        ) : (
          <SubscriptionUpgradeModal
            currentPlan={subscriptionPlan}
            onClose={handleUpgradeModalDismiss}
            onUpgrade={handleUpgradeSelection}
            source={upgradeModalSource ?? undefined}
            dismissible={upgradeModalDismissible}
          />
        )
      ) : null}
    </>
  )
}
