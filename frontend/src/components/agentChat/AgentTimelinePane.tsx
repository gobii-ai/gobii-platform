import { useCallback, useMemo, type Ref } from 'react'
import { Loader2 } from 'lucide-react'
import { TimelineEventItem } from './TimelineEventItem'
import { StreamingReplyCard } from './StreamingReplyCard'
import { StreamingThinkingCard } from './StreamingThinkingCard'
import { TypingIndicator } from './TypingIndicator'
import { HardLimitCalloutCard } from './HardLimitCalloutCard'
import { ContactCapCalloutCard } from './ContactCapCalloutCard'
import { TaskCreditsCalloutCard } from './TaskCreditsCalloutCard'
import { StarterPromptSuggestions, type StarterPrompt } from './StarterPromptSuggestions'
import { TemplateRecommendationCards } from './TemplateRecommendationCards'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { TemplateRecommendation } from '../../api/agentSpawnIntent'
import type { AgentMessage } from '../../types/agentChat'
import type { StatusExpansionTargets } from './statusExpansion'
import { chatActions, selectActiveChatSession } from '../../store/chatSlice'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { selectImmersiveShellViewer } from '../../store/immersiveShellSlice'

function timelineEventKey(event: SimplifiedTimelineItem): string {
  if (event.kind === 'collapsed-group') {
    return `collapsed:${event.cursor}`
  }
  if (event.kind === 'steps' && event.entries.length > 0) {
    return `cluster:${event.entries[0].id}`
  }
  return event.cursor
}

function deriveAgentFirstName(agentName?: string | null): string {
  return agentName?.trim().split(/\s+/)[0] || 'Agent'
}

type AgentTimelinePaneProps = {
  composerDisabled?: boolean
  contactCapOpenPacks?: () => void
  contactCapShowUpgrade?: boolean
  events: SimplifiedTimelineItem[]
  hardLimitShowUpsell?: boolean
  hardLimitUpgradeUrl?: string | null
  hasMoreNewer?: boolean
  hasStreamingContent?: boolean
  hideTypingIndicator?: boolean
  initialLoading?: boolean
  isStreaming?: boolean
  loadingNewer?: boolean
  loadingOlder?: boolean
  onContactCapDismiss?: () => void
  onHardLimitOpenSettings: () => void
  onHardLimitQuickIncrease?: () => void
  onJumpToLatest?: () => void
  onMessageLinkClick?: (href: string) => boolean | void
  onPurchaseSeats?: () => void
  onReportMessage?: (message: AgentMessage) => void
  onRetryMessage?: (message: AgentMessage) => void | Promise<void>
  onStarterPromptDismiss?: () => void
  onStarterPromptsTurnOff?: () => void
  onStarterPromptSelect?: (prompt: StarterPrompt, position: number) => Promise<void>
  onTaskCreditsDismiss?: () => void
  onTaskCreditsOpenPacks?: () => void
  onTemplateRecommendationCreate?: (template: TemplateRecommendation, position: number) => void | Promise<void>
  quickIncreaseBusy?: boolean
  quickIncreaseLabel?: string
  showContactCapCallout?: boolean
  showHardLimitCallout?: boolean
  showJumpButton?: boolean
  showNoSeatsCallout?: boolean
  showProcessingIndicator?: boolean
  showScheduledResumeEvent?: boolean
  showStarterPrompts?: boolean
  showStreamingSlot?: boolean
  showStreamingThinking?: boolean
  showTemplateRecommendations?: boolean
  showTaskCreditsCallout?: boolean
  showTaskCreditsUpgrade?: boolean
  showTypingIndicator?: boolean
  starterPromptCount: number
  starterPromptSubmitting?: boolean
  starterPrompts: StarterPrompt[]
  starterPromptsDisabled?: boolean
  starterPromptsLoading?: boolean
  statusExpansionTargets?: StatusExpansionTargets
  suppressedThinkingCursor?: string | null
  templateRecommendations?: TemplateRecommendation[]
  templateRecommendationSubmittingId?: string | null
  taskCreditsWarningVariant?: 'low' | 'out' | null
  timelineContentRef?: Ref<HTMLDivElement>
  timelineRef?: Ref<HTMLDivElement>
  typingStatusText: string
}

export function AgentTimelinePane({
  composerDisabled = false,
  contactCapOpenPacks,
  contactCapShowUpgrade = false,
  events,
  hardLimitShowUpsell = false,
  hardLimitUpgradeUrl = null,
  hasMoreNewer = false,
  hasStreamingContent = false,
  hideTypingIndicator = false,
  initialLoading = false,
  isStreaming = false,
  loadingNewer = false,
  loadingOlder = false,
  onContactCapDismiss,
  onHardLimitOpenSettings,
  onHardLimitQuickIncrease,
  onJumpToLatest,
  onMessageLinkClick,
  onPurchaseSeats,
  onReportMessage,
  onRetryMessage,
  onStarterPromptDismiss,
  onStarterPromptsTurnOff,
  onStarterPromptSelect,
  onTaskCreditsDismiss,
  onTaskCreditsOpenPacks,
  onTemplateRecommendationCreate,
  quickIncreaseBusy = false,
  quickIncreaseLabel,
  showContactCapCallout = false,
  showHardLimitCallout = false,
  showJumpButton = false,
  showNoSeatsCallout = false,
  showProcessingIndicator = false,
  showStarterPrompts = false,
  showStreamingSlot = false,
  showStreamingThinking = false,
  showTemplateRecommendations = false,
  showTaskCreditsCallout = false,
  showTaskCreditsUpgrade = false,
  showTypingIndicator = false,
  starterPromptCount,
  starterPromptSubmitting = false,
  starterPrompts,
  starterPromptsDisabled = false,
  starterPromptsLoading = false,
  statusExpansionTargets,
  suppressedThinkingCursor = null,
  templateRecommendations = [],
  templateRecommendationSubmittingId = null,
  taskCreditsWarningVariant = null,
  timelineContentRef,
  timelineRef,
  typingStatusText,
}: AgentTimelinePaneProps) {
  const dispatch = useAppDispatch()
  const activeSession = useAppSelector(selectActiveChatSession)
  const agentName = activeSession.identity.agentName
  const agentAvatarUrl = activeSession.identity.agentAvatarUrl
  const animateCursors = useMemo(
    () => new Set(Object.keys(activeSession.timelineUi.realtimeEventCursorIds)),
    [activeSession.timelineUi.realtimeEventCursorIds],
  )
  const autoScrollPinned = activeSession.timelineUi.autoScrollPinned
  const hasUnseenActivity = activeSession.timelineUi.hasUnseenActivity
  const onIncomingAnimationConsumed = useCallback(
    (cursor: string) => dispatch(chatActions.realtimeEventCursorConsumed(cursor)),
    [dispatch],
  )
  const streaming = activeSession.stream.streaming
  const viewer = useAppSelector(selectImmersiveShellViewer)
  const agentFirstName = deriveAgentFirstName(agentName)
  const viewerEmail = viewer.email
  const viewerUserId = viewer.userId

  const lastRenderedIndex = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      if (events[index].kind !== 'plan' && events[index].kind !== 'kanban') {
        return index
      }
    }
    return -1
  }, [events])

  return (
    <>
      <div className="agent-chat-timeline-region">
        <div ref={timelineRef} id="timeline-shell" data-scroll-pinned={autoScrollPinned ? 'true' : 'false'}>
          <div id="timeline-spacer" aria-hidden="true" />
          <div id="timeline-inner">
            <div ref={timelineContentRef} id="timeline-events" className="flex flex-col" data-has-jump-button={showJumpButton ? 'true' : 'false'} data-has-working-panel={showProcessingIndicator ? 'true' : 'false'}>
              {loadingOlder ? (
                <div className="timeline-load-control" data-side="older" data-state="loading">
                  <div className="timeline-load-button" role="status">
                    <span className="timeline-load-indicator" data-loading="true" aria-hidden="true" />
                    <span className="timeline-load-label">Loading…</span>
                  </div>
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
              ) : events.map((event, index) => {
                if (event.kind === 'plan' || event.kind === 'kanban') {
                  return null
                }
                return (
                  <div key={timelineEventKey(event)} data-timeline-item="true">
                    <TimelineEventItem
                      event={event}
                      isLatestEvent={index === lastRenderedIndex}
                      agentFirstName={agentFirstName}
                      agentAvatarUrl={agentAvatarUrl}
                      viewerUserId={viewerUserId ?? null}
                      viewerEmail={viewerEmail ?? null}
                      suppressedThinkingCursor={suppressedThinkingCursor}
                      statusExpansionTargets={statusExpansionTargets}
                      animateIncoming={animateCursors?.has(event.cursor) ?? false}
                      onIncomingAnimationConsumed={onIncomingAnimationConsumed}
                      onMessageLinkClick={onMessageLinkClick}
                      onReportMessage={onReportMessage}
                      onRetryMessage={onRetryMessage}
                    />
                  </div>
                )
              })}
              {showHardLimitCallout ? (
                <HardLimitCalloutCard
                  onOpenSettings={onHardLimitOpenSettings}
                  onQuickIncrease={onHardLimitQuickIncrease}
                  quickIncreaseLabel={quickIncreaseLabel}
                  quickIncreaseBusy={quickIncreaseBusy}
                  upgradeUrl={hardLimitUpgradeUrl}
                  showUpsell={hardLimitShowUpsell}
                />
              ) : null}
              {showNoSeatsCallout ? (
                <TaskCreditsCalloutCard
                  billingIssue="no_org_seats"
                  onPurchaseSeats={onPurchaseSeats}
                  variant="out"
                />
              ) : showTaskCreditsCallout ? (
                <TaskCreditsCalloutCard
                  onOpenPacks={onTaskCreditsOpenPacks}
                  showUpgrade={showTaskCreditsUpgrade}
                  onDismiss={onTaskCreditsDismiss}
                  variant={taskCreditsWarningVariant === 'out' ? 'out' : 'low'}
                />
              ) : null}
              {showContactCapCallout ? (
                <ContactCapCalloutCard
                  onOpenPacks={contactCapOpenPacks}
                  showUpgrade={contactCapShowUpgrade}
                  onDismiss={onContactCapDismiss}
                />
              ) : null}
              {showStarterPrompts ? (
                <StarterPromptSuggestions
                  prompts={starterPrompts}
                  loading={starterPromptsLoading}
                  loadingCount={starterPromptCount}
                  disabled={starterPromptSubmitting || starterPromptsDisabled || composerDisabled}
                  onDismiss={onStarterPromptDismiss}
                  onTurnOff={onStarterPromptsTurnOff}
                  onSelect={onStarterPromptSelect}
                />
              ) : null}
              {showTemplateRecommendations ? (
                <TemplateRecommendationCards
                  recommendations={templateRecommendations}
                  onCreate={onTemplateRecommendationCreate}
                  submittingTemplateId={templateRecommendationSubmittingId}
                />
              ) : null}

              {showStreamingThinking ? (
                <StreamingThinkingCard
                  cursor={streaming?.cursor ?? streaming?.streamId ?? 'streaming-thinking'}
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
                      isStreaming={isStreaming}
                      onLinkClick={onMessageLinkClick}
                    />
                  ) : null}
                </div>
              ) : null}

              {showTypingIndicator ? (
                <TypingIndicator
                  statusText={typingStatusText}
                  agentAvatarUrl={agentAvatarUrl}
                  agentFirstName={agentFirstName}
                  hidden={hideTypingIndicator}
                />
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
      </div>

      <button
        id="jump-to-latest"
        className="agent-chat-button jump-to-latest"
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
    </>
  )
}
