import { memo } from 'react'
import { ChevronRight } from 'lucide-react'
import { Button, DialogTrigger } from 'react-aria-components'
import type { CollapsedEventGroup } from '../../hooks/useSimplifiedTimeline'
import { CollapsedEventGroupOverlay } from './CollapsedEventGroupOverlay'

type CollapsedEventGroupCardProps = {
  group: CollapsedEventGroup
}

export const CollapsedEventGroupCard = memo(function CollapsedEventGroupCard({
  group,
}: CollapsedEventGroupCardProps) {
  return (
    <DialogTrigger>
      <Button className="collapsed-event-group">
        <span className="collapsed-event-group__label">{group.summary.label}</span>
        <ChevronRight className="collapsed-event-group__chevron" size={14} strokeWidth={2} />
      </Button>
      <CollapsedEventGroupOverlay group={group} />
    </DialogTrigger>
  )
})
