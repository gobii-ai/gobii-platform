import { formatRelativeTimestamp } from '../../util/time'

type SimplifiedScheduledEventCardProps = {
  nextScheduledAt: string | null
}

export function SimplifiedScheduledEventCard({ nextScheduledAt }: SimplifiedScheduledEventCardProps) {
  if (!nextScheduledAt) {
    return null
  }

  const relative = formatRelativeTimestamp(nextScheduledAt) || 'soon'

  return (
    <article className="timeline-event simplified-system-event" data-event-kind="scheduled-resume">
      <div className="simplified-system-event__label">scheduled run</div>
      <time className="simplified-system-event__time" dateTime={nextScheduledAt} title={nextScheduledAt}>
        resumes {relative}
      </time>
    </article>
  )
}
