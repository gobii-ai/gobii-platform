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

  const timeSummary = buildCronTimeSummary(minuteRaw, hourRaw)
  if (!timeSummary) {
    return null
  }

  const daySummary = buildCronDaySummary(domRaw, monthRaw, dowRaw)
  if (!daySummary) {
    return null
  }

  const year = yearRaw && !isWildcard(yearRaw) && isSimpleCronValue(yearRaw) ? parseCronNumber(yearRaw) : null

  let summary = combineCronSummary(daySummary, timeSummary)

  if (summary && year !== null) {
    summary = `${summary} in ${year}`
  }

  return summary
}

type CronTimeSummary = {
  text: string
  kind: 'at' | 'every'
}

function buildCronTimeSummary(minuteRaw: string | undefined, hourRaw: string | undefined): CronTimeSummary | null {
  const minute = parseCronNumber(minuteRaw)
  const hour = parseCronNumber(hourRaw)

  if (minute !== null && hour !== null) {
    return { text: `at ${formatTime(hour, minute)}`, kind: 'at' }
  }

  if (minute !== null && isWildcard(hourRaw)) {
    const text = minute === 0 ? 'every hour' : `every hour at ${minute} minutes past`
    return { text, kind: 'every' }
  }

  const minuteStep = parseCronStep(minuteRaw)
  if (minuteStep && isWildcard(hourRaw)) {
    return { text: `every ${minuteStep.step} minutes`, kind: 'every' }
  }

  if (minute !== null) {
    const hourList = parseCronNumberList(hourRaw)
    if (hourList?.length) {
      const times = hourList.map((value) => formatTime(value, minute))
      return { text: `at ${formatList(times)}`, kind: 'at' }
    }

    const hourRange = parseCronNumberRange(hourRaw)
    if (hourRange) {
      const startTime = formatTime(hourRange[0], minute)
      const endTime = formatTime(hourRange[1], minute)
      return { text: `every hour between ${startTime} and ${endTime}`, kind: 'every' }
    }

    const hourStep = parseCronStep(hourRaw)
    if (hourStep) {
      const baseTime =
        hourStep.start !== null ? formatTime(hourStep.start, minute) : null
      if (minute === 0 && !baseTime) {
        return { text: `every ${hourStep.step} hours`, kind: 'every' }
      }
      if (baseTime) {
        return { text: `every ${hourStep.step} hours starting at ${baseTime}`, kind: 'every' }
      }
      return { text: `every ${hourStep.step} hours at ${minute} minutes past`, kind: 'every' }
    }
  }

  if (hour !== null) {
    const minuteList = parseCronNumberList(minuteRaw)
    if (minuteList?.length) {
      const times = minuteList.map((value) => formatTime(hour, value))
      return { text: `at ${formatList(times)}`, kind: 'at' }
    }
  }

  return null
}

function buildCronDaySummary(
  domRaw: string | undefined,
  monthRaw: string | undefined,
  dowRaw: string | undefined,
): string | null {
  const monthSummary = formatMonthSummary(monthRaw)
  const dowSummary = formatWeekdaySummary(dowRaw)
  const domSummary = formatDayOfMonthSummary(domRaw)

  if (dowSummary && domSummary) {
    return null
  }

  if (dowSummary) {
    return monthSummary ? `Every ${dowSummary} in ${monthSummary}` : `Every ${dowSummary}`
  }

  if (domSummary) {
    if (monthSummary) {
      return `On ${monthSummary} ${domSummary}`
    }
    return `On the ${domSummary} day of each month`
  }

  if (monthSummary) {
    return `Every day in ${monthSummary}`
  }

  return 'Every day'
}

function combineCronSummary(daySummary: string, timeSummary: CronTimeSummary): string {
  if (timeSummary.kind === 'at') {
    return `${daySummary} ${timeSummary.text}`
  }

  const normalizedTime = capitalizeFirst(timeSummary.text)
  if (daySummary === 'Every day') {
    return normalizedTime
  }
  if (daySummary.startsWith('Every day in ')) {
    const monthPart = daySummary.slice('Every day in '.length)
    return `${normalizedTime} in ${monthPart}`
  }

  const daySuffix = daySummary.startsWith('Every ')
    ? daySummary.slice('Every '.length)
    : daySummary.startsWith('On ')
      ? daySummary.slice('On '.length)
      : daySummary.toLowerCase()

  return `${normalizedTime} on ${daySuffix}`
}

function formatMonthSummary(value: string | undefined): string | null {
  if (isWildcard(value)) return null
  if (!value) return null
  const list = parseCronNamedList(value, resolveMonthName)
  if (list?.length) {
    return formatList(list)
  }
  const range = parseCronNamedRange(value, resolveMonthName)
  if (range) {
    return `${range[0]}–${range[1]}`
  }
  const single = resolveMonthName(value)
  return single ?? null
}

function formatWeekdaySummary(value: string | undefined): string | null {
  if (isWildcard(value)) return null
  if (!value) return null
  const list = parseCronNamedList(value, resolveWeekdayName)
  if (list?.length) {
    return formatList(list)
  }
  const range = parseCronNamedRange(value, resolveWeekdayName)
  if (range) {
    return `${range[0]}–${range[1]}`
  }
  const single = resolveWeekdayName(value)
  return single ?? null
}

function formatDayOfMonthSummary(value: string | undefined): string | null {
  if (isWildcard(value)) return null
  if (!value) return null
  const single = parseCronNumber(value)
  if (single !== null) {
    return formatOrdinal(single)
  }
  const list = parseCronNumberList(value)
  if (list?.length) {
    return formatList(list.map((part) => formatOrdinal(part)))
  }
  const range = parseCronNumberRange(value)
  if (range) {
    return `${formatOrdinal(range[0])}–${formatOrdinal(range[1])}`
  }
  return null
}

function formatList(values: string[]): string {
  if (values.length <= 1) return values[0] ?? ''
  if (values.length === 2) return `${values[0]} and ${values[1]}`
  return `${values.slice(0, -1).join(', ')}, and ${values[values.length - 1]}`
}

function parseCronNumberList(value: string | undefined): number[] | null {
  if (!value || !value.includes(',')) return null
  const parts = value.split(',').map((part) => part.trim()).filter(Boolean)
  if (!parts.length) return null
  const numbers: number[] = []
  for (const part of parts) {
    if (!/^\d+$/.test(part)) {
      return null
    }
    const parsed = Number.parseInt(part, 10)
    if (!Number.isFinite(parsed)) {
      return null
    }
    numbers.push(parsed)
  }
  return numbers
}

function parseCronNumberRange(value: string | undefined): [number, number] | null {
  if (!value || !value.includes('-')) return null
  const match = value.match(/^(\d+)\s*-\s*(\d+)$/)
  if (!match) return null
  const start = Number.parseInt(match[1] ?? '', 10)
  const end = Number.parseInt(match[2] ?? '', 10)
  if (!Number.isFinite(start) || !Number.isFinite(end)) {
    return null
  }
  return [start, end]
}

function parseCronStep(value: string | undefined): { start: number | null; step: number } | null {
  if (!value || !value.includes('/')) return null
  const match = value.match(/^(\*|\d+)\s*\/\s*(\d+)$/)
  if (!match) return null
  const step = Number.parseInt(match[2] ?? '', 10)
  if (!Number.isFinite(step) || step <= 0) {
    return null
  }
  const startToken = match[1]
  if (!startToken || startToken === '*') {
    return { start: null, step }
  }
  const start = Number.parseInt(startToken, 10)
  if (!Number.isFinite(start)) {
    return null
  }
  return { start, step }
}

function parseCronNamedList(
  value: string | undefined,
  resolver: (token: string) => string | null,
): string[] | null {
  if (!value || !value.includes(',')) return null
  const parts = value.split(',').map((part) => part.trim()).filter(Boolean)
  if (!parts.length) return null
  const resolved: string[] = []
  for (const part of parts) {
    if (part.includes('-') || part.includes('/') || part.includes('*') || part.includes('?')) {
      return null
    }
    const name = resolver(part)
    if (!name) return null
    resolved.push(name)
  }
  return resolved
}

function parseCronNamedRange(
  value: string | undefined,
  resolver: (token: string) => string | null,
): [string, string] | null {
  if (!value || !value.includes('-')) return null
  const match = value.match(/^([A-Za-z0-9]+)\s*-\s*([A-Za-z0-9]+)$/)
  if (!match) return null
  const start = resolver(match[1] ?? '')
  const end = resolver(match[2] ?? '')
  if (!start || !end) return null
  return [start, end]
}

function capitalizeFirst(value: string): string {
  if (!value) return value
  return value.charAt(0).toUpperCase() + value.slice(1)
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
