const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })

function toSecondsDiff(target: Date, reference: Date): number {
  return Math.round((target.getTime() - reference.getTime()) / 1000)
}

export function formatRelativeTimestamp(value?: string | null, referenceDate: Date = new Date()): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return null
  }

  const diffSeconds = toSecondsDiff(parsed, referenceDate)
  const absSeconds = Math.abs(diffSeconds)

  if (absSeconds < 45) {
    return RELATIVE_FORMATTER.format(Math.round(diffSeconds), 'second')
  }
  const diffMinutes = diffSeconds / 60
  if (Math.abs(diffMinutes) < 45) {
    return RELATIVE_FORMATTER.format(Math.round(diffMinutes), 'minute')
  }
  const diffHours = diffMinutes / 60
  if (Math.abs(diffHours) < 24) {
    return RELATIVE_FORMATTER.format(Math.round(diffHours), 'hour')
  }
  const diffDays = diffHours / 24
  if (Math.abs(diffDays) < 30) {
    return RELATIVE_FORMATTER.format(Math.round(diffDays), 'day')
  }
  const diffMonths = diffDays / 30
  if (Math.abs(diffMonths) < 12) {
    return RELATIVE_FORMATTER.format(Math.round(diffMonths), 'month')
  }
  const diffYears = diffMonths / 12
  return RELATIVE_FORMATTER.format(Math.round(diffYears), 'year')
}

