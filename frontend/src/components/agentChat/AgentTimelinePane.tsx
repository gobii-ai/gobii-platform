import type { Ref } from 'react'
import { Loader2 } from 'lucide-react'
import { TimelineEventItem } from './TimelineEventItem'
import { StreamingReplyCard } from './StreamingReplyCard'
import { StreamingThinkingCard } from './StreamingThinkingCard'
import { TypingIndicator } from './TypingIndicator'
import { ScheduledResumeCard } from './ScheduledResumeCard'
import { HardLimitCalloutCard } from './HardLimitCalloutCard'
import { ContactCapCalloutCard } from './ContactCapCalloutCard'
import { TaskCreditsCalloutCard } from './TaskCreditsCalloutCard'
import { StarterPromptSuggestions, type StarterPrompt } from './StarterPromptSuggestions'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { AgentMessage, StreamState } from '../../types/agentChat'
import type { StatusExpansionTargets } from './statusExpansion'

function timelineEventKey(event: SimplifiedTimelineItem): string {
  if (event.kind === 'collapsed-group') {
    return `collapsed:${event.cursor}`
  }
  if (event.kind === 'steps' && event.entries.length > 0) {
    return `cluster:${event.entries[0].id}`
  }
  return event.cursor
}

type AgentTimelinePaneProps = {
  agentAvatarUrl?: string | null
  agentColorHex?: string | null
  agentFirstName: string
  animateCursors?: Set<string>
  autoScrollPinned: boolean
  composerDisabled?: boolean
  contactCapOpenPacks?: () => void
  contactCapShowUpgrade?: boolean
  events: SimplifiedTimelineItem[]
  hardLimitShowUpsell?: boolean
  hardLimitUpgradeUrl?: string | null
  hasMoreNewer?: boolean
  hasStreamingContent?: boolean
  hasUnseenActivity?: boolean
  hideTypingIndicator?: boolean
  initialLoading?: boolean
  isStreaming?: boolean
  loadingNewer?: boolean
  loadingOlder?: boolean
  nextScheduledAt?: string | null
  onContactCapDismiss?: () => void
  onHardLimitOpenSettings: () => void
  onHardLimitQuickIncrease?: () => void
  onIncomingAnimationConsumed?: (cursor: string) => void
  onJumpToLatest?: () => void
  onLoadOlder?: () => void
  onMessageCopied?: (message: AgentMessage) => void | Promise<void>
  onMessageLinkClick?: (href: string) => boolean | void
  onPurchaseSeats?: () => void
  onReportMessage?: (message: AgentMessage) => void
  onStarterPromptSelect?: (prompt: StarterPrompt, position: number) => Promise<void>
  onTaskCreditsDismiss?: () => void
  onTaskCreditsOpenPacks?: () => void
  quickIncreaseBusy?: boolean
  quickIncreaseLabel?: string
  showContactCapCallout?: boolean
  showHardLimitCallout?: boolean
  showJumpButton?: boolean
  showNoSeatsCallout?: boolean
  showOlderLoadButton?: boolean
  showProcessingIndicator?: boolean
  showScheduledResumeEvent?: boolean
  showStarterPrompts?: boolean
  showStreamingSlot?: boolean
  showStreamingThinking?: boolean
  showTaskCreditsCallout?: boolean
  showTaskCreditsUpgrade?: boolean
  showTypingIndicator?: boolean
  starterPromptCount: number
  starterPromptSubmitting?: boolean
  starterPrompts: StarterPrompt[]
  starterPromptsDisabled?: boolean
  starterPromptsLoading?: boolean
  statusExpansionTargets?: StatusExpansionTargets
  streaming?: StreamState | null
  suppressedThinkingCursor?: string | null
  taskCreditsWarningVariant?: 'low' | 'out' | null
  timelineContentRef?: Ref<HTMLDivElement>
  timelineRef?: Ref<HTMLDivElement>
  typingStatusText: string
  viewerEmail?: string | null
  viewerUserId?: number | null
}

export function AgentTimelinePane({
  agentAvatarUrl,
  agentColorHex,
  agentFirstName,
  animateCursors,
  autoScrollPinned,
  composerDisabled = false,
  contactCapOpenPacks,
  contactCapShowUpgrade = false,
  events,
  hardLimitShowUpsell = false,
  hardLimitUpgradeUrl = null,
  hasMoreNewer = false,
  hasStreamingContent = false,
  hasUnseenActivity = false,
  hideTypingIndicator = false,
  initialLoading = false,
  isStreaming = false,
  loadingNewer = false,
  loadingOlder = false,
  nextScheduledAt = null,
  onContactCapDismiss,
  onHardLimitOpenSettings,
  onHardLimitQuickIncrease,
  onIncomingAnimationConsumed,
  onJumpToLatest,
  onLoadOlder,
  onMessageCopied,
  onMessageLinkClick,
  onPurchaseSeats,
  onReportMessage,
  onStarterPromptSelect,
  onTaskCreditsDismiss,
  onTaskCreditsOpenPacks,
  quickIncreaseBusy = false,
  quickIncreaseLabel,
  showContactCapCallout = false,
  showHardLimitCallout = false,
  showJumpButton = false,
  showNoSeatsCallout = false,
  showOlderLoadButton = false,
  showProcessingIndicator = false,
  showScheduledResumeEvent = false,
  showStarterPrompts = false,
  showStreamingSlot = false,
  showStreamingThinking = false,
  showTaskCreditsCallout = false,
  showTaskCreditsUpgrade = false,
  showTypingIndicator = false,
  starterPromptCount,
  starterPromptSubmitting = false,
  starterPrompts,
  starterPromptsDisabled = false,
  starterPromptsLoading = false,
  statusExpansionTargets,
  streaming,
  suppressedThinkingCursor = null,
  taskCreditsWarningVariant = null,
  timelineContentRef,
  timelineRef,
  typingStatusText,
  viewerEmail,
  viewerUserId,
}: AgentTimelinePaneProps) {
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
              ) : events.map((event, index) => {
                if (event.kind === 'plan' || event.kind === 'kanban') {
                  return null
                }
                return (
                  <div key={timelineEventKey(event)} data-timeline-item="true">
                    <TimelineEventItem
                      event={event}
                      isLatestEvent={index === events.length - 1}
                      agentFirstName={agentFirstName}
                      agentColorHex={agentColorHex || undefined}
                      agentAvatarUrl={agentAvatarUrl}
                      viewerUserId={viewerUserId ?? null}
                      viewerEmail={viewerEmail ?? null}
                      suppressedThinkingCursor={suppressedThinkingCursor}
                      statusExpansionTargets={statusExpansionTargets}
                      animateIncoming={animateCursors?.has(event.cursor) ?? false}
                      onIncomingAnimationConsumed={onIncomingAnimationConsumed}
                      onMessageLinkClick={onMessageLinkClick}
                      onMessageCopied={onMessageCopied}
                      onReportMessage={onReportMessage}
                    />
                  </div>
                )
              })}
              {showScheduledResumeEvent ? (
                <ScheduledResumeCard nextScheduledAt={nextScheduledAt} />
              ) : null}
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
                  onSelect={onStarterPromptSelect}
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
                      onLinkClick={onMessageLinkClick}
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
    </>
  )
}
