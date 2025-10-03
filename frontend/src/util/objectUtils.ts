export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export const isRecord = isPlainObject

export function parseResultObject(value: unknown): Record<string, unknown> | null {
  if (!value) return null

  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return isPlainObject(parsed) ? parsed : null
    } catch {
      return null
    }
  }

  if (isPlainObject(value)) {
    return value
  }

  return null
}
