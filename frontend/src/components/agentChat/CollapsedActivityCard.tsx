import { memo, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'

import type { ToolEntryDisplay } from './tooling/types'
import { formatRelativeTimestamp } from '../../util/time'
import { buildActionCountLabel } from './activityEntryUtils'
import { ToolClusterTimelineOverlay } from './ToolClusterTimelineOverlay'

type CollapsedActivityCardProps = {
  overlayId: string
  entries: ToolEntryDisplay[]
  label?: string
  subtitle?: string
}

/**
 * A run of actions that has been collapsed to a single "N actions" control.
 *
 * It always opens the full view. Expanding in place gave the same control two different
 * behaviours depending on how many actions happened to be in the run, which is not something a
 * reader can predict before clicking, and the long runs went to the overlay anyway. Growing the
 * timeline in place also moved everything below the card, which is the last thing wanted while
 * older history is streaming in above.
 */
export const CollapsedActivityCard = memo(function CollapsedActivityCard({
  overlayId,
  entries,
  label,
  subtitle = 'Action timeline',
}: CollapsedActivityCardProps) {
  const [viewerOpen, setViewerOpen] = useState(false)
  const resolvedLabel = useMemo(
    () => label ?? buildActionCountLabel(entries.length),
    [entries.length, label],
  )
  // When the run expanded in place it carried a time on every entry. Collapsed to a single
  // control it had none at all, leaving a row of otherwise identical chips with nothing to place
  // them in the conversation. Show when the run happened.
  const relativeTime = useMemo(() => {
    for (let index = entries.length - 1; index >= 0; index -= 1) {
      const formatted = formatRelativeTimestamp(entries[index].timestamp)
      if (formatted) {
        return formatted
      }
    }
    return null
  }, [entries])

  if (!entries.length) {
    return null
  }

  return (
    <div className="timeline-event collapsed-activity-cluster">
      <button
        type="button"
        className="collapsed-event-group"
        aria-haspopup="dialog"
        onClick={() => setViewerOpen(true)}
      >
        <span className="collapsed-event-group__label">{resolvedLabel}</span>
        {relativeTime ? (
          <span className="collapsed-event-group__time">{relativeTime}</span>
        ) : null}
        <ChevronRight
          className="collapsed-event-group__chevron"
          data-expanded="false"
          size={14}
          strokeWidth={2}
        />
      </button>
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
