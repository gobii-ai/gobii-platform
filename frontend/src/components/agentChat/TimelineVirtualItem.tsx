import { memo, useMemo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { KanbanEventCard } from './KanbanEventCard'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { InlineScheduleCard } from './InlineStatusCard'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import { buildThinkingCluster, flattenTimelineEventsToEntries } from './activityEntryUtils'
import type { ToolClusterEvent } from '../../types/agentChat'
import type { StatusExpansionTargets } from './statusExpansion'

type TimelineVirtualItemProps = {
  event: SimplifiedTimelineItem
  isLatestEvent: boolean
  agentFirstName: string
  agentColorHex?: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
  statusExpansionTargets?: StatusExpansionTargets
  animateIncoming?: boolean
  onIncomingAnimationConsumed?: (cursor: string) => void
  onMessageLinkClick?: (href: string) => boolean | void
}

export const TimelineVirtualItem = memo(function TimelineVirtualItem({
  event,
  isLatestEvent,
  agentFirstName,
  agentColorHex,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
  statusExpansionTargets,
  animateIncoming = false,
  onIncomingAnimationConsumed,
  onMessageLinkClick,
}: TimelineVirtualItemProps) {
  const collapsedEntries = useMemo(() => {
    if (event.kind !== 'collapsed-group') {
      return []
    }
    return flattenTimelineEventsToEntries(event.events)
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
        agentColorHex={agentColorHex}
        agentAvatarUrl={agentAvatarUrl}
        viewerUserId={viewerUserId ?? null}
        viewerEmail={viewerEmail ?? null}
        onMessageLinkClick={onMessageLinkClick}
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
  if (event.kind === 'kanban' && event.cursor === statusExpansionTargets?.latestKanbanCursor) {
    return <KanbanEventCard event={event} />
  }
  if (event.kind === 'kanban') {
    const cluster: ToolClusterEvent = {
      kind: 'steps',
      cursor: event.cursor,
      entries: [],
      entryCount: 1,
      collapsible: false,
      collapseThreshold: Infinity,
      earliestTimestamp: event.timestamp ?? null,
      latestTimestamp: event.timestamp ?? null,
      kanbanEntries: [event],
    }
    return (
      <ToolClusterCard
        cluster={cluster}
        isLatestEvent={isLatestEvent}
        suppressedThinkingCursor={suppressedThinkingCursor}
        statusExpansionTargets={statusExpansionTargets}
        animateIncoming={animateIncoming}
        onIncomingAnimationConsumed={onIncomingAnimationConsumed}
      />
    )
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
