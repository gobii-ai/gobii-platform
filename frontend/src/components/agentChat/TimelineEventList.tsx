import { MessageEventCard } from './MessageEventCard'
import { StreamEventCard } from './StreamEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { ToolDetailProvider } from './tooling/ToolDetailContext'
import type { StreamState } from '../../types/agentChat'
import type { TimelineEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  streaming?: StreamState | null
}

export function TimelineEventList({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  streaming,
}: TimelineEventListProps) {
  if (initialLoading) {
    return (
      <div className="timeline-loading-state flex items-center justify-center gap-3 rounded-2xl border border-indigo-100 bg-gradient-to-br from-indigo-50/80 to-purple-50/60 px-6 py-8 shadow-sm">
        <span className="loading-pip" aria-hidden="true" />
        <span className="text-sm font-semibold text-indigo-900/80">Loading conversation…</span>
      </div>
    )
  }

  if (!events.length && !streaming) {
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
              agentColorHex={agentColorHex}
            />
          )
        }
        return <ToolClusterCard key={event.cursor} cluster={event} />
      })}
      {streaming ? (
        <StreamEventCard stream={streaming} agentFirstName={agentFirstName} />
      ) : null}
    </ToolDetailProvider>
  )
}
