import { memo, useMemo } from 'react'
import { AlarmClock, PlusSquare, Settings, Zap } from 'lucide-react'
import { formatRelativeTimestamp } from '../../util/time'

type ScheduledResumeCardProps = {
  nextScheduledAt?: string | null
  onDoubleLimit?: () => void | Promise<void>
  doubleLimitLabel?: string
  onSetUnlimited?: () => void | Promise<void>
  onOpenSettings?: () => void
  onOpenTaskPacks?: () => void
  onUpgrade?: () => void | Promise<void>
  actionBusy?: boolean
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

export const ScheduledResumeCard = memo(function ScheduledResumeCard({
  nextScheduledAt,
  onDoubleLimit,
  doubleLimitLabel = 'Double daily limit',
  onSetUnlimited,
  onOpenSettings,
  onOpenTaskPacks,
  onUpgrade,
  actionBusy = false,
}: ScheduledResumeCardProps) {
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
  const hasActions = Boolean(
    onDoubleLimit
    || onSetUnlimited
    || onOpenTaskPacks
    || onUpgrade
    || onOpenSettings,
  )

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
      {hasActions ? (
        <div className="scheduled-resume-card__actions">
          {onDoubleLimit ? (
            <button
              type="button"
              className="scheduled-resume-card__action scheduled-resume-card__action--primary"
              onClick={() => {
                void onDoubleLimit()
              }}
              disabled={actionBusy}
            >
              <Zap size={14} />
              {doubleLimitLabel}
            </button>
          ) : null}
          {onSetUnlimited ? (
            <button
              type="button"
              className="scheduled-resume-card__action"
              onClick={() => {
                void onSetUnlimited()
              }}
              disabled={actionBusy}
            >
              Set unlimited
            </button>
          ) : null}
          {onOpenTaskPacks ? (
            <button
              type="button"
              className="scheduled-resume-card__action"
              onClick={onOpenTaskPacks}
              disabled={actionBusy}
            >
              <PlusSquare size={14} />
              Buy task pack
            </button>
          ) : null}
          {onUpgrade ? (
            <button
              type="button"
              className="scheduled-resume-card__action scheduled-resume-card__action--primary"
              onClick={() => {
                void onUpgrade()
              }}
              disabled={actionBusy}
            >
              <Zap size={14} />
              Upgrade plan
            </button>
          ) : null}
          {onOpenSettings ? (
            <button
              type="button"
              className="scheduled-resume-card__action"
              onClick={onOpenSettings}
              disabled={actionBusy}
            >
              <Settings size={14} />
              Open settings
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  )
})
