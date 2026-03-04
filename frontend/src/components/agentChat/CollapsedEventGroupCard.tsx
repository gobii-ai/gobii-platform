import { memo, useCallback, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { CollapsedEventGroup } from '../../hooks/useSimplifiedTimeline'
import { CollapsedEventGroupOverlay } from './CollapsedEventGroupOverlay'

type CollapsedEventGroupCardProps = {
  group: CollapsedEventGroup
}

export const CollapsedEventGroupCard = memo(function CollapsedEventGroupCard({
  group,
}: CollapsedEventGroupCardProps) {
  const [overlayOpen, setOverlayOpen] = useState(false)
  const handleOpen = useCallback(() => setOverlayOpen(true), [])
  const handleClose = useCallback(() => setOverlayOpen(false), [])

  return (
    <>
      <div className="collapsed-event-group" role="button" tabIndex={0} onClick={handleOpen} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleOpen() }}>
        <span className="collapsed-event-group__label">{group.summary.label}</span>
        <ChevronRight className="collapsed-event-group__chevron" size={14} strokeWidth={2} />
      </div>
      <CollapsedEventGroupOverlay open={overlayOpen} group={group} onClose={handleClose} />
    </>
  )
})
