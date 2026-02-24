import { memo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { KanbanEventCard } from './KanbanEventCard'
import type { TimelineEvent, ToolClusterEvent } from './types'

type TimelineVirtualItemProps = {
  event: TimelineEvent
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
    const cluster: ToolClusterEvent = {
      kind: 'steps',
      cursor: event.cursor,
      entries: [],
      entryCount: 1,
      collapsible: false,
      collapseThreshold: 3,
      earliestTimestamp: event.timestamp ?? null,
      latestTimestamp: event.timestamp ?? null,
      thinkingEntries: [event],
    }
    return (
      <ToolClusterCard
        cluster={cluster}
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
