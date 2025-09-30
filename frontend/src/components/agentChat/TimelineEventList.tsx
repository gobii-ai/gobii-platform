import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { ToolDetailProvider } from './tooling/ToolDetailContext'
import type { TimelineEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
}

export function TimelineEventList({ agentFirstName, events, initialLoading = false }: TimelineEventListProps) {
  if (initialLoading) {
    return (
      <div className="timeline-empty flex items-center justify-center gap-2 rounded-xl border border-dashed border-slate-200/80 bg-white/60 px-4 py-6 text-sm text-slate-500">
        <span className="inline-flex h-2.5 w-2.5 animate-pulse rounded-full bg-indigo-400" aria-hidden="true" />
        <span>Loading conversation…</span>
      </div>
    )
  }

  if (!events.length) {
    return <div className="timeline-empty text-center text-sm text-slate-400">No activity yet.</div>
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
            />
          )
        }
        return <ToolClusterCard key={event.cursor} cluster={event} />
      })}
    </ToolDetailProvider>
  )
}
