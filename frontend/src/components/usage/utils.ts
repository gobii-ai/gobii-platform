import type { DateValue } from '@internationalized/date'

import type { DateRangeValue } from './types'

export const cloneRange = (range: DateRangeValue): DateRangeValue => ({
  start: range.start.copy(),
  end: range.end.copy(),
})

export const areRangesEqual = (a: DateRangeValue, b: DateRangeValue): boolean =>
  a.start.compare(b.start) === 0 && a.end.compare(b.end) === 0

export const getRangeLengthInDays = (range: DateRangeValue): number => {
  const startJulian = range.start.calendar.toJulianDay(range.start)
  const endJulian = range.end.calendar.toJulianDay(range.end)
  return endJulian - startJulian + 1
}

export const setDayWithClamp = (date: DateValue, day: number): DateValue => {
  const maxDay = date.calendar.getDaysInMonth(date)
  return date.set({ day: Math.min(day, maxDay) })
}

export const getAnchorDay = (range: DateRangeValue): number => {
  const nextStart = range.end.add({ days: 1 })
  return Math.max(range.start.day, nextStart.day)
}

export const computeBillingRangeFromStart = (start: DateValue, anchorDay: number): DateRangeValue => {
  const normalizedStart = setDayWithClamp(start, anchorDay)
  const nextStart = setDayWithClamp(normalizedStart.add({ months: 1 }), anchorDay)
  const normalizedEnd = nextStart.subtract({ days: 1 })
  return {
    start: normalizedStart,
    end: normalizedEnd,
  }
}

export const shiftBillingRange = (range: DateRangeValue, anchorDay: number, months: number): DateRangeValue => {
  const shiftedStart = setDayWithClamp(range.start.add({ months }), anchorDay)
  return computeBillingRangeFromStart(shiftedStart, anchorDay)
}

export const shiftCustomRangeByDays = (range: DateRangeValue, days: number): DateRangeValue => ({
  start: range.start.add({ days }),
  end: range.end.add({ days }),
})
