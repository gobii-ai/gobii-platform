import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

import { slugify } from '../../util/slugify'
import { formatRelativeTimestamp } from '../../util/time'
import { transformToolCluster } from './tooling/toolRegistry'
import { deriveEntryCaption, deriveThinkingPreview } from './tooling/clusterPreviewText'
import { ToolIconSlot } from './ToolIconSlot'
import { ToolProviderBadge } from './ToolProviderBadge'
import { MarkdownViewer } from '../common/MarkdownViewer'
import type { CollapsedEventGroup } from '../../hooks/useSimplifiedTimeline'
import type { ToolClusterEvent } from '../../types/agentChat'
import type { ToolEntryDisplay } from './tooling/types'

type CollapsedEventGroupOverlayProps = {
  open: boolean
  group: CollapsedEventGroup
  onClose: () => void
}

/**
 * Flatten all events in a collapsed group into a single list of ToolEntryDisplay items.
 * This avoids nested collapsing — every entry renders as a compact row.
 */
function flattenGroupEntries(group: CollapsedEventGroup): ToolEntryDisplay[] {
  const allEntries: ToolEntryDisplay[] = []

  for (const event of group.events) {
    let cluster: ToolClusterEvent

    if (event.kind === 'steps') {
      cluster = event
    } else if (event.kind === 'thinking') {
      cluster = {
        kind: 'steps',
        cursor: event.cursor,
        entries: [],
        entryCount: 1,
        collapsible: false,
        collapseThreshold: Infinity,
        earliestTimestamp: event.timestamp ?? null,
        latestTimestamp: event.timestamp ?? null,
        thinkingEntries: [event],
      }
    } else if (event.kind === 'kanban') {
      cluster = {
        kind: 'steps',
        cursor: event.cursor,
        entries: [],
        entryCount: 1,
        collapsible: false,
        collapseThreshold: Infinity,
        earliestTimestamp: event.timestamp ?? null,
        latestTimestamp: event.timestamp ?? null,
        kanbanEntries: [event],
      }
    } else {
      continue
    }

    const transformed = transformToolCluster(cluster)
    allEntries.push(...transformed.entries)
  }

  return allEntries
}

export function CollapsedEventGroupOverlay({ open, group, onClose }: CollapsedEventGroupOverlayProps) {
  const titleId = useMemo(() => `collapsed-group-title-${slugify(group.cursor)}`, [group.cursor])
  const dialogId = useMemo(() => `collapsed-group-dialog-${slugify(group.cursor)}`, [group.cursor])
  const [openEntryId, setOpenEntryId] = useState<string | null>(null)
  const entryRowRefs = useRef<Record<string, HTMLLIElement | null>>({})

  const entries = useMemo(() => flattenGroupEntries(group), [group])

  const handleToggleEntry = useCallback((entryId: string) => {
    setOpenEntryId((current) => (current === entryId ? null : entryId))
  }, [])

  useEffect(() => {
    if (!open) {
      setOpenEntryId(null)
      return undefined
    }

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [onClose, open])

  useEffect(() => {
    if (!open || !openEntryId) return
    const row = entryRowRefs.current[openEntryId]
    if (row) row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [open, openEntryId])

  if (!open || typeof document === 'undefined') return null

  return createPortal(
    <div className="tool-cluster-timeline-overlay">
      <div className="tool-cluster-timeline-backdrop" role="presentation" onClick={onClose} />
      <div
        className="tool-cluster-timeline-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        id={dialogId}
      >
        <div className="tool-cluster-timeline-header">
          <div className="tool-cluster-timeline-title">
            <span className="tool-cluster-timeline-count" id={titleId}>
              {group.summary.label}
            </span>
            <span className="tool-cluster-timeline-subtitle">Collapsed events</span>
          </div>
          <button type="button" className="tool-cluster-timeline-close" onClick={onClose} aria-label="Close">
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="tool-cluster-timeline-body">
          <ol className="tool-cluster-timeline-list" role="list">
            {entries.map((entry) => {
              const detailId = `collapsed-group-detail-${slugify(entry.id)}`
              const isOpen = openEntryId === entry.id
              const relativeTime = formatRelativeTimestamp(entry.timestamp)
              const caption = deriveEntryCaption(entry)
              const thinkingPreview = deriveThinkingPreview(entry)
              const kind = entry.toolName === 'thinking' ? 'thinking' : entry.toolName === 'kanban' ? 'kanban' : 'tool'
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
                          <MarkdownViewer content={thinkingPreview} className="tool-cluster-timeline-preview-markdown" enableHighlight={false} />
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
        </div>
      </div>
    </div>,
    document.body,
  )
}
