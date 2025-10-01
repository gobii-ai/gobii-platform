export type ScheduleIntervalPart = {
  magnitude: number
  unit: string
  label: string
}

export type ScheduleDescription =
  | { kind: 'disabled'; summary: string }
  | { kind: 'preset'; raw: string; description: string; summary: string }
  | { kind: 'interval'; raw: string; parts: ScheduleIntervalPart[]; summary: string }
  | { kind: 'cron'; raw: string; fields: Array<{ label: string; value: string }>; summary: string | null }
  | { kind: 'unknown'; raw: string; summary: null }

const CRON_FIELD_LABELS = ['Minute', 'Hour', 'Day of month', 'Month', 'Day of week', 'Year']

const SPECIAL_SCHEDULE_DESCRIPTIONS: Record<string, string> = {
  '@hourly': 'Runs at the top of every hour.',
  '@daily': 'Runs every day at midnight.',
  '@midnight': 'Runs every day at midnight.',
  '@weekly': 'Runs once a week at midnight on Sunday.',
  '@monthly': 'Runs once a month at midnight on the first day.',
  '@annually': 'Runs once a year at midnight on January 1st.',
  '@yearly': 'Runs once a year at midnight on January 1st.',
  '@reboot': 'Runs immediately when the agent restarts.',
}

const SPECIAL_SCHEDULE_SUMMARIES: Record<string, string> = {
  '@hourly': 'Every hour',
  '@daily': 'Every day at midnight',
  '@midnight': 'Every day at midnight',
  '@weekly': 'Every Sunday at midnight',
  '@monthly': 'The first day of each month at midnight',
  '@annually': 'January 1 at midnight',
  '@yearly': 'January 1 at midnight',
  '@reboot': 'When the agent restarts',
}

const DURATION_UNITS: Record<string, { label: string; shortLabel: string }> = {
  w: { label: 'week', shortLabel: 'wk' },
  d: { label: 'day', shortLabel: 'day' },
  h: { label: 'hour', shortLabel: 'hr' },
  m: { label: 'minute', shortLabel: 'min' },
  s: { label: 'second', shortLabel: 'sec' },
}

const WEEKDAY_LOOKUP: Record<string, string> = {
  '0': 'Sunday',
  '1': 'Monday',
  '2': 'Tuesday',
  '3': 'Wednesday',
  '4': 'Thursday',
  '5': 'Friday',
  '6': 'Saturday',
  '7': 'Sunday',
  SUN: 'Sunday',
  MON: 'Monday',
  TUE: 'Tuesday',
  TUES: 'Tuesday',
  WED: 'Wednesday',
  THU: 'Thursday',
  THUR: 'Thursday',
  FRI: 'Friday',
  SAT: 'Saturday',
}

const MONTH_LOOKUP: Record<string, string> = {
  '1': 'January',
  '2': 'February',
  '3': 'March',
  '4': 'April',
  '5': 'May',
  '6': 'June',
  '7': 'July',
  '8': 'August',
  '9': 'September',
  '10': 'October',
  '11': 'November',
  '12': 'December',
  JAN: 'January',
  FEB: 'February',
  MAR: 'March',
  APR: 'April',
  MAY: 'May',
  JUN: 'June',
  JUL: 'July',
  AUG: 'August',
  SEP: 'September',
  SEPT: 'September',
  OCT: 'October',
  NOV: 'November',
  DEC: 'December',
}

export function describeSchedule(raw: string | null): ScheduleDescription {
  if (!raw) {
    return { kind: 'disabled', summary: 'Disabled' }
  }

  const presetDescription = SPECIAL_SCHEDULE_DESCRIPTIONS[raw]
  if (presetDescription) {
    return {
      kind: 'preset',
      raw,
      description: presetDescription,
      summary: SPECIAL_SCHEDULE_SUMMARIES[raw] ?? presetDescription,
    }
  }

  if (raw.startsWith('@every')) {
    const intervalPortion = raw.slice('@every'.length).trim()
    const parts = parseIntervalParts(intervalPortion)
    if (parts.length) {
      const summary = `Every ${parts.map((part) => part.label).join(' ')}`
      return { kind: 'interval', raw, parts, summary }
    }
    return { kind: 'unknown', raw, summary: null }
  }

  const cronParts = raw.split(/\s+/).filter(Boolean)
  if (cronParts.length === 5 || cronParts.length === 6) {
    const labels = CRON_FIELD_LABELS.slice(0, cronParts.length)
    const fields = cronParts.map((value, index) => ({ label: labels[index], value }))
    return { kind: 'cron', raw, fields, summary: buildCronSummary(cronParts) }
  }

  return { kind: 'unknown', raw, summary: null }
}

export function summarizeSchedule(value: string | null): string | null {
  const description = describeSchedule(value)
  switch (description.kind) {
    case 'disabled':
      return 'Disabled'
    case 'preset':
    case 'interval':
      return description.summary
    case 'cron':
      return description.summary ?? null
    default:
      return null
  }
}

function parseIntervalParts(value: string): ScheduleIntervalPart[] {
  if (!value) return []
  const parts: ScheduleIntervalPart[] = []
  for (const token of value.split(/\s+/)) {
    const normalized = token.trim()
    if (!normalized) continue

    const matches = normalized.matchAll(/(\d+)([a-zA-Z]+)/g)
    let matched = false
    for (const match of matches) {
      const magnitude = Number.parseInt(match[1] ?? '0', 10)
      const unitKey = (match[2] ?? '').toLowerCase()
      const unitDefinition = DURATION_UNITS[unitKey as keyof typeof DURATION_UNITS]
      if (!Number.isFinite(magnitude) || magnitude <= 0 || !unitDefinition) continue
      matched = true
      parts.push({
        magnitude,
        unit: unitKey,
        label: `${magnitude} ${magnitude === 1 ? unitDefinition.label : `${unitDefinition.label}s`}`,
      })
    }
    if (!matched) {
      return []
    }
  }
  return parts
}

function buildCronSummary(fields: string[]): string | null {
  if (fields.length < 5) return null
  const [minuteRaw, hourRaw, domRaw, monthRaw, dowRaw, yearRaw] = [...fields, undefined]

  if (!isSimpleCronValue(minuteRaw) || !isSimpleCronValue(hourRaw)) {
    return null
  }

  const minute = parseCronNumber(minuteRaw)
  const hour = parseCronNumber(hourRaw)
  if (minute === null || hour === null) {
    return null
  }

  const time = formatTime(hour, minute)
  const dom = !isWildcard(domRaw) && isSimpleCronValue(domRaw) ? parseCronNumber(domRaw) : null
  const month = !isWildcard(monthRaw) && isSimpleCronValue(monthRaw) ? resolveMonthName(monthRaw) : null
  const dow = !isWildcard(dowRaw) && isSimpleCronValue(dowRaw) ? resolveWeekdayName(dowRaw) : null
  const year = yearRaw && !isWildcard(yearRaw) && isSimpleCronValue(yearRaw) ? parseCronNumber(yearRaw) : null

  if (dom !== null && dow) {
    return null
  }

  let summary: string | null = null

  if (dom === null && !dow && !month) {
    summary = `Every day at ${time}`
  } else if (dow && !month) {
    summary = `Every ${dow} at ${time}`
  } else if (dow && month) {
    summary = `Every ${dow} in ${month} at ${time}`
  } else if (dom !== null && !month) {
    summary = `On the ${formatOrdinal(dom)} day of each month at ${time}`
  } else if (dom !== null && month) {
    summary = `On ${month} ${formatOrdinal(dom)} at ${time}`
  } else if (!dow && !dom && month) {
    summary = `Every day in ${month} at ${time}`
  }

  if (summary && year !== null) {
    summary = `${summary} in ${year}`
  }

  return summary
}

function isWildcard(value: string | undefined): boolean {
  return value === undefined || value === '*' || value === '?'
}

function isSimpleCronValue(value: string | undefined): value is string {
  if (!value) return false
  if (value.includes('?')) return false
  return !/[-*,/]/.test(value) || /^[0-9]+$/.test(value)
}

function parseCronNumber(value: string | undefined): number | null {
  if (!value) return null
  if (!/^\d+$/.test(value)) return null
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) ? parsed : null
}

function resolveWeekdayName(value: string): string | null {
  const normalized = value.trim().toUpperCase()
  return WEEKDAY_LOOKUP[normalized] ?? null
}

function resolveMonthName(value: string): string | null {
  const normalized = value.trim().toUpperCase()
  return MONTH_LOOKUP[normalized] ?? null
}

function formatTime(hour: number, minute: number): string {
  const normalizedMinute = minute.toString().padStart(2, '0')
  const normalizedHour = ((hour + 24) % 24)
  const period = normalizedHour >= 12 ? 'PM' : 'AM'
  const hour12 = normalizedHour % 12 === 0 ? 12 : normalizedHour % 12
  return `${hour12}:${normalizedMinute} ${period}`
}

function formatOrdinal(value: number): string {
  const abs = Math.abs(value)
  const mod100 = abs % 100
  if (mod100 >= 11 && mod100 <= 13) {
    return `${value}th`
  }
  const mod10 = abs % 10
  if (mod10 === 1) return `${value}st`
  if (mod10 === 2) return `${value}nd`
  if (mod10 === 3) return `${value}rd`
  return `${value}th`
}
