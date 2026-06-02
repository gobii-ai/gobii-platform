import type { ChangeEvent, ClipboardEvent, FormEvent, KeyboardEvent } from 'react'
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Dialog, DialogTrigger, Popover } from 'react-aria-components'
import { ArrowUp, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Gauge, Loader2, MessageSquare, MessageSquareQuote, OctagonAlert, Paperclip, Plus, Rocket, Sparkles, TriangleAlert, Zap, X } from 'lucide-react'

import { InsightEventCard } from './insights'
import { ApolloInsightPanel } from './insights/ApolloInsightPanel'
import { GoogleDriveInsightPanel } from './insights/GoogleDriveInsightPanel'
import { AgentIntelligenceSelector } from './AgentIntelligenceSelector'
import { ComposerPipedreamAppsControl } from './ComposerPipedreamAppsControl'
import { PendingActionComposerPanel } from './PendingActionComposerPanel'
import { HUMAN_INPUT_OTHER_OPTION_KEY } from './HumanInputComposerPanel'
import { orderHumanInputRequests } from './humanInputOrdering'
import type { PendingActionRequest, PendingHumanInputRequest, ProcessingWebTask } from '../../types/agentChat'
import type { InsightEvent, BurnRateMetadata, AgentSetupMetadata } from '../../types/insight'
import { INSIGHT_TIMING } from '../../types/insight'
import { useSubscriptionStore } from '../../stores/subscriptionStore'
import { track, AnalyticsEvent } from '../../util/analytics'
import { formatBytes } from '../../util/formatBytes'
import { appendReturnTo } from '../../util/returnTo'
import type { LlmIntelligenceConfig } from '../../types/llmIntelligence'
import type { PlanningState } from '../../types/agentRoster'

// Detect if user is on macOS
function isMacOS(): boolean {
  if (typeof navigator === 'undefined') return false
  return /Mac|iPod|iPhone|iPad/.test(navigator.platform)
}

function shouldShowSubmitShortcutHint(): boolean {
  if (typeof window === 'undefined') return true
  return window.innerWidth >= 768
}

function getBurnRateUsagePercent(metadata: BurnRateMetadata): number {
  return Math.max(
    metadata.todayUsage.percentUsed ?? -1,
    metadata.monthUsage.percentUsed ?? -1,
    0,
  )
}

function getBurnRateUsageLevel(metadata: BurnRateMetadata): 'normal' | 'warning' | 'critical' {
  const percent = getBurnRateUsagePercent(metadata)
  if (percent >= 100) return 'critical'
  if (percent >= 90) return 'warning'
  return 'normal'
}

const DEFAULT_INSIGHT_TAB_COLOR = '#AA74CE'

function getInsightTabColor(insight: InsightEvent): string {
  if (insight.insightType === 'burn_rate') {
    const meta = insight.metadata as BurnRateMetadata
    const level = getBurnRateUsageLevel(meta)
    if (level === 'critical') return '#dc2626'
    if (level === 'warning') return '#d97706'
    return DEFAULT_INSIGHT_TAB_COLOR
  }
  if (insight.insightType === 'agent_setup') {
    return DEFAULT_INSIGHT_TAB_COLOR
  }
  return '#6b7280' // gray-500 fallback
}

function getInsightTabLabel(insight: InsightEvent): string {
  if (insight.insightType === 'burn_rate') {
    return 'Usage'
  }
  if (insight.insightType === 'agent_setup') {
    const meta = insight.metadata as AgentSetupMetadata
    switch (meta.panel) {
      case 'always_on':
        return '24/7'
      case 'sms':
        return 'SMS'
      case 'upsell_pro':
        return 'Go Pro'
      case 'upsell_scale':
        return 'Go Scale'
      default:
        return '24/7'
    }
  }
  return 'Insight'
}

function getInsightTabIcon(insight: InsightEvent) {
  if (insight.insightType === 'burn_rate') {
    const meta = insight.metadata as BurnRateMetadata
    const level = getBurnRateUsageLevel(meta)
    if (level === 'critical') {
      return <OctagonAlert size={11} strokeWidth={2.2} />
    }
    if (level === 'warning') {
      return <TriangleAlert size={11} strokeWidth={2.2} />
    }
    return <Gauge size={11} strokeWidth={2.2} />
  }
  if (insight.insightType === 'agent_setup') {
    const meta = insight.metadata as AgentSetupMetadata
    if (meta.panel === 'sms') {
      return <MessageSquare size={11} strokeWidth={2.2} />
    }
    if (meta.panel === 'upsell_pro' || meta.panel === 'upsell_scale') {
      return <Zap size={11} strokeWidth={2.2} />
    }
    return <Rocket size={11} strokeWidth={2.2} />
  }
  return <Sparkles size={11} strokeWidth={2.2} />
}

function getPendingActionRequestCount(action: PendingActionRequest): number {
  switch (action.kind) {
    case 'human_input':
    case 'contact_requests':
      return Math.max(1, action.count || action.requests.length)
    case 'requested_secrets':
      return Math.max(1, action.count || action.secrets.length)
    case 'spawn_request':
      return 1
    default:
      return 1
  }
}

type PendingActionNavigationItem =
  | { actionId: string; kind: 'human_input'; requestId: string }
  | { actionId: string; kind: 'action' }

type HumanInputComposerResponse = {
  requestId: string
  selectedOptionKey?: string
  freeText?: string
}

type HumanInputComposerBatchResponse = {
  batchId: string
  responses: HumanInputComposerResponse[]
}

type WorkingPanelTab =
  | { id: string; kind: 'insight'; insight: InsightEvent; insightIndex: number }
  | { id: NativeWorkingTabKind; kind: NativeWorkingTabKind }

type NativeWorkingTabKind = 'google_drive' | 'apollo'

const NATIVE_WORKING_TAB_CONFIG = {
  google_drive: {
    label: 'Drive',
    title: 'Google Drive',
    ariaLabel: 'View Google Drive files',
    panel: GoogleDriveInsightPanel,
    icon: <img src="/static/images/integrations/native/google_drive.svg" alt="" className="composer-insight-tab-image" />,
  },
  apollo: {
    label: 'Apollo',
    title: 'Apollo',
    ariaLabel: 'View Apollo connection',
    panel: ApolloInsightPanel,
    icon: (
      <span className="inline-flex h-3.5 w-3.5 items-center justify-center rounded-sm bg-[#F8FF2C]">
        <img src="/static/images/integrations/native/apollo.svg" alt="" className="h-3 w-3 object-contain" />
      </span>
    ),
  },
} as const

function hasHumanInputComposerResponse(
  request: PendingHumanInputRequest,
  response: HumanInputComposerResponse | undefined,
): response is HumanInputComposerResponse {
  if (!response) {
    return false
  }
  if (
    request.inputMode === 'free_text_only'
    || request.options.length === 0
    || response.selectedOptionKey === HUMAN_INPUT_OTHER_OPTION_KEY
  ) {
    return Boolean(response.freeText?.trim())
  }
  return Boolean(response.selectedOptionKey)
}

function buildSubmittedHumanInputResponse(
  request: PendingHumanInputRequest,
  response: HumanInputComposerResponse | undefined,
): HumanInputComposerResponse | null {
  if (!hasHumanInputComposerResponse(request, response)) {
    return null
  }
  if (
    request.inputMode === 'free_text_only'
    || request.options.length === 0
    || response.selectedOptionKey === HUMAN_INPUT_OTHER_OPTION_KEY
  ) {
    const freeText = response.freeText?.trim()
    if (!freeText) {
      return null
    }
    return {
      requestId: request.id,
      freeText,
    }
  }
  if (!response.selectedOptionKey) {
    return null
  }
  return {
    requestId: request.id,
    selectedOptionKey: response.selectedOptionKey,
  }
}

type HumanInputBatchAnalyticsProperties = {
  agent_id: string
  agent_name: string | null
  batch_id: string
  request_count: number
  is_batch: boolean
  active_conversation_channel: string | null
  options_request_count: number
  free_text_request_count: number
  total_option_count: number
}

function buildHumanInputBatchAnalyticsProperties(
  agentId: string,
  agentName: string | null,
  requests: PendingHumanInputRequest[],
): HumanInputBatchAnalyticsProperties {
  const optionsRequestCount = requests.filter((request) => request.options.length > 0).length
  return {
    agent_id: agentId,
    agent_name: agentName,
    batch_id: requests[0]?.batchId ?? '',
    request_count: requests.length,
    is_batch: requests.length > 1,
    active_conversation_channel: requests[0]?.activeConversationChannel ?? null,
    options_request_count: optionsRequestCount,
    free_text_request_count: requests.length - optionsRequestCount,
    total_option_count: requests.reduce((count, request) => count + request.options.length, 0),
  }
}

type ComposerAppsAction = {
  openModal: () => void
  disabled: boolean
  loading: boolean
}

type ComposerActionMenuProps = {
  disabled?: boolean
  onUploadFiles: () => void
  appsAction?: ComposerAppsAction | null
}

function ComposerActionMenu({
  disabled = false,
  onUploadFiles,
  appsAction = null,
}: ComposerActionMenuProps) {
  const [open, setOpen] = useState(false)

  return (
    <DialogTrigger isOpen={open} onOpenChange={setOpen}>
      <Button
        className="composer-action-trigger"
        aria-label="More composer actions"
        isDisabled={disabled}
      >
        <Plus className="h-4 w-4" aria-hidden="true" />
      </Button>
      <Popover className="composer-action-popover" placement="top start" offset={10}>
        <Dialog className="composer-action-menu">
          <button
            type="button"
            className="composer-action-item"
            onClick={() => {
              onUploadFiles()
              setOpen(false)
            }}
            disabled={disabled}
          >
            <span className="composer-action-item-icon" aria-hidden="true">
              <Paperclip className="h-3.5 w-3.5" />
            </span>
            <span className="composer-action-item-label">Upload Files</span>
          </button>
          {appsAction ? (
            <>
              <div className="composer-action-divider" aria-hidden="true" />
              <button
                type="button"
                className="composer-action-item"
                onClick={() => {
                  appsAction.openModal()
                  setOpen(false)
                }}
                disabled={appsAction.disabled}
              >
                <span className="composer-action-item-icon" aria-hidden="true">
                  {appsAction.loading ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="h-3.5 w-3.5" />
                  )}
                </span>
                <span className="composer-action-item-label">Apps</span>
              </button>
            </>
          ) : null}
        </Dialog>
      </Popover>
    </DialogTrigger>
  )
}

type AgentComposerProps = {
  agentId?: string | null
  agentName?: string | null
  onSubmit?: (message: string, attachments?: File[]) => void | Promise<void>
  pendingActionRequests?: PendingActionRequest[]
  planningState?: PlanningState
  onSkipPlanning?: () => void | Promise<void>
  skipPlanningBusy?: boolean
  onRespondHumanInput?: (response: HumanInputComposerResponse | HumanInputComposerBatchResponse) => Promise<void>
  onDismissHumanInput?: (requestId: string) => Promise<void>
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
      smsContactPermissionAttested?: boolean
    }>
  ) => Promise<void>
  onViewAllContactRequests?: () => void
  disabled?: boolean
  disabledReason?: string | null
  autoFocus?: boolean
  // Key that triggers re-focus when changed (e.g., agentId for switching agents)
  focusKey?: string | null
  onFocus?: () => void
  // Working panel props
  insightsPanelExpandedPreference?: boolean | null
  onInsightsPanelExpandedPreferenceChange?: (expanded: boolean) => void
  agentFirstName?: string
  isProcessing?: boolean
  processingTasks?: ProcessingWebTask[]
  insights?: InsightEvent[]
  insightsLoading?: boolean
  currentInsightIndex?: number
  onDismissInsight?: (insightId: string) => void
  onInsightIndexChange?: (index: number) => void
  onPauseChange?: (paused: boolean) => void
  isInsightsPaused?: boolean
  onOpenUsage?: () => void
  onOpenQuickSettings?: () => void
  usageUrl?: string | null
  hideInsightsPanel?: boolean
  intelligenceConfig?: LlmIntelligenceConfig | null
  intelligenceTier?: string | null
  onIntelligenceChange?: (tier: string) => Promise<boolean>
  allowLockedIntelligenceSelection?: boolean
  intelligenceBusy?: boolean
  intelligenceError?: string | null
  onOpenTaskPacks?: () => void
  canManageAgent?: boolean
  onStopProcessing?: () => void | Promise<void>
  stopProcessingBusy?: boolean
  stopProcessingRequested?: boolean
  submitError?: string | null
  showSubmitErrorUpgrade?: boolean
  maxAttachmentBytes?: number | null
  pipedreamAppsSettingsUrl?: string | null
  pipedreamAppSearchUrl?: string | null
  nativeIntegrationsUrl?: string | null
  googleSheetsDriveTabEnabled?: boolean
  apolloNativeTabEnabled?: boolean
}

export const AgentComposer = memo(function AgentComposer({
  agentId = null,
  agentName = null,
  onSubmit,
  pendingActionRequests = [],
  planningState = 'skipped',
  onSkipPlanning,
  skipPlanningBusy = false,
  onRespondHumanInput,
  onDismissHumanInput,
  onResolveSpawnRequest,
  onFulfillRequestedSecrets,
  onRemoveRequestedSecrets,
  onResolveContactRequests,
  onViewAllContactRequests,
  disabled = false,
  disabledReason = null,
  autoFocus = false,
  focusKey,
  onFocus,
  insightsPanelExpandedPreference = null,
  onInsightsPanelExpandedPreferenceChange,
  agentFirstName = 'Agent',
  isProcessing = false,
  processingTasks = [],
  insights = [],
  insightsLoading = false,
  currentInsightIndex = 0,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange,
  isInsightsPaused = false,
  onOpenUsage,
  onOpenQuickSettings,
  usageUrl = '/app/usage',
  hideInsightsPanel = false,
  intelligenceConfig = null,
  intelligenceTier = null,
  onIntelligenceChange,
  allowLockedIntelligenceSelection = false,
  intelligenceBusy = false,
  intelligenceError = null,
  onOpenTaskPacks,
  canManageAgent = true,
  onStopProcessing,
  stopProcessingBusy = false,
  stopProcessingRequested = false,
  submitError = null,
  showSubmitErrorUpgrade = false,
  maxAttachmentBytes = null,
  pipedreamAppsSettingsUrl = null,
  pipedreamAppSearchUrl = null,
  nativeIntegrationsUrl = null,
  googleSheetsDriveTabEnabled = false,
  apolloNativeTabEnabled = false,
}: AgentComposerProps) {
  const [body, setBody] = useState('')
  const [attachments, setAttachments] = useState<File[]>([])
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [isDragActive, setIsDragActive] = useState(false)
  const [activePendingActionId, setActivePendingActionId] = useState<string | null>(null)
  const [activeHumanInputRequestId, setActiveHumanInputRequestId] = useState<string | null>(null)
  const [busyHumanInputRequestId, setBusyHumanInputRequestId] = useState<string | null>(null)
  const [draftHumanInputResponses, setDraftHumanInputResponses] = useState<Record<string, HumanInputComposerResponse>>({})
  const draftHumanInputResponsesRef = useRef<Record<string, HumanInputComposerResponse>>({})
  const [autoWorkingExpanded, setAutoWorkingExpanded] = useState(true)
  const [pendingActionsForceExpanded, setPendingActionsForceExpanded] = useState(() => pendingActionRequests.length > 0)
  const { isProprietaryMode, openUpgradeModal, ensureAuthenticated } = useSubscriptionStore()
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const focusScrollTimeoutRef = useRef<number | null>(null)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const dragCounter = useRef(0)
  const [activeWorkingTabId, setActiveWorkingTabId] = useState<string | null>(null)
  const nativeAutoSwitchStateRef = useRef<Partial<Record<NativeWorkingTabKind, {
    key: string
    previousEnabled: boolean
  }>>>({})

  // Countdown timer state for auto-rotation indicator
  const [countdownProgress, setCountdownProgress] = useState(0)
  const countdownIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const lastRotationTimeRef = useRef<number>(Date.now())
  const feedbackMessage = disabledReason || attachmentError || submitError
  const showSubmitErrorAlert = Boolean((attachmentError || submitError) && !disabledReason)
  const seenHumanInputBatchAnalyticsRef = useRef<Set<string>>(new Set())

  // Track previous processing state for auto-expand/collapse
  const wasProcessingRef = useRef(isProcessing)
  const isProcessingRef = useRef(isProcessing)
  const hadPendingActionsRef = useRef(false)
  const baseWorkingExpanded = insightsPanelExpandedPreference ?? autoWorkingExpanded
  const hasPendingActions = pendingActionRequests.length > 0
  const resolvedWorkingExpanded = pendingActionsForceExpanded || baseWorkingExpanded

  useEffect(() => {
    isProcessingRef.current = isProcessing
  }, [isProcessing])

  // Auto-expand when processing starts, auto-collapse when it ends
  useEffect(() => {
    if (insightsPanelExpandedPreference === null) {
      if (!wasProcessingRef.current && isProcessing) {
        // Processing just started - auto-expand
        setAutoWorkingExpanded(true)
      } else if (wasProcessingRef.current && !isProcessing) {
        // Processing just ended - auto-collapse
        setAutoWorkingExpanded(false)
      }
    }
    wasProcessingRef.current = isProcessing
  }, [insightsPanelExpandedPreference, isProcessing])

  useEffect(() => {
    if (hasPendingActions && !hadPendingActionsRef.current) {
      setPendingActionsForceExpanded(true)
    } else if (!hasPendingActions && hadPendingActionsRef.current) {
      setPendingActionsForceExpanded(false)
    }
    hadPendingActionsRef.current = hasPendingActions
  }, [hasPendingActions])

  const MAX_COMPOSER_HEIGHT = 320

  const showIntelligenceSelector = Boolean(intelligenceConfig && intelligenceTier && onIntelligenceChange)
  const hasPipedreamApps = Boolean(pipedreamAppsSettingsUrl && pipedreamAppSearchUrl)
  const showAppsControl = Boolean(canManageAgent && agentId && (hasPipedreamApps || nativeIntegrationsUrl))
  const isPlanningMode = planningState === 'planning'
  const isStopping = Boolean(isProcessing && stopProcessingRequested)
  const showStopProcessing = Boolean(isProcessing && !isPlanningMode && !isStopping && agentId && canManageAgent && onStopProcessing)
  const requiresMessageBody = agentId === null
  const handleIntelligenceUpsell = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    if (isProprietaryMode) {
      openUpgradeModal('intelligence_selector')
      return
    }
    if (intelligenceConfig?.upgradeUrl) {
      track(AnalyticsEvent.UPGRADE_CHECKOUT_REDIRECTED, {
        source: 'intelligence_selector',
        target: 'upgrade_url',
      })
      window.open(appendReturnTo(intelligenceConfig.upgradeUrl), '_top')
    }
  }, [ensureAuthenticated, intelligenceConfig?.upgradeUrl, isProprietaryMode, openUpgradeModal])

  const handleSubmitErrorUpgrade = useCallback(async () => {
    const authenticated = await ensureAuthenticated()
    if (!authenticated) {
      return
    }
    openUpgradeModal('agent_limit_error')
  }, [ensureAuthenticated, openUpgradeModal])

  // Insight carousel logic
  const totalInsights = insights.length
  const hasMultipleInsights = totalInsights > 1
  const currentInsight = insights[currentInsightIndex % Math.max(1, totalInsights)] ?? null
  const hasInsights = totalInsights > 0
  const googleDriveTabAvailable = Boolean(googleSheetsDriveTabEnabled && canManageAgent)
  const apolloTabAvailable = Boolean(apolloNativeTabEnabled && canManageAgent)
  const nativeTabAvailability = useMemo(
    () => [
      { kind: 'google_drive', available: googleDriveTabAvailable },
      { kind: 'apollo', available: apolloTabAvailable },
    ] as const,
    [apolloTabAvailable, googleDriveTabAvailable],
  )
  const currentInsightTabId = currentInsight ? `insight:${currentInsight.insightId}` : null
  const workingTabs = useMemo<WorkingPanelTab[]>(() => {
    const insightTabs = insights.map((insight, index): WorkingPanelTab => ({
      id: `insight:${insight.insightId}`,
      kind: 'insight',
      insight,
      insightIndex: index,
    }))
    const nativeTabs = nativeTabAvailability
      .filter(({ available }) => available)
      .map(({ kind }): WorkingPanelTab => ({ id: kind, kind }))
    return [
      ...insightTabs,
      ...nativeTabs,
    ]
  }, [insights, nativeTabAvailability])
  const workingTabIds = useMemo(() => new Set(workingTabs.map((tab) => tab.id)), [workingTabs])
  const effectiveWorkingTabId = (
    activeWorkingTabId && workingTabIds.has(activeWorkingTabId)
      ? activeWorkingTabId
      : currentInsightTabId && workingTabIds.has(currentInsightTabId)
        ? currentInsightTabId
        : workingTabs[0]?.id ?? null
  )
  const activeWorkingTab = workingTabs.find((tab) => tab.id === effectiveWorkingTabId) ?? null
  const activeInsightTab = activeWorkingTab?.kind === 'insight' ? activeWorkingTab : null
  const ActiveNativePanel = activeWorkingTab && activeWorkingTab.kind !== 'insight'
    ? NATIVE_WORKING_TAB_CONFIG[activeWorkingTab.kind].panel
    : null
  const visibleInsight = activeInsightTab?.insight ?? null
  const hasWorkingTabs = workingTabs.length > 0
  const isTouchDevice = typeof window !== 'undefined' && ('ontouchstart' in window || navigator.maxTouchPoints > 0)

  const scrollToBottom = useCallback(() => {
    if (!isTouchDevice) return
    // Container scrolling: scroll the timeline-shell, not the window
    const container = document.getElementById('timeline-shell')
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
    }
  }, [isTouchDevice])

  const selectWorkingTab = useCallback((tabId: string) => {
    if (!resolvedWorkingExpanded) {
      if (onInsightsPanelExpandedPreferenceChange) {
        onInsightsPanelExpandedPreferenceChange(true)
      } else {
        setAutoWorkingExpanded(true)
      }
    }
    setActiveWorkingTabId(tabId)
    onPauseChange?.(true)
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [onInsightsPanelExpandedPreferenceChange, onPauseChange, resolvedWorkingExpanded])

  useEffect(() => {
    if (activeWorkingTabId && !workingTabIds.has(activeWorkingTabId)) {
      setActiveWorkingTabId(null)
    }
  }, [activeWorkingTabId, workingTabIds])

  useEffect(() => {
    const key = agentId ?? focusKey ?? 'new-agent'
    let nextAutoSelectedTab: NativeWorkingTabKind | null = null
    for (const { kind, available } of nativeTabAvailability) {
      const previousState = nativeAutoSwitchStateRef.current[kind]
      if (previousState && previousState.key === key && !previousState.previousEnabled && available) {
        nextAutoSelectedTab = kind
      }
      nativeAutoSwitchStateRef.current[kind] = {
        key,
        previousEnabled: available,
      }
    }
    if (nextAutoSelectedTab) {
      selectWorkingTab(nextAutoSelectedTab)
    }
  }, [agentId, focusKey, nativeTabAvailability, selectWorkingTab])

  // Handle tab click - select that insight, expand panel if collapsed, and pause auto-rotation
  const handleTabClick = useCallback((tab: WorkingPanelTab) => {
    selectWorkingTab(tab.id)
    if (tab.kind === 'insight') {
      onInsightIndexChange?.(tab.insightIndex)
    }

    // Track the tab click
    if (tab.kind === 'insight') {
      track(AnalyticsEvent.INSIGHT_TAB_CLICKED + " - " + tab.insight.title, {
        insightType: tab.insight.insightType,
        insightId: tab.insight.insightId,
        title: tab.insight.title,
        tabIndex: tab.insightIndex,
        totalInsights: insights.length,
      })
    } else {
      const title = NATIVE_WORKING_TAB_CONFIG[tab.kind].title
      track(AnalyticsEvent.INSIGHT_TAB_CLICKED + " - " + title, {
        insightType: tab.kind,
        insightId: tab.id,
        title,
        tabIndex: workingTabs.findIndex((candidate) => candidate.id === tab.id),
        totalInsights: insights.length,
      })
    }
  }, [insights.length, onInsightIndexChange, selectWorkingTab, workingTabs])

  // Handle hover - pause auto-rotation
  const handleInsightMouseEnter = useCallback(() => {
    if (hasMultipleInsights) {
      onPauseChange?.(true)
    }
  }, [hasMultipleInsights, onPauseChange])

  const handleInsightMouseLeave = useCallback(() => {
    if (hasMultipleInsights) {
      onPauseChange?.(false)
      lastRotationTimeRef.current = Date.now()
      setCountdownProgress(0)
    }
  }, [hasMultipleInsights, onPauseChange])

  // Handle panel expand/collapse toggle
  const handlePanelToggle = useCallback(() => {
    const newExpanded = !resolvedWorkingExpanded
    if (!newExpanded) {
      setPendingActionsForceExpanded(false)
    }
    if (onInsightsPanelExpandedPreferenceChange) {
      onInsightsPanelExpandedPreferenceChange(newExpanded)
    } else {
      setAutoWorkingExpanded(newExpanded)
    }
    track(AnalyticsEvent.INSIGHT_PANEL_TOGGLED + " - " + (newExpanded ? "Open" : "Close"), {
      expanded: newExpanded,
      hasInsights,
      currentInsightType: currentInsight?.insightType ?? null,
    })
  }, [
    currentInsight?.insightType,
    hasInsights,
    onInsightsPanelExpandedPreferenceChange,
    resolvedWorkingExpanded,
  ])

  // Wrap dismiss handler to track dismissals
  const handleDismissInsight = useCallback((insightId: string) => {
    const dismissedInsight = insights.find((i) => i.insightId === insightId)
    if (dismissedInsight) {
      track(AnalyticsEvent.INSIGHT_DISMISSED, {
        insightType: dismissedInsight.insightType,
        insightId: dismissedInsight.insightId,
      })
    }
    onDismissInsight?.(insightId)
  }, [insights, onDismissInsight])

  // Update countdown progress for the timer indicator (only when processing)
  useEffect(() => {
    if (!hasMultipleInsights || isInsightsPaused || !isProcessing) {
      setCountdownProgress(0)
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current)
        countdownIntervalRef.current = null
      }
      return
    }

    const updateProgress = () => {
      const elapsed = Date.now() - lastRotationTimeRef.current
      const progress = Math.min(100, (elapsed / INSIGHT_TIMING.rotationIntervalMs) * 100)
      setCountdownProgress(progress)
    }

    // Update every 100ms for smooth animation
    countdownIntervalRef.current = setInterval(updateProgress, 100)
    updateProgress()

    return () => {
      if (countdownIntervalRef.current) {
        clearInterval(countdownIntervalRef.current)
        countdownIntervalRef.current = null
      }
    }
  }, [hasMultipleInsights, isInsightsPaused, isProcessing])

  // Reset countdown when insight changes
  useEffect(() => {
    lastRotationTimeRef.current = Date.now()
    setCountdownProgress(0)
  }, [currentInsightIndex])

  useEffect(() => {
    return () => {
      if (focusScrollTimeoutRef.current !== null) {
        window.clearTimeout(focusScrollTimeoutRef.current)
      }
    }
  }, [])

  const adjustTextareaHeight = useCallback(
    (reset = false) => {
      const node = textareaRef.current
      if (!node) return
      if (reset) {
        node.style.height = ''
      }
      node.style.height = 'auto'
      const nextHeight = Math.min(node.scrollHeight, MAX_COMPOSER_HEIGHT)
      node.style.height = `${nextHeight}px`
      node.style.overflowY = node.scrollHeight > MAX_COMPOSER_HEIGHT ? 'auto' : 'hidden'
    },
    [MAX_COMPOSER_HEIGHT],
  )

  useEffect(() => {
    adjustTextareaHeight()
  }, [body, adjustTextareaHeight])

  useEffect(() => {
    adjustTextareaHeight(true)
  }, [adjustTextareaHeight])

  const pendingHumanInputRequests = useMemo(
    () => orderHumanInputRequests(
      pendingActionRequests
        .filter((request) => request.kind === 'human_input')
        .flatMap((request) => request.requests),
    ),
    [pendingActionRequests],
  )

  useEffect(() => {
    const node = textareaRef.current
    if (!node || typeof ResizeObserver === 'undefined') {
      return
    }

    const observer = new ResizeObserver(() => {
      adjustTextareaHeight(true)
    })
    observer.observe(node)

    return () => {
      observer.disconnect()
    }
  }, [adjustTextareaHeight])

  useEffect(() => {
    if (!pendingActionRequests.length) {
      setActivePendingActionId(null)
      return
    }
    const hasActiveAction = pendingActionRequests.some((request) => request.id === activePendingActionId)
    if (!hasActiveAction) {
      setActivePendingActionId(pendingActionRequests[0]?.id ?? null)
    }
  }, [activePendingActionId, pendingActionRequests])

  useEffect(() => {
    if (!pendingHumanInputRequests.length) {
      setActiveHumanInputRequestId(null)
      setBusyHumanInputRequestId(null)
      if (Object.keys(draftHumanInputResponsesRef.current).length > 0) {
        draftHumanInputResponsesRef.current = {}
        setDraftHumanInputResponses({})
      }
      return
    }
    const hasActiveRequest = pendingHumanInputRequests.some((request) => request.id === activeHumanInputRequestId)
    if (!hasActiveRequest) {
      const latestBatchId = pendingHumanInputRequests[0]?.batchId
      const latestBatchRequests = pendingHumanInputRequests
        .filter((request) => request.batchId === latestBatchId)
      setActiveHumanInputRequestId(latestBatchRequests[0]?.id ?? pendingHumanInputRequests[0]?.id ?? null)
    }
  }, [activeHumanInputRequestId, pendingHumanInputRequests])

  useEffect(() => {
    const pendingIds = new Set(pendingHumanInputRequests.map((request) => request.id))
    const currentDrafts = draftHumanInputResponsesRef.current
    const nextEntries = Object.entries(currentDrafts).filter(([requestId]) => pendingIds.has(requestId))
    if (nextEntries.length === Object.keys(currentDrafts).length) {
      return
    }
    const nextDrafts = Object.fromEntries(nextEntries)
    draftHumanInputResponsesRef.current = nextDrafts
    setDraftHumanInputResponses(nextDrafts)
  }, [pendingHumanInputRequests])

  useEffect(() => {
    if (!agentId || !pendingHumanInputRequests.length) {
      return
    }

    const requestsByBatch = new Map<string, PendingHumanInputRequest[]>()
    pendingHumanInputRequests.forEach((request) => {
      const requests = requestsByBatch.get(request.batchId) ?? []
      requests.push(request)
      requestsByBatch.set(request.batchId, requests)
    })

    requestsByBatch.forEach((requests, batchId) => {
      const analyticsKey = `${agentId}:${batchId}`
      if (seenHumanInputBatchAnalyticsRef.current.has(analyticsKey)) {
        return
      }
      seenHumanInputBatchAnalyticsRef.current.add(analyticsKey)
      track(
        AnalyticsEvent.HUMAN_INPUT_PANEL_SHOWN,
        buildHumanInputBatchAnalyticsProperties(agentId, agentName, requests),
      )
    })
  }, [agentId, agentName, pendingHumanInputRequests])

  // Auto-focus the textarea when autoFocus prop is true or when focusKey changes (agent switch)
  useEffect(() => {
    if (!autoFocus) return
    // Use a small delay to ensure the DOM is ready after navigation
    const timer = setTimeout(() => {
      textareaRef.current?.focus()
    }, 100)
    return () => clearTimeout(timer)
  }, [autoFocus, focusKey])

  useEffect(() => {
    const node = shellRef.current
    if (!node || typeof window === 'undefined') return

    const updateComposerHeight = () => {
      const height = node.getBoundingClientRect().height
      document.documentElement.style.setProperty('--composer-height', `${height}px`)
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.setProperty('--composer-height', `${height}px`)
      }
    }

    updateComposerHeight()

    const observer = new ResizeObserver(updateComposerHeight)
    observer.observe(node)

    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--composer-height')
      const jumpButton = document.getElementById('jump-to-latest')
      if (jumpButton) {
        jumpButton.style.removeProperty('--composer-height')
      }
    }
  }, [])

  const activePendingAction =
    pendingActionRequests.find((request) => request.id === activePendingActionId)
    ?? pendingActionRequests[0]
    ?? null
  const activeHumanInputRequest =
    activePendingAction?.kind === 'human_input'
      ? (
        pendingHumanInputRequests.find((request) => request.id === activeHumanInputRequestId)
        ?? activePendingAction.requests[0]
        ?? null
      )
      : null
  const activeHumanInputUsesMainComposer = Boolean(
    activeHumanInputRequest
    && (
      activeHumanInputRequest.inputMode === 'free_text_only'
      || activeHumanInputRequest.options.length === 0
    ),
  )

  const submitShortcutHint = shouldShowSubmitShortcutHint()
    ? `${isMacOS() ? '⌘↵' : 'Ctrl+↵'} to ${activeHumanInputUsesMainComposer ? 'submit' : 'send'}`
    : ''
  const composerPlaceholder = disabledReason || [
    activeHumanInputUsesMainComposer ? 'Type your answer' : 'Message',
    submitShortcutHint,
  ].filter(Boolean).join(' · ')

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }
    const frame = window.requestAnimationFrame(() => {
      adjustTextareaHeight(true)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [
    activePendingAction,
    activeHumanInputRequestId,
    adjustTextareaHeight,
    composerPlaceholder,
    pendingHumanInputRequests.length,
    resolvedWorkingExpanded,
  ])

  const syncDraftHumanInputResponses = useCallback((nextDrafts: Record<string, HumanInputComposerResponse>) => {
    draftHumanInputResponsesRef.current = nextDrafts
    setDraftHumanInputResponses(nextDrafts)
  }, [])

  const getHumanInputBatchRequests = useCallback((batchId: string) => (
    pendingHumanInputRequests
      .filter((candidate) => candidate.batchId === batchId)
  ), [pendingHumanInputRequests])

  const submitHumanInputDrafts = useCallback(async (
    request: PendingHumanInputRequest,
    nextDrafts: Record<string, HumanInputComposerResponse>,
  ) => {
    if (!onRespondHumanInput) {
      return
    }

    const batchRequests = getHumanInputBatchRequests(request.batchId)

    try {
      setBusyHumanInputRequestId(request.id)
      if (batchRequests.length > 1) {
        const responses = batchRequests
          .map((candidate) => buildSubmittedHumanInputResponse(candidate, nextDrafts[candidate.id]))
          .filter((candidate): candidate is HumanInputComposerResponse => Boolean(candidate))
        if (responses.length !== batchRequests.length) {
          syncDraftHumanInputResponses(nextDrafts)
          return
        }
        await onRespondHumanInput({
          batchId: request.batchId,
          responses,
        })
        const remaining = { ...draftHumanInputResponsesRef.current }
        batchRequests.forEach((candidate) => {
          delete remaining[candidate.id]
        })
        syncDraftHumanInputResponses(remaining)
      } else {
        const submittedResponse = buildSubmittedHumanInputResponse(request, nextDrafts[request.id])
        if (!submittedResponse) {
          syncDraftHumanInputResponses(nextDrafts)
          return
        }
        await onRespondHumanInput(submittedResponse)
        const currentDrafts = draftHumanInputResponsesRef.current
        if (!currentDrafts[request.id]) {
          return
        }
        const remaining = { ...currentDrafts }
        delete remaining[request.id]
        syncDraftHumanInputResponses(remaining)
      }
    } finally {
      setBusyHumanInputRequestId(null)
    }
  }, [getHumanInputBatchRequests, onRespondHumanInput, syncDraftHumanInputResponses])

  const handleSelectHumanInputOption = useCallback(async (requestId: string, optionKey: string) => {
    if (disabled || isSending || busyHumanInputRequestId) {
      return
    }

    const request = pendingHumanInputRequests.find((candidate) => candidate.id === requestId)
    if (!request) {
      return
    }

    const currentDrafts = draftHumanInputResponsesRef.current
    const existing = currentDrafts[requestId]
    const nextSelectedOptionKey = existing?.selectedOptionKey === optionKey ? undefined : optionKey
    const nextDraft: HumanInputComposerResponse = {
      ...existing,
      requestId,
      selectedOptionKey: nextSelectedOptionKey,
    }
    const nextDrafts = { ...currentDrafts }
    if (!nextSelectedOptionKey && !nextDraft.freeText?.trim()) {
      delete nextDrafts[requestId]
    } else {
      nextDrafts[requestId] = nextDraft
    }
    syncDraftHumanInputResponses(nextDrafts)

    const optionIndex = request.options.findIndex((candidate) => candidate.key === optionKey)
    if (
      agentId
      && nextSelectedOptionKey === optionKey
      && optionKey !== HUMAN_INPUT_OTHER_OPTION_KEY
      && optionIndex >= 0
    ) {
      const option = request.options[optionIndex]
      track(AnalyticsEvent.HUMAN_INPUT_OPTION_SELECTED, {
        agent_id: agentId,
        batch_id: request.batchId,
        request_id: request.id,
        batch_position: request.batchPosition,
        batch_size: request.batchSize,
        option_key: option.key,
        option_title: option.title,
        option_index: optionIndex + 1,
        option_count: request.options.length,
        active_conversation_channel: request.activeConversationChannel ?? null,
      })
    }

    if (
      !onRespondHumanInput
      || nextSelectedOptionKey === undefined
      || nextSelectedOptionKey === HUMAN_INPUT_OTHER_OPTION_KEY
    ) {
      return
    }

    const batchRequests = getHumanInputBatchRequests(request.batchId)
    if (batchRequests.length <= 1) {
      return
    }

    const hasAllResponses = batchRequests.every((candidate) => (
      hasHumanInputComposerResponse(candidate, nextDrafts[candidate.id])
    ))
    if (!hasAllResponses) {
      return
    }

    await submitHumanInputDrafts(request, nextDrafts)
  }, [
    agentId,
    busyHumanInputRequestId,
    disabled,
    getHumanInputBatchRequests,
    isSending,
    onRespondHumanInput,
    pendingHumanInputRequests,
    submitHumanInputDrafts,
    syncDraftHumanInputResponses,
  ])

  const handleDraftHumanInputFreeTextChange = useCallback((requestId: string, value: string) => {
    const currentDrafts = draftHumanInputResponsesRef.current
    const existing = currentDrafts[requestId]
    const nextDraft = {
      ...existing,
      requestId,
      freeText: value,
    }
    const nextDrafts = { ...currentDrafts }
    if (!nextDraft.selectedOptionKey && !value.trim()) {
      delete nextDrafts[requestId]
    } else {
      nextDrafts[requestId] = nextDraft
    }
    syncDraftHumanInputResponses(nextDrafts)
  }, [syncDraftHumanInputResponses])

  const handleSubmitHumanInputRequest = useCallback(async () => {
    if (!activeHumanInputRequest || !onRespondHumanInput || disabled || isSending || busyHumanInputRequestId) {
      return
    }

    const request = activeHumanInputRequest
    const batchRequests = getHumanInputBatchRequests(request.batchId)
    const currentDrafts = draftHumanInputResponsesRef.current
    const currentDraft = currentDrafts[request.id]
    const currentResponse = buildSubmittedHumanInputResponse(request, currentDraft)
    if (!currentResponse) {
      return
    }

    // Preserve the draft shape during batch completeness checks. Replacing the
    // draft with the submitted payload would drop the "__other__" selection flag,
    // causing inline free-text answers to look unanswered inside multi-question batches.
    const nextDrafts = { ...currentDrafts }

    if (batchRequests.length > 1) {
      const nextUnanswered = batchRequests.find((candidate) => !hasHumanInputComposerResponse(candidate, nextDrafts[candidate.id]))
      if (nextUnanswered) {
        const nextPendingAction = pendingActionRequests.find((candidate) => (
          candidate.kind === 'human_input'
          && candidate.requests.some((pendingRequest) => pendingRequest.id === nextUnanswered.id)
        ))
        syncDraftHumanInputResponses(nextDrafts)
        if (nextPendingAction) {
          setActivePendingActionId(nextPendingAction.id)
        }
        setActiveHumanInputRequestId(nextUnanswered.id)
        return
      }
    }
    await submitHumanInputDrafts(request, nextDrafts)
  }, [
    activeHumanInputRequest,
    busyHumanInputRequestId,
    disabled,
    getHumanInputBatchRequests,
    isSending,
    onRespondHumanInput,
    pendingActionRequests,
    submitHumanInputDrafts,
    syncDraftHumanInputResponses,
  ])

  const handleDismissHumanInputRequest = useCallback(async (requestId: string) => {
    if (!onDismissHumanInput || disabled || isSending || busyHumanInputRequestId) {
      return
    }
    try {
      setBusyHumanInputRequestId(requestId)
      await onDismissHumanInput(requestId)
      const currentDrafts = draftHumanInputResponsesRef.current
      if (!currentDrafts[requestId]) {
        return
      }
      const remaining = { ...currentDrafts }
      delete remaining[requestId]
      syncDraftHumanInputResponses(remaining)
    } finally {
      setBusyHumanInputRequestId(null)
    }
  }, [busyHumanInputRequestId, disabled, isSending, onDismissHumanInput, syncDraftHumanInputResponses])

  useEffect(() => {
    if (!activeHumanInputUsesMainComposer || !activeHumanInputRequest) {
      return
    }
    setBody(draftHumanInputResponsesRef.current[activeHumanInputRequest.id]?.freeText ?? '')
    requestAnimationFrame(() => adjustTextareaHeight(true))
  }, [activeHumanInputRequest?.id, activeHumanInputUsesMainComposer, adjustTextareaHeight])

  const submitFreeTextHumanInputFromComposer = useCallback(async () => {
    if (!activeHumanInputUsesMainComposer || !activeHumanInputRequest || !onRespondHumanInput) {
      return false
    }
    const trimmed = body.trim()
    if (!trimmed || disabled || isSending || busyHumanInputRequestId) {
      return true
    }

    const request = activeHumanInputRequest
    const currentDrafts = draftHumanInputResponsesRef.current
    const nextDrafts = {
      ...currentDrafts,
      [request.id]: {
        ...currentDrafts[request.id],
        requestId: request.id,
        freeText: trimmed,
      },
    }
    const batchRequests = getHumanInputBatchRequests(request.batchId)
    if (batchRequests.length > 1) {
      const nextUnanswered = batchRequests.find((candidate) => !hasHumanInputComposerResponse(candidate, nextDrafts[candidate.id]))
      if (nextUnanswered) {
        syncDraftHumanInputResponses(nextDrafts)
        const nextPendingAction = pendingActionRequests.find((candidate) => (
          candidate.kind === 'human_input'
          && candidate.requests.some((pendingRequest) => pendingRequest.id === nextUnanswered.id)
        ))
        if (nextPendingAction) {
          setActivePendingActionId(nextPendingAction.id)
        }
        setActiveHumanInputRequestId(nextUnanswered.id)
        setBody('')
        requestAnimationFrame(() => adjustTextareaHeight(true))
        return true
      }
    }

    await submitHumanInputDrafts(request, nextDrafts)
    setBody('')
    requestAnimationFrame(() => adjustTextareaHeight(true))
    return true
  }, [
    activeHumanInputRequest,
    activeHumanInputUsesMainComposer,
    adjustTextareaHeight,
    body,
    busyHumanInputRequestId,
    disabled,
    getHumanInputBatchRequests,
    isSending,
    onRespondHumanInput,
    pendingActionRequests,
    submitHumanInputDrafts,
    syncDraftHumanInputResponses,
  ])

  const submitMessage = useCallback(async () => {
    if (await submitFreeTextHumanInputFromComposer()) {
      return
    }
    const trimmed = body.trim()
    const lacksRequiredContent = requiresMessageBody ? !trimmed : !trimmed && attachments.length === 0
    if (lacksRequiredContent || disabled || isSending) {
      return
    }
    const attachmentsSnapshot = attachments.slice()
    if (onSubmit) {
      try {
        setIsSending(true)
        await onSubmit(trimmed, attachmentsSnapshot)
        setBody('')
        setAttachments([])
        setAttachmentError(null)
        if (fileInputRef.current) {
          fileInputRef.current.value = ''
        }
        requestAnimationFrame(() => adjustTextareaHeight(true))
      } catch {
        return
      } finally {
        setIsSending(false)
      }
    } else {
      setBody('')
      setAttachments([])
      setAttachmentError(null)
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }
      requestAnimationFrame(() => adjustTextareaHeight(true))
    }
  }, [
    adjustTextareaHeight,
    attachments,
    body,
    disabled,
    isSending,
    onSubmit,
    requiresMessageBody,
    submitFreeTextHumanInputFromComposer,
  ])

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    await submitMessage()
  }

  const handleKeyDown = async (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) {
      return
    }
    const shouldSend = (event.metaKey || event.ctrlKey) && !event.shiftKey && !event.altKey
    if (!shouldSend) {
      return
    }
    event.preventDefault()
    await submitMessage()
  }

  const addAttachments = useCallback((files: File[]) => {
    if (disabled || isSending) {
      return
    }
    if (!files.length) {
      return
    }
    const acceptedFiles = maxAttachmentBytes
      ? files.filter((file) => file.size <= maxAttachmentBytes)
      : files
    const rejectedFile = maxAttachmentBytes
      ? files.find((file) => file.size > maxAttachmentBytes) ?? null
      : null

    if (rejectedFile && maxAttachmentBytes) {
      setAttachmentError(`"${rejectedFile.name}" is too large. Max file size is ${formatBytes(maxAttachmentBytes)}.`)
    } else {
      setAttachmentError(null)
    }

    if (!acceptedFiles.length) {
      return
    }
    setAttachments((current) => [...current, ...acceptedFiles])
  }, [disabled, isSending, maxAttachmentBytes])

  const handleAttachmentChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    addAttachments(files)
    event.target.value = ''
  }, [addAttachments])

  const handlePaste = useCallback((event: ClipboardEvent<HTMLTextAreaElement>) => {
    const clipboardData = event.clipboardData
    const itemFiles = Array.from(clipboardData.items ?? [])
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file))
    const files = itemFiles.length > 0 ? itemFiles : Array.from(clipboardData.files ?? [])
    if (!files.length) {
      return
    }
    event.preventDefault()
    addAttachments(files)
  }, [addAttachments])

  const handleOpenFilePicker = useCallback(() => {
    if (disabled || isSending) {
      return
    }
    fileInputRef.current?.click()
  }, [disabled, isSending])

  const removeAttachment = useCallback((index: number) => {
    setAttachments((current) => current.filter((_, currentIndex) => currentIndex !== index))
  }, [])

  useEffect(() => {
    const hasFiles = (event: DragEvent) => {
      const types = Array.from(event.dataTransfer?.types ?? [])
      return types.includes('Files')
    }

    const handleDragEnter = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current += 1
      setIsDragActive(true)
    }

    const handleDragOver = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
    }

    const handleDragLeave = (event: DragEvent) => {
      if (!hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = Math.max(0, dragCounter.current - 1)
      if (dragCounter.current === 0) {
        setIsDragActive(false)
      }
    }

    const handleDrop = (event: DragEvent) => {
      if (disabled || isSending || !hasFiles(event)) {
        return
      }
      event.preventDefault()
      dragCounter.current = 0
      setIsDragActive(false)
      const files = Array.from(event.dataTransfer?.files ?? [])
      addAttachments(files)
    }

    window.addEventListener('dragenter', handleDragEnter)
    window.addEventListener('dragover', handleDragOver)
    window.addEventListener('dragleave', handleDragLeave)
    window.addEventListener('drop', handleDrop)

    return () => {
      window.removeEventListener('dragenter', handleDragEnter)
      window.removeEventListener('dragover', handleDragOver)
      window.removeEventListener('dragleave', handleDragLeave)
      window.removeEventListener('drop', handleDrop)
    }
  }, [addAttachments, disabled, isSending])

  const pendingActionCount = pendingActionRequests.reduce((total, action) => total + getPendingActionRequestCount(action), 0)
  const pendingActionNavigationItems = useMemo<PendingActionNavigationItem[]>(() => (
    pendingActionRequests.reduce<PendingActionNavigationItem[]>((items, action) => {
      if (action.kind !== 'human_input') {
        items.push({ actionId: action.id, kind: 'action' })
        return items
      }
      orderHumanInputRequests(action.requests).forEach((request) => items.push({
        actionId: action.id,
        kind: 'human_input',
        requestId: request.id,
      }))
      return items
    }, [])
  ), [pendingActionRequests])
  const activePendingActionItemIndex = Math.max(0, pendingActionNavigationItems.findIndex((item) => (
    activePendingAction?.kind === 'human_input'
      ? item.kind === 'human_input'
        && item.actionId === activePendingAction.id
        && item.requestId === activeHumanInputRequest?.id
      : item.actionId === activePendingAction?.id
  )))
  // Show the panel when processing, when requests need attention, or when contextual tabs can help.
  const showWorkingPanel = !hideInsightsPanel && (isProcessing || isPlanningMode || hasPendingActions || hasWorkingTabs || insightsLoading)
  const taskCount = processingTasks.length
  const skipPlanningDisabled = !canManageAgent || !onSkipPlanning || skipPlanningBusy
  const showHumanInputActionPanel = activePendingAction?.kind === 'human_input' && resolvedWorkingExpanded && !activeHumanInputUsesMainComposer
  const composerSurfaceClassName = `composer-surface${
    showHumanInputActionPanel
      ? showWorkingPanel
        ? ' overflow-hidden rounded-b-[1.25rem]'
        : ' overflow-hidden rounded-[1.25rem]'
      : ''
  }`
  const composerActionsDisabled = disabled || isSending
  const isSubmittingMainComposerHumanInput = activeHumanInputUsesMainComposer && Boolean(busyHumanInputRequestId)
  const sendButtonBusy = isSending || isSubmittingMainComposerHumanInput
  const sendDisabled = composerActionsDisabled
    || stopProcessingBusy
    || (activeHumanInputUsesMainComposer ? !body.trim() || Boolean(busyHumanInputRequestId) : requiresMessageBody ? !body.trim() : !body.trim() && attachments.length === 0)
  const sendTitle = stopProcessingBusy
    ? 'Stopping'
    : disabledReason || (sendButtonBusy ? activeHumanInputUsesMainComposer ? 'Submitting' : 'Sending' : `${activeHumanInputUsesMainComposer ? 'Submit' : 'Send'} (${isMacOS() ? '⌘↵' : 'Ctrl+Enter'})`)
  const renderSkipPlanningButton = (className = 'composer-skip-planning-button') => (
    <button
      type="button"
      className={className}
      onClick={(event) => {
        event.preventDefault()
        event.stopPropagation()
        void onSkipPlanning?.()
      }}
      disabled={skipPlanningDisabled}
      title={canManageAgent ? 'Skip Planning' : 'Only managers can skip planning'}
    >
      {skipPlanningBusy ? 'Skipping...' : 'Skip Planning'}
    </button>
  )
  const handlePendingActionNavigationItemChange = (item: PendingActionNavigationItem | undefined) => {
    if (!item) {
      return
    }
    const leavingMainComposerHumanInput = activeHumanInputUsesMainComposer
      && (
        item.kind !== 'human_input'
        || item.requestId !== activeHumanInputRequest?.id
      )
    setActivePendingActionId(item.actionId)
    if (item.kind === 'human_input') {
      setActiveHumanInputRequestId(item.requestId)
    } else {
      setActiveHumanInputRequestId(null)
    }
    if (leavingMainComposerHumanInput) {
      setBody('')
      requestAnimationFrame(() => adjustTextareaHeight(true))
    }
  }

  const renderComposerUtilityRow = (appsAction: ComposerAppsAction | null = null) => (
    <div className="composer-utility-row">
      <div className="composer-utility-row__leading">
        {activeHumanInputUsesMainComposer && activeHumanInputRequest ? (
          <button
            type="button"
            onClick={() => void handleDismissHumanInputRequest(activeHumanInputRequest.id)}
            disabled={disabled || isSending || Boolean(busyHumanInputRequestId)}
            className="text-sm font-medium text-slate-500 transition hover:text-slate-900 disabled:cursor-wait disabled:opacity-50"
          >
            Dismiss
          </button>
        ) : (
          <ComposerActionMenu
            disabled={composerActionsDisabled}
            onUploadFiles={handleOpenFilePicker}
            appsAction={appsAction}
          />
        )}
      </div>
      <div className="composer-utility-row__actions">
        {showIntelligenceSelector ? (
          <AgentIntelligenceSelector
            config={intelligenceConfig as LlmIntelligenceConfig}
            currentTier={intelligenceTier ?? 'standard'}
            onSelect={(tier) => onIntelligenceChange?.(tier)}
            onUpsell={allowLockedIntelligenceSelection ? undefined : handleIntelligenceUpsell}
            onOpenTaskPacks={onOpenTaskPacks}
            allowLockedSelection={allowLockedIntelligenceSelection}
            disabled={!canManageAgent}
            busy={intelligenceBusy}
            error={intelligenceError}
          />
        ) : null}
        {showStopProcessing ? (
          <button
            type="button"
            className={`composer-send-button composer-send-button--stop${stopProcessingBusy ? ' composer-send-button--stop-busy' : ''}`}
            disabled={stopProcessingBusy}
            title={stopProcessingBusy ? 'Stopping' : 'Stop'}
            aria-label={stopProcessingBusy ? 'Stopping agent' : 'Stop agent'}
            onClick={(event) => {
              event.preventDefault()
              void onStopProcessing?.()
            }}
          >
            <span className="composer-send-button-stop-icon" aria-hidden="true" />
            <span className="sr-only">{stopProcessingBusy ? 'Stopping' : 'Stop'}</span>
          </button>
        ) : (
          <button
            type="submit"
            className="composer-send-button"
            disabled={sendDisabled}
            title={sendTitle}
            aria-label={
              stopProcessingBusy
                ? 'Stopping agent'
                : sendButtonBusy
                  ? activeHumanInputUsesMainComposer ? 'Submitting response' : 'Sending message'
                  : activeHumanInputUsesMainComposer ? 'Submit response' : 'Send message'
            }
          >
            {sendButtonBusy ? (
              <span className="inline-flex items-center justify-center">
                <span
                  className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-white"
                  aria-hidden="true"
                />
                <span className="sr-only">{activeHumanInputUsesMainComposer ? 'Submitting' : 'Sending'}</span>
              </span>
            ) : (
              <>
                <ArrowUp className="h-4 w-4" aria-hidden="true" />
                <span className="sr-only">{activeHumanInputUsesMainComposer ? 'Submit' : 'Send'}</span>
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )

  return (
    <div
      className="composer-shell"
      id="agent-composer-shell"
      ref={shellRef}
      data-processing={isProcessing ? 'true' : 'false'}
      data-expanded={resolvedWorkingExpanded ? 'true' : 'false'}
      data-panel-visible={showWorkingPanel ? 'true' : 'false'}
    >
      <div className={composerSurfaceClassName}>
        {/* Working panel - integrated above input */}
        {showWorkingPanel ? (
          <div
            className="composer-working-panel"
            data-expanded={resolvedWorkingExpanded ? 'true' : 'false'}
            data-has-pending-actions={hasPendingActions ? 'true' : 'false'}
          >
            {/* Header row - clickable to toggle, with tabs and chevron */}
            <div
              className="composer-working-header-row"
              onClick={handlePanelToggle}
              role="button"
              tabIndex={0}
              aria-expanded={resolvedWorkingExpanded}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handlePanelToggle()
                }
              }}
            >
              {hasPendingActions ? (
                <>
                  {isProcessing ? (
                    <span
                      className="composer-working-indicator composer-working-indicator--dots"
                      aria-label={isStopping ? 'stopping' : isPlanningMode ? 'planning' : 'working'}
                    >
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                    </span>
                  ) : (
                    <MessageSquareQuote className="composer-working-indicator" aria-hidden="true" />
                  )}
                  <span className="composer-working-status">
                    <strong>Needs your input</strong>
                  </span>
                  <span className="composer-working-tasks-badge">
                    {pendingActionCount} {pendingActionCount === 1 ? 'request' : 'requests'}
                  </span>
                  {isPlanningMode ? renderSkipPlanningButton() : null}
                  {pendingActionNavigationItems.length > 1 ? (
                    <div
                      className="composer-pending-action-nav"
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <button
                        type="button"
                        className="composer-pending-action-nav__button"
                        onClick={() => handlePendingActionNavigationItemChange(
                          pendingActionNavigationItems[Math.max(0, activePendingActionItemIndex - 1)],
                        )}
                        disabled={disabled || activePendingActionItemIndex === 0}
                        aria-label="Previous pending request"
                      >
                        <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                      </button>
                      <span className="composer-pending-action-nav__count">
                        {activePendingActionItemIndex + 1} of {pendingActionNavigationItems.length}
                      </span>
                      <button
                        type="button"
                        className="composer-pending-action-nav__button"
                        onClick={() => handlePendingActionNavigationItemChange(
                          pendingActionNavigationItems[Math.min(
                            pendingActionNavigationItems.length - 1,
                            activePendingActionItemIndex + 1,
                          )],
                        )}
                        disabled={disabled || activePendingActionItemIndex >= pendingActionNavigationItems.length - 1}
                        aria-label="Next pending request"
                      >
                        <ChevronRight className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </div>
                  ) : null}
                </>
              ) : isProcessing || isPlanningMode ? (
                <>
                  <Sparkles className="composer-working-indicator" aria-hidden="true" />
                  <span className="composer-working-status">
                    <strong>{agentFirstName}</strong> is {isStopping ? 'stopping' : isPlanningMode ? 'planning' : 'working'}
                    <span className="composer-working-ellipsis" aria-label={isPlanningMode ? 'planning' : 'working'}>
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                      <span className="composer-working-dot" />
                    </span>
                  </span>
                  {isPlanningMode ? renderSkipPlanningButton() : null}
                  {taskCount > 0 ? (
                    <span className="composer-working-tasks-badge">
                      {taskCount} {taskCount === 1 ? 'task' : 'tasks'}
                    </span>
                  ) : null}
                </>
              ) : (
                <span className="composer-working-status">
                  <strong>Insights</strong>
                </span>
              )}

              {/* Colored pill tabs in header */}
              {!hasPendingActions && hasWorkingTabs ? (
                <div
                  className="composer-insight-tabs"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  <div className="composer-insight-tabs-scroll">
                    {workingTabs.map((tab) => {
                      const isActive = tab.id === effectiveWorkingTabId
                      const nativeTabConfig = tab.kind === 'insight' ? null : NATIVE_WORKING_TAB_CONFIG[tab.kind]
                      const color = tab.kind === 'insight' ? getInsightTabColor(tab.insight) : DEFAULT_INSIGHT_TAB_COLOR
                      const label = nativeTabConfig?.label ?? (tab.kind === 'insight' ? getInsightTabLabel(tab.insight) : '')
                      const ariaLabel = nativeTabConfig?.ariaLabel
                        ?? (tab.kind === 'insight' ? `View ${tab.insight.insightType.replace('_', ' ')} insight` : undefined)
                      return (
                        <button
                          key={tab.id}
                          type="button"
                          className="composer-insight-tab"
                          data-active={isActive ? 'true' : 'false'}
                          onClick={() => handleTabClick(tab)}
                          aria-label={ariaLabel}
                          style={{
                            '--tab-color': color,
                            '--tab-progress': tab.kind === 'insight' && isActive && !isInsightsPaused && isProcessing ? `${countdownProgress}%` : '0%',
                          } as React.CSSProperties}
                        >
                          <span className="composer-insight-tab-inner" />
                          <span className="composer-insight-tab-icon" aria-hidden="true">
                            {nativeTabConfig?.icon ?? (tab.kind === 'insight' ? getInsightTabIcon(tab.insight) : null)}
                          </span>
                          <span className="composer-insight-tab-label">{label}</span>
                          {tab.kind === 'insight' && isActive && !isInsightsPaused && isProcessing && (
                            <span className="composer-insight-tab-progress" />
                          )}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ) : !hasPendingActions && insightsLoading ? (
                <div className="composer-insight-tabs composer-insight-tabs--loading" aria-hidden="true">
                  <div className="composer-insight-tabs-scroll">
                    <span className="composer-insight-tab-placeholder composer-insight-tab-placeholder--active" />
                    <span className="composer-insight-tab-placeholder" />
                    <span className="composer-insight-tab-placeholder composer-insight-tab-placeholder--short" />
                  </div>
                </div>
              ) : null}

              <span className="composer-working-toggle">
                {resolvedWorkingExpanded ? (
                  <ChevronDown className="h-4 w-4" />
                ) : (
                  <ChevronUp className="h-4 w-4" />
                )}
              </span>
            </div>

            {/* Expanded content */}
            {resolvedWorkingExpanded && (hasPendingActions || hasWorkingTabs || insightsLoading) ? (
              <div
                className="composer-working-content"
                onMouseEnter={handleInsightMouseEnter}
                onMouseLeave={handleInsightMouseLeave}
              >
                {hasPendingActions ? (
                  <div className="composer-working-pending-actions">
                    <PendingActionComposerPanel
                      actions={pendingActionRequests}
                      agentName={agentName ?? agentFirstName}
                      activeActionId={activePendingActionId}
                      disabled={disabled || isSending}
                      activeHumanInputRequestId={activeHumanInputRequestId}
                      draftHumanInputResponses={draftHumanInputResponses}
                      busyHumanInputRequestId={busyHumanInputRequestId}
                      onSelectHumanInputOption={handleSelectHumanInputOption}
                      onDraftHumanInputFreeTextChange={handleDraftHumanInputFreeTextChange}
                      onSubmitHumanInputRequest={handleSubmitHumanInputRequest}
                      onDismissHumanInputRequest={handleDismissHumanInputRequest}
                      onResolveSpawnRequest={onResolveSpawnRequest}
                      onFulfillRequestedSecrets={onFulfillRequestedSecrets}
                      onRemoveRequestedSecrets={onRemoveRequestedSecrets}
                      onResolveContactRequests={onResolveContactRequests}
                      onViewAllContactRequests={onViewAllContactRequests}
                    />
                  </div>
                ) : (
                  <div
                    className="composer-working-insight"
                    data-kind={activeWorkingTab?.kind ?? 'loading'}
                    data-loading={!activeWorkingTab && insightsLoading ? 'true' : 'false'}
                    key={activeWorkingTab?.id ?? 'insights-loading'}
                  >
                    {ActiveNativePanel ? (
                      <ActiveNativePanel nativeIntegrationsUrl={nativeIntegrationsUrl} />
                    ) : visibleInsight ? (
                      <InsightEventCard
                        insight={visibleInsight}
                        onDismiss={handleDismissInsight}
                        onOpenUsage={onOpenUsage}
                        onOpenQuickSettings={onOpenQuickSettings}
                        usageUrl={usageUrl}
                      />
                    ) : (
                      <div className="composer-working-insight-skeleton" aria-hidden="true">
                        <span className="composer-working-insight-skeleton__eyebrow" />
                        <span className="composer-working-insight-skeleton__title" />
                        <span className="composer-working-insight-skeleton__line" />
                        <span className="composer-working-insight-skeleton__line composer-working-insight-skeleton__line--short" />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Main input form */}
        {!showHumanInputActionPanel ? (
          <form className="flex flex-col" onSubmit={handleSubmit}>
            {isDragActive ? (
              <div className="agent-chat-drop-overlay" aria-hidden="true">
                <div className="agent-chat-drop-overlay__panel">Drop files to upload</div>
              </div>
            ) : null}
            <div className="composer-input-surface flex flex-col rounded-[1.25rem] border border-slate-200/60 bg-white px-4 py-3 transition">
              <input
                ref={fileInputRef}
                type="file"
                className="sr-only"
                multiple
                disabled={disabled || isSending}
                onChange={handleAttachmentChange}
              />
              <div className="flex items-start gap-3">
                <textarea
                  name="body"
                  rows={1}
                  required={requiresMessageBody || attachments.length === 0}
                  className="block min-h-[1.8rem] w-full flex-1 resize-none border-0 bg-transparent px-0 py-1 text-[0.9375rem] leading-relaxed tracking-[-0.01em] text-slate-800 placeholder:text-slate-400/80 focus:outline-none focus:ring-0"
                  placeholder={composerPlaceholder}
                  value={body}
                  onChange={(event) => {
                    const nextValue = event.target.value
                    setBody(nextValue)
                    if (activeHumanInputUsesMainComposer && activeHumanInputRequest) {
                      handleDraftHumanInputFreeTextChange(activeHumanInputRequest.id, nextValue)
                    }
                  }}
                  onKeyDown={handleKeyDown}
                  onPaste={handlePaste}
                  onFocus={() => {
                    onFocus?.()
                    if (!isTouchDevice) return
                    if (focusScrollTimeoutRef.current !== null) {
                      window.clearTimeout(focusScrollTimeoutRef.current)
                    }
                    focusScrollTimeoutRef.current = window.setTimeout(scrollToBottom, 60)
                  }}
                  disabled={disabled}
                  ref={textareaRef}
                />
              </div>
              {showAppsControl ? (
                <ComposerPipedreamAppsControl
                  agentId={agentId as string}
                  enablePipedreamApps={hasPipedreamApps}
                  nativeIntegrationsUrl={nativeIntegrationsUrl}
                  disabled={composerActionsDisabled}
                >
                  {(appsAction) => renderComposerUtilityRow(appsAction)}
                </ComposerPipedreamAppsControl>
              ) : (
                renderComposerUtilityRow()
              )}
              {attachments.length > 0 ? (
                <div className="flex flex-wrap gap-2 pt-0.5 text-xs">
                  {attachments.map((file, index) => (
                    <span
                      key={`${file.name}-${file.size}-${file.lastModified}-${index}`}
                      className="inline-flex max-w-full items-center gap-2 rounded-full border border-indigo-100 bg-indigo-50/60 px-3 py-1 text-indigo-700 transition-colors hover:bg-indigo-50"
                    >
                      <span className="max-w-[160px] truncate font-medium" title={file.name}>
                        {file.name}
                      </span>
                      <button
                        type="button"
                        className="-mr-0.5 inline-flex items-center justify-center rounded-full p-0.5 text-indigo-400 transition-colors hover:bg-indigo-100 hover:text-indigo-600"
                        onClick={() => removeAttachment(index)}
                        disabled={disabled || isSending}
                        aria-label={`Remove ${file.name}`}
                      >
                        <X className="h-3 w-3" aria-hidden="true" />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              {feedbackMessage ? (
                <div
                  className="composer-submit-error"
                  role={showSubmitErrorAlert ? 'alert' : undefined}
                  aria-live={showSubmitErrorAlert ? 'polite' : undefined}
                >
                  <span className="composer-submit-error-text">{feedbackMessage}</span>
                  {!disabledReason && showSubmitErrorUpgrade && isProprietaryMode && canManageAgent ? (
                    <button
                      type="button"
                      className="composer-submit-error-upgrade"
                      onClick={() => void handleSubmitErrorUpgrade()}
                    >
                      Upgrade plan
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </form>
        ) : null}
      </div>
    </div>
  )
})
