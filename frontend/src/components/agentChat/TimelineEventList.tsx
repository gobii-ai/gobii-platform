import { Fragment, useMemo } from 'react'
import { MessageEventCard } from './MessageEventCard'
import { ToolClusterCard } from './ToolClusterCard'
import { ToolDetailProvider } from './tooling/ToolDetailContext'
import { ThinkingBubble } from './ThinkingBubble'
import type { TimelineEvent } from './types'

type TimelineEventListProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  thinkingReasoning?: string
  thinkingCollapsed?: boolean
  onToggleThinking?: () => void
}

export function TimelineEventList({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  thinkingReasoning,
  thinkingCollapsed = false,
  onToggleThinking,
}: TimelineEventListProps) {
  const lastAgentMessageIndex = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const event = events[i]
      if (event.kind === 'message' && event.message.isOutbound) {
        return i
      }
    }
    return -1
  }, [events])

  const hasThinking = Boolean(thinkingReasoning?.trim())
  const showThinkingBeforeMessage = hasThinking && lastAgentMessageIndex >= 0 && onToggleThinking

  if (initialLoading) {
    return (
      <div className="timeline-loading-state flex items-center justify-center gap-3 rounded-2xl border border-indigo-100 bg-gradient-to-br from-indigo-50/80 to-purple-50/60 px-6 py-8 shadow-sm">
        <span className="loading-pip" aria-hidden="true" />
        <span className="text-sm font-semibold text-indigo-900/80">Loading conversation…</span>
      </div>
    )
  }

  if (!events.length) {
    return <div className="timeline-empty text-center text-sm text-slate-400">No activity yet.</div>
  }

  return (
    <ToolDetailProvider>
      {events.map((event, index) => {
        const showThinkingHere = showThinkingBeforeMessage && index === lastAgentMessageIndex

        if (event.kind === 'message') {
          return (
            <Fragment key={event.cursor}>
              {showThinkingHere && (
                <ThinkingBubble
                  reasoning={thinkingReasoning || ''}
                  isStreaming={false}
                  collapsed={thinkingCollapsed}
                  onToggle={onToggleThinking}
                />
              )}
              <MessageEventCard
                eventCursor={event.cursor}
                message={event.message}
                agentFirstName={agentFirstName}
                agentColorHex={agentColorHex}
              />
            </Fragment>
          )
        }
        return (
          <Fragment key={event.cursor}>
            {showThinkingHere && (
              <ThinkingBubble
                reasoning={thinkingReasoning || ''}
                isStreaming={false}
                collapsed={thinkingCollapsed}
                onToggle={onToggleThinking}
              />
            )}
            <ToolClusterCard cluster={event} />
          </Fragment>
        )
      })}
    </ToolDetailProvider>
  )
}
