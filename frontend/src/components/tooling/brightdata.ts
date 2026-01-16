import { isPlainObject, parseResultObject } from '../../util/objectUtils'

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

type SerpItem = { title: string; url: string; position: number | null }

function normalizeSerpItem(value: unknown, index: number): SerpItem | null {
  if (!isPlainObject(value)) return null
  const raw = value as Record<string, unknown>
  const title = typeof raw['t'] === 'string' ? raw['t'] : typeof raw['title'] === 'string' ? raw['title'] : null
  const url = typeof raw['u'] === 'string' ? raw['u'] : typeof raw['link'] === 'string' ? raw['link'] : null
  if (!url) {
    return null
  }
  const positionRaw = raw['p'] ?? raw['position']
  const position =
    typeof positionRaw === 'number' && Number.isFinite(positionRaw)
      ? positionRaw
      : typeof positionRaw === 'string'
        ? Number.parseInt(positionRaw, 10)
        : null
  return {
    title: title && title.trim().length ? title : url,
    url,
    position: Number.isFinite(position) ? (position as number) : index + 1,
  }
}

function collectSerpArray(value: unknown): unknown[] | null {
  if (Array.isArray(value)) {
    return value
  }
  if (isPlainObject(value)) {
    const obj = value as Record<string, unknown>
    if (Array.isArray(obj['items'])) return obj['items']
    if (Array.isArray(obj['organic'])) return obj['organic']
    if (Array.isArray(obj['results'])) return obj['results']
    if (obj['result']) {
      const nested = collectSerpArray(obj['result'])
      if (nested) return nested
    }
  }
  return null
}

export function extractBrightDataSerpItems(result: unknown): SerpItem[] {
  const parsed = parseResultObject(result)
  const candidates = collectSerpArray(parsed ?? result)
  if (!candidates) return []
  return candidates
    .map((item, idx) => normalizeSerpItem(item, idx))
    .filter((item): item is SerpItem => Boolean(item))
}

export function extractBrightDataFirstRecord(result: unknown): Record<string, unknown> | null {
  const parsed = parseResultObject(result)
  const candidates: unknown[] = []

  if (Array.isArray(parsed)) {
    candidates.push(...parsed)
  } else if (isPlainObject(parsed)) {
    const obj = parsed as Record<string, unknown>
    if (Array.isArray(obj.result)) {
      candidates.push(...obj.result)
    } else if (obj.result && Array.isArray((obj.result as { items?: unknown[] }).items)) {
      candidates.push(...(((obj.result as { items?: unknown[] }).items as unknown[]) ?? []))
    } else {
      candidates.push(parsed)
    }
  } else if (Array.isArray(result)) {
    candidates.push(...result)
  }

  const first = candidates.find((item) => isPlainObject(item)) as Record<string, unknown> | undefined
  return first ?? null
}
