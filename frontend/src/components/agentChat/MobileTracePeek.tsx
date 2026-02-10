import { memo, useMemo } from 'react'
import { ChevronUp } from 'lucide-react'
import type { TimelineEvent, ThinkingEvent, KanbanEvent, ToolClusterEvent } from './types'
import { transformToolCluster } from './tooling/toolRegistry'
import { ToolIconSlot } from './ToolIconSlot'
import { formatRelativeTimestamp } from '../../util/time'

type MobileTracePeekProps = {
  events: TimelineEvent[]
  suppressedThinkingCursor?: string | null
  onOpen: () => void
}

function wrapThinkingEvent(event: ThinkingEvent): ToolClusterEvent {
  return {
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
}

function wrapKanbanEvent(event: KanbanEvent): ToolClusterEvent {
  return {
    kind: 'steps',
    cursor: event.cursor,
    entries: [],
    entryCount: 1,
    collapsible: false,
    collapseThreshold: 3,
    earliestTimestamp: event.timestamp ?? null,
    latestTimestamp: event.timestamp ?? null,
    kanbanEntries: [event],
  }
}

export const MobileTracePeek = memo(function MobileTracePeek({
  events,
  suppressedThinkingCursor,
  onOpen,
}: MobileTracePeekProps) {
  const recentEntries = useMemo(() => {
    const clusters: ToolClusterEvent[] = []
    for (const event of events) {
      if (event.kind === 'steps') clusters.push(event)
      else if (event.kind === 'thinking') clusters.push(wrapThinkingEvent(event))
      else if (event.kind === 'kanban') clusters.push(wrapKanbanEvent(event))
    }
    if (!clusters.length) return []

    const flattened = clusters
      .map((cluster) => transformToolCluster(cluster, { suppressedThinkingCursor }))
      .flatMap((cluster) => cluster.entries)
      .filter((entry) => Boolean(entry.label))

    return flattened.slice(-2)
  }, [events, suppressedThinkingCursor])

  if (!recentEntries.length) {
    return null
  }

  return (
    <button type="button" className="mobile-trace-peek" onClick={onOpen} aria-label="Open activity">
      <div className="mobile-trace-peek__header">
        <span className="mobile-trace-peek__title">Activity</span>
        <span className="mobile-trace-peek__chevron" aria-hidden="true">
          <ChevronUp size={16} />
        </span>
      </div>
      <div className="mobile-trace-peek__rows">
        {recentEntries.map((entry) => {
          const relative = entry.timestamp ? (formatRelativeTimestamp(entry.timestamp) || entry.timestamp) : null
          return (
            <div key={entry.id} className="mobile-trace-peek__row">
              <span className={`mobile-trace-peek__icon ${entry.iconBgClass} ${entry.iconColorClass}`} aria-hidden="true">
                <ToolIconSlot entry={entry} />
              </span>
              <span className="mobile-trace-peek__label">{entry.label}</span>
              {relative ? <span className="mobile-trace-peek__time">{relative}</span> : null}
            </div>
          )
        })}
      </div>
    </button>
  )
})
