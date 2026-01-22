import { memo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { ToolDetailProvider } from './tooling/ToolDetailContext'
import { KanbanEventCard } from './KanbanEventCard'
import type { TimelineEvent, ToolClusterEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
}

export const TimelineEventList = memo(function TimelineEventList({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
}: TimelineEventListProps) {
  if (initialLoading) {
    return (
      <div className="timeline-loading-state flex items-center justify-center gap-3.5 rounded-[1.25rem] border border-indigo-100/80 bg-gradient-to-br from-white via-indigo-50/60 to-purple-50/40 px-7 py-9 shadow-sm">
        <span className="loading-pip" aria-hidden="true" />
        <span className="text-sm font-medium tracking-tight text-indigo-800/75">Loading conversation…</span>
      </div>
    )
  }

  if (!events.length) {
    return null
  }

  return (
    <ToolDetailProvider>
      {events.map((event) => {
        if (event.kind === 'message') {
          return (
            <MessageEventCard
              key={event.cursor}
              eventCursor={event.cursor}
              message={event.message}
              agentFirstName={agentFirstName}
              agentColorHex={agentColorHex}
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
            suppressedThinkingCursor={suppressedThinkingCursor}
          />
        )
      })}
    </ToolDetailProvider>
  )
})
