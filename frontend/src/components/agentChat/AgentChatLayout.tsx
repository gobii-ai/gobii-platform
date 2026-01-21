import type { ReactNode, Ref } from 'react'
import { useState, useCallback, useMemo, useEffect, useRef } from 'react'
import '../../styles/agentChatLegacy.css'
import { AgentComposer } from './AgentComposer'
import { TimelineEventList } from './TimelineEventList'
import { ThinkingBubble } from './ThinkingBubble'
import { StreamingReplyCard } from './StreamingReplyCard'
import { ResponseSkeleton } from './ResponseSkeleton'
import { ChatSidebar } from './ChatSidebar'
import { AgentChatBanner, type ConnectionStatusTone } from './AgentChatBanner'
import { AgentChatSettingsPanel } from './AgentChatSettingsPanel'
import { AgentChatAddonsPanel } from './AgentChatAddonsPanel'
import { HardLimitCalloutCard } from './HardLimitCalloutCard'
import { ContactCapCalloutCard } from './ContactCapCalloutCard'
import { TaskCreditsCalloutCard } from './TaskCreditsCalloutCard'
import type { AgentChatContextSwitcherData } from './AgentChatContextSwitcher'
import type { AgentTimelineProps } from './types'
import type { ProcessingWebTask, StreamState, KanbanBoardSnapshot } from '../../types/agentChat'
import type { InsightEvent } from '../../types/insight'
import type { AgentRosterEntry } from '../../types/agentRoster'
import type { PlanTier } from '../../stores/subscriptionStore'
import { buildAgentComposerPalette } from '../../util/color'
import type { DailyCreditsInfo, DailyCreditsStatus, DailyCreditsUpdatePayload } from '../../types/dailyCredits'
import type { ContactCapInfo, ContactCapStatus, ContactPackOption, TaskPackOption } from '../../types/agentAddons'

type TaskQuotaInfo = {
  available: number
  total: number
  used: number
  used_pct: number
}

type AgentChatLayoutProps = AgentTimelineProps & {
  agentId?: string | null
  agentColorHex?: string | null
  agentAvatarUrl?: string | null
  agentName?: string | null
  agentIsOrgOwned?: boolean
  connectionStatus?: ConnectionStatusTone
  connectionLabel?: string
  connectionDetail?: string | null
  agentRoster?: AgentRosterEntry[]
  activeAgentId?: string | null
  insightsPanelStorageKey?: string | null
  switchingAgentId?: string | null
  rosterLoading?: boolean
  rosterError?: string | null
  onSelectAgent?: (agent: AgentRosterEntry) => void
  onCreateAgent?: () => void
  contextSwitcher?: AgentChatContextSwitcherData
  autoFocusComposer?: boolean
  kanbanSnapshot?: KanbanBoardSnapshot | null
  footer?: ReactNode
  dailyCredits?: DailyCreditsInfo | null
  dailyCreditsStatus?: DailyCreditsStatus | null
  dailyCreditsLoading?: boolean
  dailyCreditsError?: string | null
  onRefreshDailyCredits?: () => void
  onUpdateDailyCredits?: (payload: DailyCreditsUpdatePayload) => Promise<void>
  dailyCreditsUpdating?: boolean
  hardLimitUpgradeUrl?: string | null
  hardLimitShowUpsell?: boolean
  contactCap?: ContactCapInfo | null
  contactCapStatus?: ContactCapStatus | null
  contactPackOptions?: ContactPackOption[]
  contactPackCanManageBilling?: boolean
  contactPackShowUpgrade?: boolean
  contactPackUpdating?: boolean
  onUpdateContactPacks?: (quantities: Record<string, number>) => Promise<void>
  onRefreshAddons?: () => void
  contactPackManageUrl?: string | null
  taskPackOptions?: TaskPackOption[]
  taskPackCanManageBilling?: boolean
  taskPackUpdating?: boolean
  onUpdateTaskPacks?: (quantities: Record<string, number>) => Promise<void>
  taskQuota?: TaskQuotaInfo | null
  showTaskCreditsWarning?: boolean
  showTaskCreditsUpgrade?: boolean
  taskCreditsDismissKey?: string | null
  onLoadOlder?: () => void
  onLoadNewer?: () => void
  onJumpToLatest?: () => void
  onClose?: () => void
  onSendMessage?: (body: string, attachments?: File[]) => void | Promise<void>
  onComposerFocus?: () => void
  isNearBottom?: boolean
  hasUnseenActivity?: boolean
  timelineRef?: Ref<HTMLDivElement>
  loadingOlder?: boolean
  loadingNewer?: boolean
  initialLoading?: boolean
  processingWebTasks?: ProcessingWebTask[]
  processingStartedAt?: number | null
  awaitingResponse?: boolean
  streaming?: StreamState | null
  streamingThinkingCollapsed?: boolean
  onToggleStreamingThinking?: () => void
  insights?: InsightEvent[]
  currentInsightIndex?: number
  onDismissInsight?: (insightId: string) => void
  onInsightIndexChange?: (index: number) => void
  onPauseChange?: (paused: boolean) => void
  isInsightsPaused?: boolean
  onUpgrade?: (plan: PlanTier) => void
}

export function AgentChatLayout({
  agentFirstName,
  events,
  agentId,
  agentColorHex,
  agentAvatarUrl,
  agentName,
  agentIsOrgOwned = false,
  connectionStatus,
  connectionLabel,
  connectionDetail,
  agentRoster,
  activeAgentId,
  insightsPanelStorageKey,
  switchingAgentId,
  rosterLoading,
  rosterError,
  onSelectAgent,
  onCreateAgent,
  contextSwitcher,
  autoFocusComposer = false,
  kanbanSnapshot,
  footer,
  dailyCredits,
  dailyCreditsStatus,
  dailyCreditsLoading = false,
  dailyCreditsError = null,
  onRefreshDailyCredits,
  onUpdateDailyCredits,
  dailyCreditsUpdating = false,
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
  taskQuota = null,
  showTaskCreditsWarning = false,
  showTaskCreditsUpgrade = false,
  taskCreditsDismissKey = null,
  hasMoreOlder,
  hasMoreNewer,
  processingActive,
  processingStartedAt,
  awaitingResponse = false,
  processingWebTasks = [],
  streaming,
  streamingThinkingCollapsed = false,
  onToggleStreamingThinking,
  onLoadOlder,
  onLoadNewer,
  onJumpToLatest,
  onClose,
  onSendMessage,
  onComposerFocus,
  isNearBottom = true,
  hasUnseenActivity = false,
  timelineRef,
  loadingOlder = false,
  loadingNewer = false,
  initialLoading = false,
  insights,
  currentInsightIndex,
  onDismissInsight,
  onInsightIndexChange,
  onPauseChange,
  isInsightsPaused,
  onUpgrade,
}: AgentChatLayoutProps) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [addonsMode, setAddonsMode] = useState<'contacts' | 'tasks' | null>(null)
  const [contactCapDismissed, setContactCapDismissed] = useState(false)
  const [taskCreditsDismissed, setTaskCreditsDismissed] = useState(false)
  const contactCapLimitReachedRef = useRef<boolean | null>(null)
  const taskCreditsWarningRef = useRef<boolean | null>(null)
  const addonsOpen = addonsMode !== null

  const contactCapDismissKey = useMemo(() => {
    return agentId ? `agent-chat-contact-cap-dismissed:${agentId}` : null
  }, [agentId])
  const taskCreditsStorageKey = useMemo(() => {
    return taskCreditsDismissKey ? `agent-chat-task-credits-dismissed:${taskCreditsDismissKey}` : null
  }, [taskCreditsDismissKey])

  const handleSidebarToggle = useCallback((collapsed: boolean) => {
    setSidebarCollapsed(collapsed)
  }, [])

  const handleSettingsOpen = useCallback(() => {
    setSettingsOpen(true)
    onRefreshDailyCredits?.()
  }, [onRefreshDailyCredits])

  const handleSettingsClose = useCallback(() => {
    setSettingsOpen(false)
  }, [])

  const handleAddonsOpen = useCallback((mode: 'contacts' | 'tasks') => {
    setAddonsMode(mode)
    onRefreshAddons?.()
  }, [onRefreshAddons])

  const handleAddonsClose = useCallback(() => {
    setAddonsMode(null)
  }, [])

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
    if (!taskCreditsStorageKey || typeof window === 'undefined') {
      taskCreditsWarningRef.current = showTaskCreditsWarning ?? null
      if (!showTaskCreditsWarning) {
        setTaskCreditsDismissed(false)
      }
      return
    }
    const previousWarning = taskCreditsWarningRef.current
    taskCreditsWarningRef.current = showTaskCreditsWarning ?? null
    if (previousWarning && !showTaskCreditsWarning) {
      window.localStorage.removeItem(taskCreditsStorageKey)
      setTaskCreditsDismissed(false)
    }
  }, [showTaskCreditsWarning, taskCreditsStorageKey])

  const isStreaming = Boolean(streaming && !streaming.done)
  const hasStreamingReasoning = Boolean(streaming?.reasoning?.trim())
  const hasStreamingContent = Boolean(streaming?.content?.trim())
  const suppressedThinkingCursor = streaming?.cursor ?? null
  // Show streaming reasoning while streaming, or briefly after done to allow collapse animation
  // (streaming is cleared when historical thinking event arrives)
  const showStreamingReasoning = hasStreamingReasoning && (isStreaming || streaming?.done)

  // Streaming slot shows while actively streaming content, or briefly after done for reasoning collapse
  const showStreamingSlot = showStreamingReasoning || (hasStreamingContent && isStreaming)

  // Show progress bar whenever processing is active (agent is working)
  // Keep it mounted but hide visually while actively streaming message content or when newer messages are waiting
  const isActivelyStreamingContent = hasStreamingContent && isStreaming
  const shouldRenderResponseSkeleton = Boolean(awaitingResponse || processingActive || isStreaming)
  const hideResponseSkeleton = isActivelyStreamingContent || hasMoreNewer

  const showProcessingIndicator = Boolean((processingActive || isStreaming || awaitingResponse) && !hasMoreNewer)
  const showBottomSentinel = !initialLoading && !hasMoreNewer
  const showLoadOlderButton = !initialLoading && (hasMoreOlder || loadingOlder)
  const showLoadNewerButton = !initialLoading && (hasMoreNewer || loadingNewer)

  const showJumpButton = hasMoreNewer || hasUnseenActivity || !isNearBottom

  const showBanner = Boolean(agentName)
  const composerPalette = useMemo(() => buildAgentComposerPalette(agentColorHex), [agentColorHex])
  const showHardLimitCallout = Boolean(
    (dailyCreditsStatus?.hardLimitReached || dailyCreditsStatus?.hardLimitBlocked) && onUpdateDailyCredits,
  )
  const showContactCapCallout = Boolean(contactCapStatus?.limitReached && !contactCapDismissed)
  const showTaskCreditsCallout = Boolean(showTaskCreditsWarning && !taskCreditsDismissed)

  const handleContactCapDismiss = useCallback(() => {
    if (!contactCapDismissKey || typeof window === 'undefined') {
      setContactCapDismissed(true)
      return
    }
    window.localStorage.setItem(contactCapDismissKey, 'true')
    setContactCapDismissed(true)
  }, [contactCapDismissKey])
  const handleTaskCreditsDismiss = useCallback(() => {
    if (!taskCreditsStorageKey || typeof window === 'undefined') {
      setTaskCreditsDismissed(true)
      return
    }
    window.localStorage.setItem(taskCreditsStorageKey, 'true')
    setTaskCreditsDismissed(true)
  }, [taskCreditsStorageKey])

  const mainClassName = `agent-chat-main${sidebarCollapsed ? ' agent-chat-main--sidebar-collapsed' : ''}`

  return (
    <>
      <ChatSidebar
        defaultCollapsed={true}
        onToggle={handleSidebarToggle}
        agents={agentRoster}
        activeAgentId={activeAgentId}
        switchingAgentId={switchingAgentId}
        loading={rosterLoading}
        errorMessage={rosterError}
        onSelectAgent={onSelectAgent}
        onCreateAgent={onCreateAgent}
        contextSwitcher={contextSwitcher}
      />
      {showBanner && (
        <AgentChatBanner
          agentName={agentName || 'Agent'}
          agentAvatarUrl={agentAvatarUrl}
          agentColorHex={agentColorHex}
          isOrgOwned={agentIsOrgOwned}
          connectionStatus={connectionStatus}
          connectionLabel={connectionLabel}
          connectionDetail={connectionDetail}
          kanbanSnapshot={kanbanSnapshot}
          processingActive={processingActive}
          dailyCreditsStatus={dailyCreditsStatus}
          onSettingsOpen={onUpdateDailyCredits ? handleSettingsOpen : undefined}
          onClose={onClose}
          sidebarCollapsed={sidebarCollapsed}
          onUpgrade={onUpgrade}
        />
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
      />
      <AgentChatAddonsPanel
        open={addonsOpen}
        mode={addonsMode ?? 'contacts'}
        onClose={handleAddonsClose}
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
      <main className={mainClassName}>
        <div
          id="agent-workspace-root"
          style={composerPalette.cssVars}
        >
          {/* Scrollable timeline container */}
          <div ref={timelineRef} id="timeline-shell" data-scroll-pinned={isNearBottom ? 'true' : 'false'}>
            {/* Spacer pushes content to bottom when there's extra space */}
            <div id="timeline-spacer" aria-hidden="true" />
            <div id="timeline-inner">
              <div id="timeline-events" className="flex flex-col gap-3" data-has-jump-button={showJumpButton ? 'true' : 'false'} data-has-working-panel={showProcessingIndicator ? 'true' : 'false'}>
                <div
                  id="timeline-load-older"
                  className="timeline-load-control"
                  data-side="older"
                  data-state={loadingOlder ? 'loading' : hasMoreOlder ? 'has-more' : 'exhausted'}
                  hidden={!showLoadOlderButton}
                >
                  <button
                    type="button"
                    className="timeline-load-button"
                    hidden={!showLoadOlderButton}
                    onClick={onLoadOlder}
                    disabled={loadingOlder}
                  >
                    <span className="timeline-load-indicator" data-loading={loadingOlder ? 'true' : 'false'} aria-hidden="true" />
                    <span className="timeline-load-label">{loadingOlder ? 'Loading…' : 'Load older'}</span>
                  </button>
                </div>

                <div id="timeline-event-list" className="flex flex-col gap-3">
                  <TimelineEventList
                    agentFirstName={agentFirstName}
                    events={events}
                    agentColorHex={agentColorHex || undefined}
                    initialLoading={initialLoading}
                    suppressedThinkingCursor={suppressedThinkingCursor}
                  />
                </div>
                {showHardLimitCallout ? (
                  <HardLimitCalloutCard
                    onOpenSettings={handleSettingsOpen}
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

                {showStreamingSlot && !hasMoreNewer ? (
                  <div id="streaming-response-slot" className="streaming-response-slot flex flex-col gap-3">
                    {showStreamingReasoning && onToggleStreamingThinking ? (
                      <ThinkingBubble
                        reasoning={streaming?.reasoning || ''}
                        isStreaming={isStreaming}
                        collapsed={streamingThinkingCollapsed}
                        onToggle={onToggleStreamingThinking}
                      />
                    ) : null}
                    {hasStreamingContent ? (
                      <StreamingReplyCard
                        content={streaming?.content || ''}
                        agentFirstName={agentFirstName}
                        isStreaming={isStreaming}
                      />
                    ) : null}
                  </div>
                ) : null}

                {shouldRenderResponseSkeleton ? (
                  <ResponseSkeleton startTime={processingStartedAt} hidden={hideResponseSkeleton} />
                ) : null}

                {showBottomSentinel ? (
                  <div id="timeline-bottom-sentinel" className="timeline-bottom-sentinel" aria-hidden="true" />
                ) : null}

                <div
                  id="timeline-load-newer"
                  className="timeline-load-control"
                  data-side="newer"
                  data-state={loadingNewer ? 'loading' : hasMoreNewer ? 'has-more' : 'exhausted'}
                  hidden={!showLoadNewerButton}
                >
                  <button
                    type="button"
                    className="timeline-load-button"
                    hidden={!showLoadNewerButton}
                    onClick={onLoadNewer}
                    disabled={loadingNewer}
                  >
                    <span className="timeline-load-indicator" data-loading={loadingNewer ? 'true' : 'false'} aria-hidden="true" />
                    <span className="timeline-load-label">{loadingNewer ? 'Loading…' : 'Load newer'}</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Jump button positioned within scroll container */}
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
          </div>

          {/* Composer at bottom of flex layout */}
          <AgentComposer
            onSubmit={onSendMessage}
            onFocus={onComposerFocus}
            agentFirstName={agentFirstName}
            isProcessing={showProcessingIndicator}
            processingTasks={processingWebTasks}
            autoFocus={autoFocusComposer}
            focusKey={activeAgentId}
            insightsPanelStorageKey={insightsPanelStorageKey}
            insights={insights}
            currentInsightIndex={currentInsightIndex}
            onDismissInsight={onDismissInsight}
            onInsightIndexChange={onInsightIndexChange}
            onPauseChange={onPauseChange}
            isInsightsPaused={isInsightsPaused}
          />
        </div>
        {footer ? <div className="mt-6 px-4 sm:px-6 lg:px-10">{footer}</div> : null}
      </main>
    </>
  )
}
