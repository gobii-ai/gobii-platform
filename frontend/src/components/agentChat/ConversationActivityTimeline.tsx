import { memo, useMemo } from 'react'
import { Loader2 } from 'lucide-react'
import { MessageEventCard } from './MessageEventCard'
import { TraceEntryList } from './TraceEntryList'
import type { TimelineEvent, MessageEvent, ThinkingEvent } from './types'
import { groupTraceByMessage } from './traceGrouping'

type ConversationActivityTimelineProps = {
  agentFirstName: string
  events: TimelineEvent[]
  initialLoading?: boolean
  agentColorHex?: string
  agentAvatarUrl?: string | null
  viewerUserId?: number | null
  viewerEmail?: string | null
  suppressedThinkingCursor?: string | null
  showTraceColumn?: boolean
  streamingThinkingEvent?: ThinkingEvent | null
}

function isMessageEvent(event: TimelineEvent): event is MessageEvent {
  return event.kind === 'message'
}

export const ConversationActivityTimeline = memo(function ConversationActivityTimeline({
  agentFirstName,
  events,
  initialLoading = false,
  agentColorHex,
  agentAvatarUrl,
  viewerUserId,
  viewerEmail,
  suppressedThinkingCursor,
  showTraceColumn = true,
  streamingThinkingEvent = null,
}: ConversationActivityTimelineProps) {
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

  const messageEvents = useMemo(() => events.filter(isMessageEvent), [events])
  const grouping = useMemo(() => groupTraceByMessage(events), [events])

  const prelude = grouping.groups.find((g) => g.anchorCursor === null) ?? null
  const traceByCursor = useMemo(() => {
    const map = new Map<string, TimelineEvent[]>()
    for (const group of grouping.groups) {
      if (!group.anchorCursor) continue
      map.set(group.anchorCursor, group.traceEvents)
    }
    return map
  }, [grouping.groups])

  if (!messageEvents.length) {
    // If we have no messages but we do have trace, we still want to render trace in the right column on desktop.
    if (!showTraceColumn || !prelude?.traceEvents?.length) {
      return null
    }
    return (
      <div className="conversation-activity-grid">
        <div className="conversation-activity-grid__row">
          <div className="conversation-activity-grid__message" />
          <div className="conversation-activity-grid__activity">
            <div className="conversation-activity-grid__gutter" aria-hidden="true" />
            <div className="conversation-activity-grid__trace">
              <TraceEntryList traceEvents={prelude.traceEvents} suppressedThinkingCursor={suppressedThinkingCursor} />
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="conversation-activity-grid">
      {messageEvents.map((event, index) => {
        const baseTrace = traceByCursor.get(event.cursor) ?? []
        const traceEvents = index === 0 && prelude?.traceEvents?.length
          ? [...prelude.traceEvents, ...baseTrace]
          : baseTrace
        const includeStreaming = Boolean(streamingThinkingEvent && index === messageEvents.length - 1)
        const finalTraceEvents = includeStreaming ? [...traceEvents, streamingThinkingEvent as ThinkingEvent] : traceEvents
        const hasTrace = showTraceColumn && finalTraceEvents.length > 0

        return (
          <div
            key={event.cursor}
            className="conversation-activity-grid__row"
            data-message-cursor={event.cursor}
          >
            <div className="conversation-activity-grid__message">
              <MessageEventCard
                eventCursor={event.cursor}
                message={event.message}
                agentFirstName={agentFirstName}
                agentColorHex={agentColorHex}
                agentAvatarUrl={agentAvatarUrl}
                viewerUserId={viewerUserId ?? null}
                viewerEmail={viewerEmail ?? null}
              />
            </div>
            {showTraceColumn ? (
              <div className="conversation-activity-grid__activity">
                <div className="conversation-activity-grid__gutter" aria-hidden="true">
                  {hasTrace ? (
                    <svg
                      className="conversation-activity-grid__connector"
                      viewBox="0 0 100 100"
                      preserveAspectRatio="none"
                      aria-hidden="true"
                    >
                      <path
                        className="conversation-activity-grid__connector-line"
                        d="M98 1 H22 Q14 1 14 6 V94 Q14 99 22 99 H98"
                      />
                    </svg>
                  ) : null}
                </div>
                <div className="conversation-activity-grid__trace">
                  <TraceEntryList traceEvents={finalTraceEvents} suppressedThinkingCursor={suppressedThinkingCursor} />
                </div>
              </div>
            ) : null}
          </div>
        )
      })}
    </div>
  )
})
