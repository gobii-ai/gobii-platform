import { memo, useCallback, useMemo } from 'react'
import { transformToolCluster, isClusterRenderable } from './tooling/toolRegistry'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'
import { ToolIconSlot } from './ToolIconSlot'
import { MoodShiftCard } from './MoodShiftCard'
import { ToolProviderBadge } from './ToolProviderBadge'
import { ToolClusterLivePreview } from './ToolClusterLivePreview'
import type { ToolClusterEvent } from './types'
import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { buildActionCountLabel } from './activityEntryUtils'
import type { StatusExpansionTargets } from './statusExpansion'
import { isStatusDisplayEntry, resolveEntrySeparation } from './statusExpansion'
import { useAppDispatch, useAppSelector } from '../../store/hooks'
import { selectImmersiveShellViewer } from '../../store/immersiveShellSlice'
import { chatActions, selectActiveChatSession } from '../../store/chatSlice'
import { buildToolClusterRenderSegments } from './toolClusterSegments'

type ToolClusterCardProps = {
  cluster: ToolClusterEvent
  isLatestEvent?: boolean
  suppressedThinkingCursor?: string | null
  statusExpansionTargets?: StatusExpansionTargets
  animateIncoming?: boolean
  forceActive?: boolean
  onIncomingAnimationConsumed?: (cursor: string) => void
}

export const ToolClusterCard = memo(function ToolClusterCard({
  cluster,
  isLatestEvent = false,
  suppressedThinkingCursor,
  statusExpansionTargets,
  animateIncoming = false,
  forceActive = false,
  onIncomingAnimationConsumed,
}: ToolClusterCardProps) {
  const scheduleTimeZone = useAppSelector(selectImmersiveShellViewer).timeZone
  const transformed = useMemo(
    () => {
      const base = transformToolCluster(cluster, { suppressedThinkingCursor, timeZone: scheduleTimeZone ?? undefined })
      if (!cluster.visibleDisplayEntryIds?.length) {
        return base
      }

      const visibleIds = new Set(cluster.visibleDisplayEntryIds)
      const entries = base.entries.filter((entry) => visibleIds.has(entry.id))
      return {
        ...base,
        entries,
        entryCount: entries.length,
        collapsible: false,
        collapseThreshold: Infinity,
      }
    },
    [cluster, scheduleTimeZone, suppressedThinkingCursor],
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
  const previewEntries = useMemo(
    () => resolvedTransformed.entries.filter((entry) => !entry.separateFromPreview),
    [resolvedTransformed.entries],
  )
  const renderSegments = useMemo(
    () => buildToolClusterRenderSegments(resolvedTransformed.entries),
    [resolvedTransformed.entries],
  )

  // Keyed by the run's first entry id, not the cluster cursor: the cursor changes when
  // thinking/tool events coalesce, and this component itself unmounts when the run flips
  // between collapsed and expanded. Store-held state under a stable id survives both
  // (bug #306 — the open panel closed whenever a new message arrived).
  const stableOverlayId = resolvedTransformed.entries[0]?.id ?? resolvedTransformed.cursor
  const dispatch = useAppDispatch()
  const activityOverlay = useAppSelector(selectActiveChatSession).timelineUi.activityOverlay
  const timelineOpen = activityOverlay?.overlayId === stableOverlayId
  const timelineInitialEntryId = timelineOpen ? activityOverlay?.initialEntryId ?? null : null
  const handleToggleCluster = useCallback(() => {
    dispatch(chatActions.activityOverlayOpened({ overlayId: stableOverlayId }))
  }, [dispatch, stableOverlayId])

  const handlePreviewEntrySelect = useCallback(
    (entry: ToolEntryDisplay) => {
      dispatch(chatActions.activityOverlayOpened({ overlayId: stableOverlayId, initialEntryId: entry.id }))
    },
    [dispatch, stableOverlayId],
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
  const hasSeparatedEntry = useMemo(
    () => resolvedTransformed.entries.some((entry) => entry.separateFromPreview),
    [resolvedTransformed.entries],
  )
  const shouldCollapse = useMemo(() => {
    if (hasSeparatedEntry) {
      return false
    }
    if (resolvedTransformed.collapsible) {
      return true
    }
    if (!statusExpansionTargets) {
      return false
    }
    return resolvedTransformed.entries.some((entry) => isStatusDisplayEntry(entry) && !entry.separateFromPreview)
  }, [hasSeparatedEntry, resolvedTransformed.collapsible, resolvedTransformed.entries, statusExpansionTargets])
  const shouldCollapsePreviewEntries = hasExpandedStatusEntry && previewEntries.length > 1

  if (!isClusterRenderable(resolvedTransformed)) {
    return null
  }

  if (shouldCollapse) {
    return (
      <CollapsedActivityCard
        overlayId={stableOverlayId}
        entries={resolvedTransformed.entries}
        label={buildActionCountLabel(resolvedTransformed.entryCount)}
      />
    )
  }

  const renderSeparatedEntry = (entry: ToolEntryDisplay) => {
    // A mood has no result to inspect and no parameters worth showing; the feeling is the whole
    // event. Giving it the standard icon-label-detail card would bury it again.
    if (entry.emotion !== undefined) {
      return <MoodShiftCard key={entry.id} entry={entry} />
    }
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
        {renderSegments.map((segment) => {
          if (segment.kind === 'separate') {
            return <div key={segment.key} className="tool-cluster-separate-list">{renderSeparatedEntry(segment.entry)}</div>
          }

          const segmentCluster = {
            ...resolvedTransformed,
            entries: segment.entries,
            entryCount: segment.entries.length,
            collapsible: false,
            collapseThreshold: Infinity,
          }
          return (
            <div key={segment.key} className="tool-cluster-summary">
              {shouldCollapsePreviewEntries && segment.entries.length > 1 ? (
                <CollapsedActivityCard
                  overlayId={segment.entries[0]?.id ?? `${stableOverlayId}:${segment.key}`}
                  entries={segment.entries}
                  label={buildActionCountLabel(segment.entries.length)}
                />
              ) : (
                <ToolClusterLivePreview
                  cluster={segmentCluster}
                  isLatestEvent={isLatestEvent && segment.isTrailing}
                  animateIncoming={animateIncoming && segment.isTrailing}
                  forceActive={forceActive && segment.isTrailing}
                  previewEntryLimit={segment.entries.length}
                  onOpenTimeline={handleToggleCluster}
                  onSelectEntry={handlePreviewEntrySelect}
                  onIncomingAnimationConsumed={onIncomingAnimationConsumed}
                />
              )}
            </div>
          )
        })}
      </div>
      <ToolClusterTimelineOverlay
        open={timelineOpen}
        overlayId={stableOverlayId}
        title={buildActionCountLabel(resolvedTransformed.entryCount)}
        entries={resolvedTransformed.entries}
        initialOpenEntryId={timelineInitialEntryId}
        onClose={() => dispatch(chatActions.activityOverlayClosed())}
      />
    </article>
  )
})
