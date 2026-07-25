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

  // Expanding in place can only ever show the tail of a long run, so the card would promise "N
  // actions" and then reveal fewer, needing a second click to finish the job it advertised.
  // Go straight to the full view in that case and keep the inline expand for runs that fit.
  const exceedsInlineLimit = entries.length > INLINE_ACTIVITY_ENTRY_LIMIT
  const showInlineList = expanded && !exceedsInlineLimit

  return (
    <div className="timeline-event collapsed-activity-cluster">
      <button
        type="button"
        className="collapsed-event-group"
        aria-expanded={exceedsInlineLimit ? undefined : (expanded ? 'true' : 'false')}
        aria-haspopup={exceedsInlineLimit ? 'dialog' : undefined}
        onClick={() => (exceedsInlineLimit ? setViewerOpen(true) : setExpanded((current) => !current))}
      >
        <span className="collapsed-event-group__label">{resolvedLabel}</span>
        <ChevronRight
          className="collapsed-event-group__chevron"
          data-expanded={showInlineList ? 'true' : 'false'}
          size={14}
          strokeWidth={2}
        />
      </button>
      {showInlineList ? (
        <div className="collapsed-activity-cluster__body">
          <ActivityEntryList
            entries={entries}
            limit={INLINE_ACTIVITY_ENTRY_LIMIT}
            limitStrategy="tail"
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
