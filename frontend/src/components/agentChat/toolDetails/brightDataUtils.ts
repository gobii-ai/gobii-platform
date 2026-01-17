import { isNonEmptyString } from './utils'

export function toText(value: unknown): string | null {
  return isNonEmptyString(value) ? (value as string) : null
}

export function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const normalized = value.replace(/[, $]+/g, '')
    const parsed = Number(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

export function shorten(value: string | null, max = 360): string | null {
  if (!value) return null
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}
