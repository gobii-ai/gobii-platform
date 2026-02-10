import { memo, useMemo, useState, useCallback } from 'react'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'
import { ToolIconSlot } from './ToolIconSlot'
import type { TimelineEvent, ToolClusterEvent, ThinkingEvent, KanbanEvent } from './types'
import { transformToolCluster } from './tooling/toolRegistry'
import type { ToolClusterTransform, ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'

type TraceEntryListProps = {
  traceEvents: TimelineEvent[]
  suppressedThinkingCursor?: string | null
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

type FlatTraceEntry = {
  entry: ToolEntryDisplay
  cluster: ToolClusterTransform
}

function deriveRowCaption(entry: ToolEntryDisplay): string | null {
  if (entry.caption) return entry.caption
  if (entry.summary) return entry.summary
  if (typeof entry.result === 'string') {
    const text = entry.result.replace(/\s+/g, ' ').trim()
    return text ? text.slice(0, 120) : null
  }
  return null
}

export const TraceEntryList = memo(function TraceEntryList({ traceEvents, suppressedThinkingCursor }: TraceEntryListProps) {
  const flattened = useMemo<FlatTraceEntry[]>(() => {
    const clusters: ToolClusterEvent[] = []
    for (const event of traceEvents) {
      if (event.kind === 'steps') clusters.push(event)
      else if (event.kind === 'thinking') clusters.push(wrapThinkingEvent(event))
      else if (event.kind === 'kanban') clusters.push(wrapKanbanEvent(event))
    }
    if (!clusters.length) return []

    const out: FlatTraceEntry[] = []
    for (const cluster of clusters) {
      const transformed = transformToolCluster(cluster, { suppressedThinkingCursor })
      for (const entry of transformed.entries) {
        out.push({ entry, cluster: transformed })
      }
    }
    return out
  }, [suppressedThinkingCursor, traceEvents])

  const [overlayOpen, setOverlayOpen] = useState(false)
  const [overlayCluster, setOverlayCluster] = useState<ToolClusterTransform | null>(null)
  const [overlayEntryId, setOverlayEntryId] = useState<string | null>(null)

  const handleOpenEntry = useCallback((cluster: ToolClusterTransform, entryId: string) => {
    setOverlayCluster(cluster)
    setOverlayEntryId(entryId)
    setOverlayOpen(true)
  }, [])

  if (!flattened.length) {
    return null
  }

  return (
    <>
      <div className="trace-entry-list" role="list">
        {flattened.map(({ entry, cluster }) => {
          const caption = deriveRowCaption(entry)
          const relative = entry.timestamp ? (formatRelativeTimestamp(entry.timestamp) || entry.timestamp) : null
          return (
            <button
              key={entry.id}
              type="button"
              className="trace-entry-row"
              onClick={() => handleOpenEntry(cluster, entry.id)}
              aria-label={`Open ${entry.label} details`}
            >
              <span className={`trace-entry-row__icon ${entry.iconBgClass} ${entry.iconColorClass}`} aria-hidden="true">
                <ToolIconSlot entry={entry} />
              </span>
              <span className="trace-entry-row__body">
                <span className="trace-entry-row__label">{entry.label}</span>
                {caption ? <span className="trace-entry-row__caption">{caption}</span> : null}
              </span>
              {relative ? <span className="trace-entry-row__time">{relative}</span> : <span className="trace-entry-row__time" aria-hidden="true" />}
            </button>
          )
        })}
      </div>

      {overlayCluster ? (
        <ToolClusterTimelineOverlay
          open={overlayOpen}
          cluster={overlayCluster}
          initialOpenEntryId={overlayEntryId}
          onClose={() => {
            setOverlayOpen(false)
            setOverlayCluster(null)
            setOverlayEntryId(null)
          }}
        />
      ) : null}
    </>
  )
})

