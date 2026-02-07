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
}

export const ToolClusterCard = memo(function ToolClusterCard({ cluster, isLatestEvent = false, suppressedThinkingCursor }: ToolClusterCardProps) {
  const transformed = useMemo(
    () => transformToolCluster(cluster, { suppressedThinkingCursor }),
    [cluster, suppressedThinkingCursor],
  )
  const separatedEntries = useMemo(
    () => transformed.entries.filter((entry) => entry.separateFromPreview),
    [transformed.entries],
  )
  const previewEntries = useMemo(
    () => transformed.entries.filter((entry) => !entry.separateFromPreview),
    [transformed.entries],
  )
  const visiblePreviewEntries = useMemo(
    () => previewEntries.slice(-TOOL_CLUSTER_PREVIEW_ENTRY_LIMIT),
    [previewEntries],
  )
  const separatedEntryPlacement = useMemo(() => {
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
  }, [separatedEntries, visiblePreviewEntries])
  const hasPreviewEntries = previewEntries.length > 0

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
        <div className="tool-cluster-summary">
          {hasPreviewEntries ? (
            <ToolClusterLivePreview
              cluster={transformed}
              isLatestEvent={isLatestEvent}
              onOpenTimeline={handleToggleCluster}
              onSelectEntry={handlePreviewEntrySelect}
            />
          ) : null}
        </div>
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
