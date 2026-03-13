import { memo, useMemo } from 'react'
import type { CollapsedEventGroup } from '../../hooks/useSimplifiedTimeline'
import { CollapsedActivityCard } from './CollapsedActivityCard'
import { flattenTimelineEventsToEntries } from './activityEntryUtils'

type CollapsedEventGroupCardProps = {
  group: CollapsedEventGroup
}

export const CollapsedEventGroupCard = memo(function CollapsedEventGroupCard({
  group,
}: CollapsedEventGroupCardProps) {
  const entries = useMemo(() => flattenTimelineEventsToEntries(group.events), [group.events])

  return <CollapsedActivityCard overlayId={group.cursor} entries={entries} label={group.summary.label} subtitle="Collapsed actions" />
})
