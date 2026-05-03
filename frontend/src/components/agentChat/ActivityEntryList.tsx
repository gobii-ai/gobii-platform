import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { formatRelativeTimestamp } from '../../util/time'
import { slugify } from '../../util/slugify'
import { MarkdownViewer } from '../common/MarkdownViewer'
import { ToolIconSlot } from './ToolIconSlot'
import { ToolProviderBadge } from './ToolProviderBadge'
import { deriveEntryCaption, deriveThinkingPreview } from './tooling/clusterPreviewText'
import type { ToolEntryDisplay } from './tooling/types'

type ActivityEntryListProps = {
  entries: ToolEntryDisplay[]
  initialOpenEntryId?: string | null
  limit?: number
  limitStrategy?: 'head' | 'tail'
  onViewAll?: () => void
}

export function ActivityEntryList({
  entries,
  initialOpenEntryId = null,
  limit,
  limitStrategy = 'head',
  onViewAll,
}: ActivityEntryListProps) {
  const entryRowRefs = useRef<Record<string, HTMLLIElement | null>>({})
  const initialOpenEntryIdRef = useRef<string | null>(null)
  const [openEntryId, setOpenEntryId] = useState<string | null>(null)
  const visibleEntries = useMemo(
    () => {
      if (typeof limit !== 'number') {
        return entries
      }
      return limitStrategy === 'tail' ? entries.slice(-limit) : entries.slice(0, limit)
    },
    [entries, limit, limitStrategy],
  )
  const hasMoreEntries = typeof limit === 'number' && entries.length > limit

  useEffect(() => {
    if (!initialOpenEntryId || initialOpenEntryIdRef.current === initialOpenEntryId) {
      return
    }
    initialOpenEntryIdRef.current = initialOpenEntryId
    const hasTarget = visibleEntries.some((entry) => entry.id === initialOpenEntryId)
    setOpenEntryId(hasTarget ? initialOpenEntryId : null)
  }, [initialOpenEntryId, visibleEntries])

  useEffect(() => {
    if (!openEntryId) {
      return
    }
    const hasOpenEntry = visibleEntries.some((entry) => entry.id === openEntryId)
    if (!hasOpenEntry) {
      setOpenEntryId(null)
    }
  }, [openEntryId, visibleEntries])

  useEffect(() => {
    if (!openEntryId) {
      return
    }
    const row = entryRowRefs.current[openEntryId]
    if (row) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  }, [openEntryId])

  const handleToggleEntry = useCallback((entryId: string) => {
    setOpenEntryId((current) => (current === entryId ? null : entryId))
  }, [])

  return (
    <>
      <ol className="tool-cluster-timeline-list" role="list">
        {visibleEntries.map((entry) => {
          const detailId = `tool-cluster-timeline-detail-${slugify(entry.id)}`
          const isOpen = openEntryId === entry.id
          const relativeTime = formatRelativeTimestamp(entry.timestamp)
          const caption = deriveEntryCaption(entry)
          const thinkingPreview = deriveThinkingPreview(entry)
          const kind = entry.toolName === 'thinking' ? 'thinking' : 'tool'
          const DetailComponent = entry.detailComponent

          return (
            <li
              key={entry.id}
              className="tool-cluster-timeline-item"
              data-kind={kind}
              data-entry-id={entry.id}
              ref={(node) => {
                entryRowRefs.current[entry.id] = node
              }}
            >
              <button
                type="button"
                className="tool-cluster-timeline-row"
                aria-expanded={isOpen ? 'true' : 'false'}
                aria-controls={detailId}
                data-open={isOpen ? 'true' : 'false'}
                onClick={() => handleToggleEntry(entry.id)}
              >
                <span className={`tool-cluster-timeline-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
                  <ToolIconSlot entry={entry} />
                </span>
                <span className="tool-cluster-timeline-main">
                  <span className="tool-cluster-timeline-label-row">
                    <span className="tool-cluster-timeline-label">{entry.label}</span>
                    <ToolProviderBadge entry={entry} className="tool-provider-badge--timeline" />
                  </span>
                  {caption ? <span className="tool-cluster-timeline-caption">{caption}</span> : null}
                  {thinkingPreview ? (
                    <div className="tool-cluster-timeline-preview">
                      <MarkdownViewer
                        content={thinkingPreview}
                        className="tool-cluster-timeline-preview-markdown"
                        enableHighlight={false}
                      />
                    </div>
                  ) : null}
                </span>
                {entry.timestamp ? (
                  <time
                    className="tool-cluster-timeline-time"
                    dateTime={entry.timestamp ?? undefined}
                    title={entry.timestamp ?? undefined}
                  >
                    {relativeTime ?? entry.timestamp}
                  </time>
                ) : null}
              </button>
              {isOpen ? (
                <div className="tool-cluster-timeline-detail" id={detailId} role="region" aria-label={`${entry.label} details`}>
                  <DetailComponent entry={entry} />
                </div>
              ) : null}
            </li>
          )
        })}
      </ol>
      {hasMoreEntries && onViewAll ? (
        <div className="collapsed-activity-cluster__footer">
          <button type="button" className="collapsed-activity-cluster__view-all" onClick={onViewAll}>
            View all actions
          </button>
        </div>
      ) : null}
    </>
  )
}
