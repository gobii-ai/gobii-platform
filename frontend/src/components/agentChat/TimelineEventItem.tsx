import { memo, useMemo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { UserActionEventCard } from './UserActionEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { InlineScheduleCard } from './InlineStatusCard'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import type { AgentMessage } from '../../types/agentChat'
import { buildThinkingCluster, flattenTimelineEventsToEntries } from './activityEntryUtils'
import type { StatusExpansionTargets } from './statusExpansion'

type TimelineEventItemProps = {
  event: SimplifiedTimelineItem
  isLatestEvent: boolean
  agentFirstName: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
  statusExpansionTargets?: StatusExpansionTargets
  animateIncoming?: boolean
  onIncomingAnimationConsumed?: (cursor: string) => void
  onMessageLinkClick?: (href: string) => boolean | void
  onMessageCopied?: (message: AgentMessage) => void | Promise<void>
  onReportMessage?: (message: AgentMessage) => void
  onRetryMessage?: (message: AgentMessage) => void | Promise<void>
}

export const TimelineEventItem = memo(function TimelineEventItem({
  event,
  isLatestEvent,
  agentFirstName,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
  statusExpansionTargets,
  animateIncoming = false,
  onIncomingAnimationConsumed,
  onMessageLinkClick,
  onMessageCopied,
  onReportMessage,
  onRetryMessage,
}: TimelineEventItemProps) {
  const collapsedEntries = useMemo(() => {
    if (event.kind !== 'collapsed-group') {
      return []
    }
    return event.displayEntries ?? flattenTimelineEventsToEntries(event.events)
  }, [event])

  if (event.kind === 'collapsed-group') {
    return <CollapsedActivityCard overlayId={event.cursor} entries={collapsedEntries} label={event.summary.label} subtitle="Collapsed actions" />
  }
  if (event.kind === 'inline-schedule') {
    return <InlineScheduleCard entry={event.entry} />
  }
  if (event.kind === 'message') {
    return (
      <MessageEventCard
        eventCursor={event.cursor}
        message={event.message}
        agentFirstName={agentFirstName}
        agentAvatarUrl={agentAvatarUrl}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        onMessageLinkClick={onMessageLinkClick}
        onMessageCopied={onMessageCopied}
        onReportMessage={onReportMessage}
        onRetryMessage={onRetryMessage}
      />
    )
  }
  if (event.kind === 'user_action') {
    return (
      <UserActionEventCard
        event={event}
        viewerUserId={viewerUserId ?? null}
      />
    )
  }
  if (event.kind === 'thinking') {
    return (
      <ToolClusterCard
        cluster={buildThinkingCluster(event)}
        isLatestEvent={isLatestEvent}
        suppressedThinkingCursor={suppressedThinkingCursor}
        statusExpansionTargets={statusExpansionTargets}
        animateIncoming={animateIncoming}
        onIncomingAnimationConsumed={onIncomingAnimationConsumed}
      />
    )
  }
  if (event.kind === 'plan' || event.kind === 'kanban') {
    return null
  }
  return (
    <ToolClusterCard
      cluster={event}
      isLatestEvent={isLatestEvent}
      suppressedThinkingCursor={suppressedThinkingCursor}
      statusExpansionTargets={statusExpansionTargets}
      animateIncoming={animateIncoming}
      onIncomingAnimationConsumed={onIncomingAnimationConsumed}
    />
  )
})
