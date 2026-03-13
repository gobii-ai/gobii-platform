import { memo, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'

import type { ToolEntryDisplay } from './tooling/types'
import { ActivityEntryList } from './ActivityEntryList'
import { INLINE_ACTIVITY_ENTRY_LIMIT, buildActionCountLabel } from './activityEntryUtils'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'

type CollapsedActivityCardProps = {
  overlayId: string
  entries: ToolEntryDisplay[]
  label?: string
  subtitle?: string
}

export const CollapsedActivityCard = memo(function CollapsedActivityCard({
  overlayId,
  entries,
  label,
  subtitle = 'Action timeline',
}: CollapsedActivityCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [viewerOpen, setViewerOpen] = useState(false)
  const resolvedLabel = useMemo(
    () => label ?? buildActionCountLabel(entries.length),
    [entries.length, label],
  )

  if (!entries.length) {
    return null
  }

  return (
    <div className="timeline-event collapsed-activity-cluster">
      <button
        type="button"
        className="collapsed-event-group"
        aria-expanded={expanded ? 'true' : 'false'}
        onClick={() => setExpanded((current) => !current)}
      >
        <span className="collapsed-event-group__label">{resolvedLabel}</span>
        <ChevronRight
          className="collapsed-event-group__chevron"
          data-expanded={expanded ? 'true' : 'false'}
          size={14}
          strokeWidth={2}
        />
      </button>
      {expanded ? (
        <div className="collapsed-activity-cluster__body">
          <ActivityEntryList
            entries={entries}
            limit={INLINE_ACTIVITY_ENTRY_LIMIT}
            onViewAll={entries.length > INLINE_ACTIVITY_ENTRY_LIMIT ? () => setViewerOpen(true) : undefined}
          />
        </div>
      ) : null}
      <ToolClusterTimelineOverlay
        open={viewerOpen}
        overlayId={overlayId}
        title={buildActionCountLabel(entries.length)}
        subtitle={subtitle}
        entries={entries}
        onClose={() => setViewerOpen(false)}
      />
    </div>
  )
})
