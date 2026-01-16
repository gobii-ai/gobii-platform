import { isPlainObject } from '../../util/objectUtils'

function normalizeResultPayload(result: unknown): unknown | null {
  if (result === null || result === undefined) return null

  if (typeof result === 'string') {
    const trimmed = result.trim()
    if (!trimmed.length) return null
    try {
      return JSON.parse(trimmed)
    } catch {
      return null
    }
  }

  return result
}

function extractArrayCandidate(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value
  }

  if (!isPlainObject(value)) {
    return null
  }

  const obj = value as Record<string, unknown>
  const keysToCheck = ['items', 'results', 'organic', 'data', 'value', 'entries', 'links']
  for (const key of keysToCheck) {
    const candidate = obj[key]
    if (Array.isArray(candidate)) {
      return candidate
    }
  }

  if ('result' in obj) {
    const nested = extractArrayCandidate(obj['result'])
    if (nested) {
      return nested
    }
  }

  return null
}

function extractNumericCount(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (!isPlainObject(value)) {
    return null
  }

  const obj = value as Record<string, unknown>
  const keysToCheck = ['count', 'total', 'total_results', 'result_count', 'results_count']
  for (const key of keysToCheck) {
    const candidate = obj[key]
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      return candidate
    }
  }

  if ('_meta' in obj) {
    const nested = extractNumericCount(obj['_meta'])
    if (nested !== null) {
      return nested
    }
  }

  if ('result' in obj) {
    const nested = extractNumericCount(obj['result'])
    if (nested !== null) {
      return nested
    }
  }

  return null
}

export function extractBrightDataSearchQuery(parameters: Record<string, unknown> | null | undefined): string | null {
  if (!parameters) return null

  const keysToCheck = ['query', 'q', 'keywords', 'term', 'search']
  for (const key of keysToCheck) {
    const raw = parameters[key]
    if (typeof raw === 'string') {
      const trimmed = raw.trim()
      if (trimmed.length > 0) {
        return trimmed
      }
    }
  }

  return null
}

export function extractBrightDataResultCount(result: unknown): number | null {
  const payload = normalizeResultPayload(result)
  if (payload === null) {
    return null
  }

  const arrayCandidate = extractArrayCandidate(payload)
  if (arrayCandidate) {
    return arrayCandidate.length
  }

  const numeric = extractNumericCount(payload)
  if (numeric !== null) {
    return numeric
  }

  return null
}
