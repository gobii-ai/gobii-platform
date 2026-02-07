import { memo } from 'react'
import { Loader2 } from 'lucide-react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { KanbanEventCard } from './KanbanEventCard'
import type { TimelineEvent, ToolClusterEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
}

export const TimelineEventList = memo(function TimelineEventList({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
}: TimelineEventListProps) {
  if (initialLoading) {
    return (
      <div className="flex items-center justify-center py-10" aria-live="polite" aria-busy="true">
        <div className="flex flex-col items-center gap-3 text-center">
          <Loader2 size={28} className="animate-spin text-blue-600" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-slate-700">Loading conversation…</p>
          </div>
        </div>
      </div>
    )
  }

  if (!events.length) {
    return null
  }

  return (
    <>
      {events.map((event, index) => {
        const isLatestEvent = index === events.length - 1
        if (event.kind === 'message') {
          return (
            <MessageEventCard
              key={event.cursor}
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
              key={event.cursor}
              cluster={cluster}
              isLatestEvent={isLatestEvent}
              suppressedThinkingCursor={suppressedThinkingCursor}
            />
          )
        }
        if (event.kind === 'kanban') {
          return <KanbanEventCard key={event.cursor} event={event} />
        }
        return (
          <ToolClusterCard
            key={event.cursor}
            cluster={event}
            isLatestEvent={isLatestEvent}
            suppressedThinkingCursor={suppressedThinkingCursor}
          />
        )
      })}
    </>
  )
})
