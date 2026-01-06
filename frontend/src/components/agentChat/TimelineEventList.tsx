import { memo, useCallback } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { ToolDetailProvider } from './tooling/ToolDetailContext'
import { ThinkingBubble } from './ThinkingBubble'
import { KanbanEventCard } from './KanbanEventCard'
import type { TimelineEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  thinkingCollapsedByCursor?: Record<string, boolean>
  onToggleThinking?: (cursor: string) => void
}

export const TimelineEventList = memo(function TimelineEventList({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  thinkingCollapsedByCursor,
  onToggleThinking,
}: TimelineEventListProps) {
  const handleToggleThinking = useCallback(
    (cursor: string) => {
      if (onToggleThinking) {
        onToggleThinking(cursor)
      }
    },
    [onToggleThinking],
  )

  if (initialLoading) {
    return (
      <div className="timeline-loading-state flex items-center justify-center gap-3.5 rounded-[1.25rem] border border-indigo-100/80 bg-gradient-to-br from-white via-indigo-50/60 to-purple-50/40 px-7 py-9 shadow-sm">
        <span className="loading-pip" aria-hidden="true" />
        <span className="text-sm font-medium tracking-tight text-indigo-800/75">Loading conversation…</span>
      </div>
    )
  }

  if (!events.length) {
    return <div className="timeline-empty text-center text-sm font-medium tracking-tight text-slate-400/80">No activity yet.</div>
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
        if (event.kind === 'thinking') {
          const collapsed = thinkingCollapsedByCursor?.[event.cursor] ?? true
          return (
            <ThinkingBubble
              key={event.cursor}
              reasoning={event.reasoning || ''}
              isStreaming={false}
              collapsed={collapsed}
              onToggle={() => handleToggleThinking(event.cursor)}
            />
          )
        }
        if (event.kind === 'kanban') {
          return <KanbanEventCard key={event.cursor} event={event} />
        }
        return <ToolClusterCard key={event.cursor} cluster={event} />
      })}
    </ToolDetailProvider>
  )
})
