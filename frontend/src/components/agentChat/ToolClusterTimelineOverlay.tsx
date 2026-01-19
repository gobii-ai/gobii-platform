import { useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { Workflow, X } from 'lucide-react'

import { formatRelativeTimestamp } from '../../util/time'
import { slugify } from '../../util/slugify'
import { MarkdownViewer } from '../common/MarkdownViewer'
import type { ToolClusterTransform, ToolEntryDisplay } from './tooling/types'

type ToolClusterTimelineOverlayProps = {
  open: boolean
  cluster: ToolClusterTransform
  onClose: () => void
}

function ToolIcon({ icon, className }: { icon: ToolEntryDisplay['icon'] | undefined; className?: string }) {
  const IconComponent = icon ?? Workflow
  return <IconComponent className={className} aria-hidden="true" />
}

function deriveCaption(entry: ToolEntryDisplay): string | null {
  if (entry.caption && entry.caption !== entry.label) {
    return entry.caption
  }
  if (entry.summary && entry.summary !== entry.label) {
    return entry.summary
  }
  return null
}

function deriveThinkingPreview(entry: ToolEntryDisplay): string | null {
  if (entry.toolName !== 'thinking') {
    return null
  }
  const reasoning = typeof entry.result === 'string' ? entry.result : ''
  if (!reasoning.trim()) {
    return null
  }
  const lines = reasoning.split(/\r?\n/)
  const firstLineIndex = lines.findIndex((line) => line.trim().length > 0)
  if (firstLineIndex === -1) {
    return null
  }
  const firstLine = lines[firstLineIndex]
    .trimEnd()
    .replace(/\*\*(.*?)\*\*/g, '$1')
    .replace(/__(.*?)__/g, '$1')
  const remainder = lines.slice(firstLineIndex + 1).join('\n')
  const hasMore = remainder.trim().length > 0
  return hasMore ? `${firstLine}…` : firstLine
}

export function ToolClusterTimelineOverlay({ open, cluster, onClose }: ToolClusterTimelineOverlayProps) {
  const [openEntryId, setOpenEntryId] = useState<string | null>(null)
  const titleId = useMemo(() => `tool-cluster-timeline-title-${slugify(cluster.cursor)}`, [cluster.cursor])
  const dialogId = useMemo(() => `tool-cluster-timeline-dialog-${slugify(cluster.cursor)}`, [cluster.cursor])

  useEffect(() => {
    if (!open) {
      setOpenEntryId(null)
      return undefined
    }

    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }

    document.addEventListener('keydown', handleKey)
    const originalOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', handleKey)
      document.body.style.overflow = originalOverflow
    }
  }, [onClose, open])

  const handleToggleEntry = useCallback((entryId: string) => {
    setOpenEntryId((current) => (current === entryId ? null : entryId))
  }, [])

  if (!open || typeof document === 'undefined') {
    return null
  }

  return createPortal(
    <div className="tool-cluster-timeline-overlay">
      <div className="tool-cluster-timeline-backdrop" role="presentation" onClick={onClose} />
      <div className="tool-cluster-timeline-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId} id={dialogId}>
        <div className="tool-cluster-timeline-header">
          <div className="tool-cluster-timeline-title">
            <span className="tool-cluster-timeline-count" id={titleId}>
              {cluster.entryCount} events
            </span>
            <span className="tool-cluster-timeline-subtitle">Event timeline</span>
          </div>
          <button type="button" className="tool-cluster-timeline-close" onClick={onClose} aria-label="Close timeline">
            <X className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
        <div className="tool-cluster-timeline-body">
          <ol className="tool-cluster-timeline-list" role="list">
            {cluster.entries.map((entry) => {
              const detailId = `tool-cluster-timeline-detail-${slugify(entry.id)}`
              const isOpen = openEntryId === entry.id
              const relativeTime = formatRelativeTimestamp(entry.timestamp)
              const caption = deriveCaption(entry)
              const thinkingPreview = deriveThinkingPreview(entry)
              const kind = entry.toolName === 'thinking' ? 'thinking' : entry.toolName === 'kanban' ? 'kanban' : 'tool'
              const DetailComponent = entry.detailComponent
              return (
                <li key={entry.id} className="tool-cluster-timeline-item" data-kind={kind}>
                  <button
                    type="button"
                    className="tool-cluster-timeline-row"
                    aria-expanded={isOpen ? 'true' : 'false'}
                    aria-controls={detailId}
                    data-open={isOpen ? 'true' : 'false'}
                    onClick={() => handleToggleEntry(entry.id)}
                  >
                    <span className={`tool-cluster-timeline-icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
                      <ToolIcon icon={entry.icon} className="h-5 w-5" />
                    </span>
                    <span className="tool-cluster-timeline-main">
                      <span className="tool-cluster-timeline-label">{entry.label}</span>
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
