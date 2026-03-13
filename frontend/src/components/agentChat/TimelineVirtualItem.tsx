import { memo, useMemo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { KanbanEventCard } from './KanbanEventCard'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { InlineScheduleCard } from './InlineStatusCard'
import type { SimplifiedTimelineItem } from '../../hooks/useSimplifiedTimeline'
import { buildThinkingCluster, flattenTimelineEventsToEntries } from './activityEntryUtils'

type TimelineVirtualItemProps = {
  event: SimplifiedTimelineItem
  isLatestEvent: boolean
  agentFirstName: string
  agentColorHex?: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
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
      />
    )
  }
  if (event.kind === 'thinking') {
    return (
      <ToolClusterCard
        cluster={buildThinkingCluster(event)}
        isLatestEvent={isLatestEvent}
        suppressedThinkingCursor={suppressedThinkingCursor}
      />
    )
  }
  if (event.kind === 'kanban') {
    return <KanbanEventCard event={event} />
  }
  return (
    <ToolClusterCard
      cluster={event}
      isLatestEvent={isLatestEvent}
      suppressedThinkingCursor={suppressedThinkingCursor}
    />
  )
})
