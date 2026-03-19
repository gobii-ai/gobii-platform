import { memo, useCallback, useMemo, useState } from 'react'
import { transformToolCluster, isClusterRenderable } from './tooling/toolRegistry'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'
import { ToolIconSlot } from './ToolIconSlot'
import { ToolProviderBadge } from './ToolProviderBadge'
import { ToolClusterLivePreview } from './ToolClusterLivePreview'
import type { ToolClusterEvent } from './types'
import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'
import { compareTimelineCursors } from '../../util/timelineCursor'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { buildActionCountLabel } from './activityEntryUtils'
import type { StatusExpansionTargets } from './statusExpansion'
import { isStatusDisplayEntry, resolveEntrySeparation } from './statusExpansion'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
  isLatestEvent?: boolean
  suppressedThinkingCursor?: string | null
  statusExpansionTargets?: StatusExpansionTargets
}

export const ToolClusterCard = memo(function ToolClusterCard({
  cluster,
  isLatestEvent = false,
  suppressedThinkingCursor,
  statusExpansionTargets,
}: ToolClusterCardProps) {
  const transformed = useMemo(
    () => transformToolCluster(cluster, { suppressedThinkingCursor }),
    [cluster, suppressedThinkingCursor],
  )
  const resolvedTransformed = useMemo(() => {
    if (!statusExpansionTargets) {
      return transformed
    }

    let changed = false
    const entries = transformed.entries.map((entry) => {
      const separateFromPreview = resolveEntrySeparation(entry, statusExpansionTargets)
      if (separateFromPreview === entry.separateFromPreview) {
        return entry
      }
      changed = true
      return {
        ...entry,
        separateFromPreview,
      }
    })

    if (!changed) {
      return transformed
    }

    return {
      ...transformed,
      entries,
    }
  }, [statusExpansionTargets, transformed])
  const separatedEntries = useMemo(
    () => resolvedTransformed.entries.filter((entry) => entry.separateFromPreview),
    [resolvedTransformed.entries],
  )
  const previewEntries = useMemo(
    () => resolvedTransformed.entries.filter((entry) => !entry.separateFromPreview),
    [resolvedTransformed.entries],
  )
  const visiblePreviewEntries = previewEntries
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
    if (resolvedTransformed.collapsible) {
      classes.push('tool-cluster--collapsible')
    }
    return classes.join(' ')
  }, [resolvedTransformed.collapsible])
  const hasExpandedStatusEntry = useMemo(
    () => resolvedTransformed.entries.some((entry) => isStatusDisplayEntry(entry) && entry.separateFromPreview),
    [resolvedTransformed.entries],
  )
  const shouldCollapse = useMemo(() => {
    if (hasExpandedStatusEntry) {
      return false
    }
    if (resolvedTransformed.collapsible) {
      return true
    }
    if (!statusExpansionTargets) {
      return false
    }
    return resolvedTransformed.entries.some((entry) => isStatusDisplayEntry(entry) && !entry.separateFromPreview)
  }, [hasExpandedStatusEntry, resolvedTransformed.collapsible, resolvedTransformed.entries, statusExpansionTargets])

  if (!isClusterRenderable(resolvedTransformed)) {
    return null
  }

  if (shouldCollapse) {
    return (
      <CollapsedActivityCard
        overlayId={resolvedTransformed.cursor}
        entries={resolvedTransformed.entries}
        label={buildActionCountLabel(resolvedTransformed.entryCount)}
      />
    )
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
      data-entry-count={resolvedTransformed.entryCount}
      data-collapsible={resolvedTransformed.collapsible ? 'true' : 'false'}
      data-collapse-threshold={cluster.collapseThreshold}
      data-cluster-kind="tool"
      data-earliest={resolvedTransformed.earliestTimestamp}
    >
      <div className="tool-cluster-shell">
        {separatedEntryPlacement.beforePreview.length ? (
          <div className="tool-cluster-separate-list">{separatedEntryPlacement.beforePreview.map(renderSeparatedEntry)}</div>
        ) : null}
        {hasPreviewEntries ? (
          <div className="tool-cluster-summary">
            <ToolClusterLivePreview
              cluster={resolvedTransformed}
              isLatestEvent={isLatestEvent}
              previewEntryLimit={previewEntries.length}
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
        overlayId={resolvedTransformed.cursor}
        title={buildActionCountLabel(resolvedTransformed.entryCount)}
        entries={resolvedTransformed.entries}
        initialOpenEntryId={timelineInitialEntryId}
        onClose={() => {
          setTimelineOpen(false)
          setTimelineInitialEntryId(null)
        }}
      />
    </article>
  )
})
