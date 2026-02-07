import { memo, useMemo } from 'react'
import { AlarmClock } from 'lucide-react'
import { formatRelativeTimestamp } from '../../util/time'

type ScheduledResumeCardProps = {
  nextScheduledAt?: string | null
}

const TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  hour: 'numeric',
  minute: '2-digit',
})
const WEEKDAY_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  weekday: 'long',
  hour: 'numeric',
  minute: '2-digit',
})
const DATE_TIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

function dayDistance(target: Date, reference: Date): number {
  const millisecondsPerDay = 24 * 60 * 60 * 1000
  const delta = startOfDay(target).getTime() - startOfDay(reference).getTime()
  return Math.round(delta / millisecondsPerDay)
}

function formatWakeTime(target: Date, reference: Date): string {
  const days = dayDistance(target, reference)
  if (days === 0) {
    return `Today at ${TIME_FORMATTER.format(target)}`
  }
  if (days === 1) {
    return `Tomorrow at ${TIME_FORMATTER.format(target)}`
  }
  if (days > 1 && days < 7) {
    return WEEKDAY_TIME_FORMATTER.format(target)
  }
  return DATE_TIME_FORMATTER.format(target)
}

export const ScheduledResumeCard = memo(function ScheduledResumeCard({ nextScheduledAt }: ScheduledResumeCardProps) {
  const parsed = useMemo(() => {
    if (!nextScheduledAt) {
      return null
    }
    const date = new Date(nextScheduledAt)
    if (Number.isNaN(date.getTime())) {
      return null
    }
    return date
  }, [nextScheduledAt])

  if (!parsed || !nextScheduledAt) {
    return null
  }

  const now = new Date()
  const isFuture = parsed.getTime() > now.getTime()
  const relativeText = formatRelativeTimestamp(nextScheduledAt, now)
  const absoluteText = formatWakeTime(parsed, now)
  const title = isFuture && relativeText
    ? `Agent will continue ${relativeText}`
    : 'Agent will continue soon'

  return (
    <article className="timeline-event scheduled-resume-card" aria-live="polite">
      <div className="scheduled-resume-card__spark" aria-hidden="true" />
      <div className="scheduled-resume-card__icon-wrap" aria-hidden="true">
        <AlarmClock size={16} />
      </div>
      <div className="scheduled-resume-card__content">
        <p className="scheduled-resume-card__title">{title}</p>
        <time className="scheduled-resume-card__time" dateTime={nextScheduledAt}>
          {absoluteText}
        </time>
      </div>
      <span className="scheduled-resume-card__pill">Scheduled wake-up</span>
    </article>
  )
})
