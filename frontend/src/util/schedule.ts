export type ScheduleIntervalPart = {
  magnitude: number
  unit: string
  label: string
}

export type ScheduleDisplayOptions = {
  timeZone?: string
  referenceDate?: Date
}

let defaultDisplayTimeZone: string | undefined

export function setScheduleDisplayTimeZone(timeZone: string | null | undefined): void {
  defaultDisplayTimeZone = timeZone?.trim() || undefined
}

export type ScheduleDescription =
  | { kind: 'disabled'; summary: string }
  | { kind: 'preset'; raw: string; description: string; summary: string }
  | { kind: 'interval'; raw: string; parts: ScheduleIntervalPart[]; summary: string }
  | { kind: 'cron'; raw: string; fields: Array<{ label: string; value: string }>; summary: string | null }
  | { kind: 'unknown'; raw: string; summary: null }

const CRON_FIELD_LABELS = ['Minute', 'Hour', 'Day of month', 'Month', 'Day of week', 'Year']

const SPECIAL_SCHEDULES: Record<string, { cron?: string; description?: string; summary?: string }> = {
  '@hourly': { cron: '0 * * * *' },
  '@daily': { cron: '0 0 * * *' },
  '@midnight': { cron: '0 0 * * *' },
  '@weekly': { cron: '0 0 * * 0' },
  '@monthly': { cron: '0 0 1 * *' },
  '@annually': { cron: '0 0 1 1 *' },
  '@yearly': { cron: '0 0 1 1 *' },
  '@reboot': { description: 'Runs immediately when the agent restarts.', summary: 'When the agent restarts' },
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

export function describeSchedule(raw: string | null, options: ScheduleDisplayOptions = {}): ScheduleDescription {
  if (!raw) {
    return { kind: 'disabled', summary: 'Disabled' }
  }

  const preset = SPECIAL_SCHEDULES[raw]
  if (preset) {
    const summary = (preset.cron ? buildCronSummary(preset.cron.split(' '), options) : null)
      ?? preset.summary
      ?? preset.description
      ?? 'Scheduled'
    return {
      kind: 'preset',
      raw,
      description: preset.cron ? `${summary.replace(/[.!?]+$/, '')}.` : preset.description ?? summary,
      summary,
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
    return { kind: 'cron', raw, fields, summary: buildCronSummary(cronParts, options) }
  }

  return { kind: 'unknown', raw, summary: null }
}

export function summarizeSchedule(value: string | null, options: ScheduleDisplayOptions = {}): string | null {
  const description = describeSchedule(value, options)
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

function buildCronSummary(fields: string[], options: ScheduleDisplayOptions): string | null {
  if (fields.length < 5) return null
  const [minuteRaw, hourRaw, domRaw, monthRaw, dowRaw, yearRaw] = [...fields, undefined]

  const localized = localizeFixedUtcCronTime(minuteRaw, hourRaw, domRaw, monthRaw, dowRaw, options)
  if (localized?.summary) {
    return localized.summary
  }
  const timeSummary = localized
    ? { text: `at ${localized.time}`, kind: 'at' as const }
    : buildCronTimeSummary(minuteRaw, hourRaw)
  if (!timeSummary) {
    return null
  }

  const daySummary = buildCronDaySummary(domRaw, monthRaw, localized?.dayOfWeek ?? dowRaw)
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

type LocalizedCronTime = { time: string; dayOfWeek: string | undefined; summary?: string }

function localizeFixedUtcCronTime(
  minuteRaw: string | undefined,
  hourRaw: string | undefined,
  dayOfMonthRaw: string | undefined,
  monthRaw: string | undefined,
  dayOfWeekRaw: string | undefined,
  options: ScheduleDisplayOptions,
): LocalizedCronTime | null {
  const minute = parseCronNumber(minuteRaw)
  const hour = parseCronNumber(hourRaw)
  if (minute === null || hour === null || (!isWildcard(dayOfMonthRaw) && !isWildcard(dayOfWeekRaw))) {
    return null
  }

  const sourceWeekdays = parseCronWeekdayIndexes(dayOfWeekRaw)
  if (!sourceWeekdays) {
    return null
  }

  const timeZone = resolveDisplayTimeZone(options.timeZone)
  const referenceDate = options.referenceDate instanceof Date && !Number.isNaN(options.referenceDate.getTime())
    ? options.referenceDate
    : new Date()
  const occurrence = findNextUtcOccurrence(hour, minute, sourceWeekdays, referenceDate, dayOfMonthRaw, monthRaw)
  if (!occurrence) {
    return null
  }

  const time = formatZonedTime(occurrence, timeZone)
  const localWeekday = getZonedWeekdayIndex(occurrence, timeZone)
  if (!time || localWeekday === null) {
    return null
  }

  const sourceWeekday = occurrence.getUTCDay()
  const dayShift = normalizeWeekday(localWeekday - sourceWeekday)
  return {
    time,
    dayOfWeek: shiftCronWeekdays(dayOfWeekRaw, dayShift),
    ...(!isWildcard(dayOfMonthRaw) || !isWildcard(monthRaw)
      ? { summary: formatNextZonedOccurrence(occurrence, timeZone) }
      : {}),
  }
}

function resolveDisplayTimeZone(preferredTimeZone: string | undefined): string {
  try {
    const candidate = preferredTimeZone?.trim()
      || defaultDisplayTimeZone
      || Intl.DateTimeFormat().resolvedOptions().timeZone
      || 'UTC'
    new Intl.DateTimeFormat('en-US', { timeZone: candidate }).format()
    return candidate
  } catch {
    return 'UTC'
  }
}

function findNextUtcOccurrence(
  hour: number,
  minute: number,
  weekdays: Set<number>,
  referenceDate: Date,
  dayOfMonthRaw: string | undefined,
  monthRaw: string | undefined,
): Date | null {
  const dayOfMonth = isWildcard(dayOfMonthRaw) ? null : parseCronNumber(dayOfMonthRaw)
  const month = isWildcard(monthRaw) ? null : parseCronMonth(monthRaw)
  if ((!isWildcard(dayOfMonthRaw) && dayOfMonth === null) || (!isWildcard(monthRaw) && month === null)) {
    return null
  }
  const start = Date.UTC(referenceDate.getUTCFullYear(), referenceDate.getUTCMonth(), referenceDate.getUTCDate(), hour, minute)

  const searchDays = dayOfMonth === null && month === null ? 7 : 366 * 4
  for (let dayOffset = 0; dayOffset <= searchDays; dayOffset += 1) {
    const candidate = new Date(start + dayOffset * 24 * 60 * 60 * 1000)
    if (
      candidate.getTime() >= referenceDate.getTime()
      && weekdays.has(candidate.getUTCDay())
      && (dayOfMonth === null || candidate.getUTCDate() === dayOfMonth)
      && (month === null || candidate.getUTCMonth() + 1 === month)
    ) {
      return candidate
    }
  }
  return null
}

function formatNextZonedOccurrence(value: Date, timeZone: string): string | undefined {
  try {
    return `Next run ${new Intl.DateTimeFormat('en-US', {
      timeZone,
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      timeZoneName: 'short',
    }).format(value)}`
  } catch {
    return undefined
  }
}

function formatZonedTime(value: Date, timeZone: string): string | null {
  try {
    return new Intl.DateTimeFormat('en-US', { timeZone, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }).format(value)
  } catch {
    return null
  }
}

function getZonedWeekdayIndex(value: Date, timeZone: string): number | null {
  try {
    const weekday = new Intl.DateTimeFormat('en-US', { timeZone, weekday: 'short' }).format(value).toUpperCase()
    const resolved = resolveWeekdayName(weekday)
    return resolved ? WEEKDAY_NAMES.indexOf(resolved) : null
  } catch {
    return null
  }
}

const WEEKDAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']

function parseCronMonth(value: string | undefined): number | null {
  if (!value) return null
  const numeric = parseCronNumber(value)
  if (numeric !== null) return numeric >= 1 && numeric <= 12 ? numeric : null
  const month = resolveMonthName(value)
  const index = month ? MONTH_NAMES.indexOf(month) : -1
  return index >= 0 ? index + 1 : null
}

function parseCronWeekdayIndexes(value: string | undefined): Set<number> | null {
  if (isWildcard(value)) return new Set([0, 1, 2, 3, 4, 5, 6])
  if (!value) return null

  const indexes = new Set<number>()
  for (const part of value.split(',')) {
    const normalized = part.trim()
    if (!normalized || normalized.includes('/')) return null
    const range = normalized.split('-')
    if (range.length > 2) return null
    const start = parseWeekdayIndex(range[0])
    const end = parseWeekdayIndex(range[1] ?? range[0])
    if (start === null || end === null) return null

    let current = start
    indexes.add(current)
    while (current !== end) {
      current = normalizeWeekday(current + 1)
      indexes.add(current)
    }
  }
  return indexes.size ? indexes : null
}

function parseWeekdayIndex(value: string | undefined): number | null {
  const token = value?.trim()
  if (!token) return null
  const numeric = parseCronNumber(token)
  if (numeric !== null) return numeric >= 0 && numeric <= 7 ? normalizeWeekday(numeric) : null
  const name = resolveWeekdayName(token)
  if (!name) return null
  const index = WEEKDAY_NAMES.indexOf(name)
  return index >= 0 ? index : null
}

function shiftCronWeekdays(value: string | undefined, shift: number): string | undefined {
  if (!value || isWildcard(value) || shift === 0) return value
  return value
    .split(',')
    .map((part) => part
      .split('-')
      .map((token) => {
        const weekday = parseWeekdayIndex(token.trim())
        return weekday === null ? token : String(normalizeWeekday(weekday + shift))
      })
      .join('-'))
    .join(',')
}

function normalizeWeekday(value: number): number {
  return ((value % 7) + 7) % 7
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
  return formatNamedSummary(value, resolveMonthName)
}

function formatWeekdaySummary(value: string | undefined): string | null {
  return formatNamedSummary(value, resolveWeekdayName)
}

function formatNamedSummary(value: string | undefined, resolver: (token: string) => string | null): string | null {
  if (isWildcard(value)) return null
  if (!value) return null
  const list = parseCronNamedList(value, resolver)
  if (list?.length) return formatList(list)
  const range = parseCronNamedRange(value, resolver)
  if (range) return `${range[0]}–${range[1]}`
  return resolver(value)
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
  if (parts.some((part) => !/^\d+$/.test(part))) return null
  const numbers = parts.map((part) => Number.parseInt(part, 10))
  return numbers.every(Number.isFinite) ? numbers : null
}

function parseCronNumberRange(value: string | undefined): [number, number] | null {
  if (!value || !value.includes('-')) return null
  const match = value.match(/^(\d+)\s*-\s*(\d+)$/)
  if (!match) return null
  const [start, end] = [Number.parseInt(match[1] ?? '', 10), Number.parseInt(match[2] ?? '', 10)]
  if (!Number.isFinite(start) || !Number.isFinite(end)) return null
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
  if (parts.some((part) => part.includes('-') || part.includes('/') || part.includes('*') || part.includes('?'))) return null
  const resolved = parts.map(resolver)
  return resolved.every((name): name is string => Boolean(name)) ? resolved : null
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
  return `${hour12}:${normalizedMinute} ${period} UTC`
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
