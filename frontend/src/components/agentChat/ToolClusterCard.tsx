import { memo, useCallback, useMemo, useState } from 'react'
import { transformToolCluster, isClusterRenderable } from './tooling/toolRegistry'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'
import { ToolIconSlot } from './ToolIconSlot'
import { ToolProviderBadge } from './ToolProviderBadge'
import { ToolClusterLivePreview, TOOL_CLUSTER_PREVIEW_ENTRY_LIMIT } from './ToolClusterLivePreview'
import type { ToolClusterEvent } from './types'
import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'
import { compareTimelineCursors } from '../../util/timelineCursor'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
  isLatestEvent?: boolean
  suppressedThinkingCursor?: string | null
  variant?: 'preview' | 'trace'
}

function clampText(value: string, maxLen: number): string {
  const normalized = value.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLen) return normalized
  return `${normalized.slice(0, Math.max(0, maxLen - 1)).trimEnd()}…`
}

function deriveTraceCaption(entry: ToolEntryDisplay): string | null {
  if (entry.caption) return entry.caption
  if (entry.summary) return entry.summary
  if (typeof entry.result === 'string' && entry.result.trim()) {
    return clampText(entry.result, 86)
  }
  return null
}

export const ToolClusterCard = memo(function ToolClusterCard({
  cluster,
  isLatestEvent = false,
  suppressedThinkingCursor,
  variant = 'preview',
}: ToolClusterCardProps) {
  const transformed = useMemo(
    () => transformToolCluster(cluster, { suppressedThinkingCursor }),
    [cluster, suppressedThinkingCursor],
  )

  const [timelineOpen, setTimelineOpen] = useState(false)
  const [timelineInitialEntryId, setTimelineInitialEntryId] = useState<string | null>(null)
  const handleToggleCluster = useCallback(() => {
    setTimelineInitialEntryId(null)
    setTimelineOpen(true)
  }, [])

  const handlePreviewEntrySelect = useCallback(
    (entry: ToolEntryDisplay) => {
      setTimelineInitialEntryId(entry.id)
      setTimelineOpen(true)
    },
    [],
  )

  const articleClasses = useMemo(() => {
    const classes = ['timeline-event', 'tool-cluster']
    if (transformed.collapsible) {
      classes.push('tool-cluster--collapsible')
    }
    return classes.join(' ')
  }, [transformed.collapsible])

  if (!isClusterRenderable(transformed)) {
    return null
  }

  if (variant === 'trace') {
    const entries = transformed.entries
    const maxVisible = 4
    const visible = entries.slice(-maxVisible)
    const hiddenCount = Math.max(0, entries.length - visible.length)
    const updatedAt = transformed.latestTimestamp ?? entries[entries.length - 1]?.timestamp ?? null
    const updatedLabel = updatedAt ? (formatRelativeTimestamp(updatedAt) || updatedAt) : null

    return (
      <article
        className={`${articleClasses} tool-cluster--trace`}
        data-cursor={cluster.cursor}
        data-entry-count={transformed.entryCount}
        data-cluster-kind="tool-trace"
        data-earliest={transformed.earliestTimestamp}
      >
        <div className="tool-cluster-trace">
          <button
            type="button"
            className="tool-cluster-trace__header"
            onClick={handleToggleCluster}
          >
            <span className="tool-cluster-trace__title">Work</span>
            <span className="tool-cluster-trace__count">{transformed.entryCount}</span>
            {updatedLabel ? (
              <time className="tool-cluster-trace__updated" dateTime={updatedAt ?? undefined} title={updatedAt ?? undefined}>
                {updatedLabel}
              </time>
            ) : null}
            <span className="tool-cluster-trace__open" aria-hidden="true">Details</span>
          </button>
          <div className="tool-cluster-trace__rows">
            {visible.map((entry) => {
              const caption = deriveTraceCaption(entry)
              const rowRelative = formatRelativeTimestamp(entry.timestamp) || entry.timestamp || ''
              const status = entry.status ?? null
              return (
                <button
                  key={entry.id}
                  type="button"
                  className="tool-cluster-trace-row"
                  onClick={() => handlePreviewEntrySelect(entry)}
                  data-status={status ?? ''}
                >
                  <span className={`tool-cluster-trace-row__icon ${entry.iconBgClass} ${entry.iconColorClass}`} aria-hidden="true">
                    <ToolIconSlot entry={entry} />
                  </span>
                  <span className="tool-cluster-trace-row__body">
                    <span className="tool-cluster-trace-row__top">
                      <span className="tool-cluster-trace-row__label">{entry.label}</span>
                      <ToolProviderBadge entry={entry} className="tool-provider-badge--trace" />
                      {status ? <span className="tool-cluster-trace-row__status">{status}</span> : null}
                    </span>
                    {caption ? <span className="tool-cluster-trace-row__caption">{caption}</span> : null}
                  </span>
                  {entry.timestamp ? (
                    <time
                      className="tool-cluster-trace-row__time"
                      dateTime={entry.timestamp ?? undefined}
                      title={entry.timestamp ?? undefined}
                    >
                      {rowRelative}
                    </time>
                  ) : (
                    <span className="tool-cluster-trace-row__time" aria-hidden="true" />
                  )}
                </button>
              )
            })}
            {hiddenCount > 0 ? (
              <button type="button" className="tool-cluster-trace-more" onClick={handleToggleCluster}>
                +{hiddenCount} more
              </button>
            ) : null}
          </div>
        </div>
        <ToolClusterTimelineOverlay
          open={timelineOpen}
          cluster={transformed}
          initialOpenEntryId={timelineInitialEntryId}
          onClose={() => {
            setTimelineOpen(false)
            setTimelineInitialEntryId(null)
          }}
        />
      </article>
    )
  }

  const separatedEntries = transformed.entries.filter((entry) => entry.separateFromPreview)
  const previewEntries = transformed.entries.filter((entry) => !entry.separateFromPreview)
  const visiblePreviewEntries = previewEntries.slice(-TOOL_CLUSTER_PREVIEW_ENTRY_LIMIT)
  const hasPreviewEntries = previewEntries.length > 0

  const separatedEntryPlacement = (() => {
    if (!separatedEntries.length) {
      return { beforePreview: [] as ToolEntryDisplay[], afterPreview: [] as ToolEntryDisplay[] }
    }

    const firstVisiblePreviewCursor = visiblePreviewEntries[0]?.cursor
    if (!firstVisiblePreviewCursor) {
      return { beforePreview: [] as ToolEntryDisplay[], afterPreview: separatedEntries }
    }

    const beforePreview: ToolEntryDisplay[] = []
    const afterPreview: ToolEntryDisplay[] = []
    for (const entry of separatedEntries) {
      if (!entry.cursor) {
        afterPreview.push(entry)
        continue
      }
      if (compareTimelineCursors(entry.cursor, firstVisiblePreviewCursor) <= 0) {
        beforePreview.push(entry)
      } else {
        afterPreview.push(entry)
      }
    }
    return { beforePreview, afterPreview }
  })()

  const renderSeparatedEntry = (entry: ToolEntryDisplay) => {
    const DetailComponent = entry.detailComponent
    const detailRelative = formatRelativeTimestamp(entry.timestamp) || entry.timestamp || ''
    return (
      <article key={entry.id} className="tool-cluster-separate-card">
        <div className="tool-cluster-separate-card__header">
          <span className={`tool-cluster-separate-card__icon ${entry.iconBgClass} ${entry.iconColorClass}`}>
            <ToolIconSlot entry={entry} />
          </span>
          <div className="tool-cluster-separate-card__title-wrap">
            <div className="tool-cluster-separate-card__title-row">
              <span className="tool-cluster-separate-card__label">{entry.label}</span>
              <ToolProviderBadge entry={entry} className="tool-provider-badge--detail" />
            </div>
            {entry.caption ? <p className="tool-cluster-separate-card__caption">{entry.caption}</p> : null}
            {entry.timestamp ? (
              <time
                dateTime={entry.timestamp ?? undefined}
                className="tool-cluster-separate-card__meta"
                title={entry.timestamp ?? undefined}
              >
                {detailRelative}
              </time>
            ) : null}
          </div>
        </div>
        <div className="tool-cluster-separate-card__body">
          <DetailComponent entry={entry} />
        </div>
      </article>
    )
  }

  return (
    <article
      className={articleClasses}
      data-cursor={cluster.cursor}
      data-entry-count={transformed.entryCount}
      data-collapsible={transformed.collapsible ? 'true' : 'false'}
      data-collapse-threshold={cluster.collapseThreshold}
      data-cluster-kind="tool"
      data-earliest={transformed.earliestTimestamp}
    >
      <div className="tool-cluster-shell">
        {separatedEntryPlacement.beforePreview.length ? (
          <div className="tool-cluster-separate-list">{separatedEntryPlacement.beforePreview.map(renderSeparatedEntry)}</div>
        ) : null}
        {hasPreviewEntries ? (
          <div className="tool-cluster-summary">
            <ToolClusterLivePreview
              cluster={transformed}
              isLatestEvent={isLatestEvent}
              onOpenTimeline={handleToggleCluster}
              onSelectEntry={handlePreviewEntrySelect}
            />
          </div>
        ) : null}
        {separatedEntryPlacement.afterPreview.length ? (
          <div className="tool-cluster-separate-list">{separatedEntryPlacement.afterPreview.map(renderSeparatedEntry)}</div>
        ) : null}
      </div>
      <ToolClusterTimelineOverlay
        open={timelineOpen}
        cluster={transformed}
        initialOpenEntryId={timelineInitialEntryId}
        onClose={() => {
          setTimelineOpen(false)
          setTimelineInitialEntryId(null)
        }}
      />
    </article>
  )
})
