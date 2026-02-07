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

export function extractArrayCandidate(value: unknown): unknown[] | null {
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return extractArrayCandidate(parsed)
    } catch {
      return null
    }
  }

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
    if (typeof candidate === 'string') {
      try {
        const parsed = JSON.parse(candidate)
        if (Array.isArray(parsed)) {
          return parsed
        }
        if (isPlainObject(parsed)) {
          const nested = extractArrayCandidate(parsed)
          if (nested) {
            return nested
          }
        }
      } catch {
        // fall through
      }
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

  const numeric = extractNumericCount(payload)
  const arrayCandidate = extractArrayCandidate(payload)
  const arrayLength = arrayCandidate?.length ?? null

  if (numeric !== null && arrayLength !== null) {
    return Math.max(numeric, arrayLength)
  }
  if (numeric !== null) return numeric
  if (arrayLength !== null) return arrayLength

  return null
}

type SerpItem = { title: string; url: string; position: number | null }

const GENERIC_SEARCH_LINK_TITLES = new Set([
  'read more',
  'more',
  'more items',
  'visit',
  'visit site',
  'learn more',
  'details',
  'open',
])

const SEARCH_TITLE_SKIP_TOKENS = [
  'people also ask',
  'discussions and forums',
  'filters and topics',
  'accessibility feedback',
  'feedback',
  'ai mode',
]

function sanitizeSearchTitle(value: string): string {
  return value
    .replace(/^#+\s*/, '')
    .replace(/[_*`~]+/g, '')
    .replace(/\\\[/g, '[')
    .replace(/\\\]/g, ']')
    .replace(/\s+/g, ' ')
    .trim()
}

function isGenericSearchTitle(value: string | null): boolean {
  if (!value) {
    return true
  }
  const normalized = sanitizeSearchTitle(value)
    .toLowerCase()
    .replace(/[.…]+$/g, '')
    .trim()
  return GENERIC_SEARCH_LINK_TITLES.has(normalized)
}

function isPlausibleSearchTitleLine(value: string): boolean {
  const normalized = sanitizeSearchTitle(value)
  if (normalized.length < 4) return false
  if (!/[a-zA-Z]/.test(normalized)) return false
  if (/^https?:\/\//i.test(normalized)) return false
  if (normalized.startsWith('![') || normalized.startsWith('[') || normalized === ']') return false
  const lowered = normalized.toLowerCase()
  if (SEARCH_TITLE_SKIP_TOKENS.some((token) => lowered.includes(token))) return false
  if (isGenericSearchTitle(normalized)) return false
  return true
}

function parseHost(value: string): string | null {
  try {
    const url = new URL(value)
    return url.hostname.toLowerCase()
  } catch {
    return null
  }
}

function normalizeSearchResultUrl(value: string | null): string | null {
  if (!value) return null
  const raw = value.trim()
  if (!raw) return null
  const withProtocol = /^https?:\/\//i.test(raw) ? raw : `https://${raw}`
  try {
    const parsed = new URL(withProtocol)
    const host = parsed.hostname.toLowerCase()

    if (host.includes('google.') && parsed.pathname === '/url') {
      const candidate = parsed.searchParams.get('q') || parsed.searchParams.get('url')
      if (candidate && /^https?:\/\//i.test(candidate)) {
        return candidate
      }
    }

    if (
      host.includes('google.') ||
      host.includes('googleusercontent.com') ||
      host.includes('accounts.google.com') ||
      host.includes('support.google.com')
    ) {
      return null
    }

    return parsed.toString()
  } catch {
    return null
  }
}

function extractForwardMarkdownTitle(source: string, fromIndex: number): string | null {
  const window = source.slice(fromIndex, Math.min(source.length, fromIndex + 440))
  const headingMatch = window.match(/###\s+([^\n]+)/)
  if (headingMatch?.[1]) {
    const heading = sanitizeSearchTitle(headingMatch[1])
    if (isPlausibleSearchTitleLine(heading)) {
      return heading
    }
  }
  const lines = window
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  for (const line of lines) {
    if (isPlausibleSearchTitleLine(line)) {
      return sanitizeSearchTitle(line)
    }
  }
  return null
}

function extractBackwardMarkdownTitle(source: string, toIndex: number): string | null {
  const window = source.slice(Math.max(0, toIndex - 260), toIndex)
  const lines = window
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .reverse()
  for (const line of lines) {
    if (isPlausibleSearchTitleLine(line)) {
      return sanitizeSearchTitle(line)
    }
  }
  return null
}

function extractMarkdownSerpItems(markdown: string): SerpItem[] {
  const items: SerpItem[] = []
  const seen = new Set<string>()
  const linkPattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g
  let match: RegExpExecArray | null
  while ((match = linkPattern.exec(markdown)) !== null) {
    let title = (match[1] || '').trim()
    const rawUrl = (match[2] || '').trim()
    const normalizedUrl = normalizeSearchResultUrl(rawUrl)
    if (!normalizedUrl) {
      continue
    }
    const host = parseHost(normalizedUrl)
    if (!host) {
      continue
    }
    if (seen.has(normalizedUrl)) {
      continue
    }
    seen.add(normalizedUrl)
    if (isGenericSearchTitle(title)) {
      title =
        extractForwardMarkdownTitle(markdown, match.index + match[0].length) ??
        extractBackwardMarkdownTitle(markdown, match.index) ??
        host
    }
    const cleanedTitle = sanitizeSearchTitle(title)
    items.push({
      title: cleanedTitle || host,
      url: normalizedUrl,
      position: items.length + 1,
    })
    if (items.length >= 64) {
      break
    }
  }
  return items
}

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
  if (candidates) {
    return candidates
      .map((item, idx) => normalizeSerpItem(item, idx))
      .filter((item): item is SerpItem => Boolean(item))
  }

  const markdownSource =
    (isPlainObject(parsed) && typeof parsed['result'] === 'string' ? (parsed['result'] as string) : null) ||
    (typeof result === 'string' ? result : null)

  return markdownSource ? extractMarkdownSerpItems(markdownSource) : []
}

export function extractBrightDataFirstRecord(result: unknown): Record<string, unknown> | null {
  const payload = normalizeResultPayload(result)
  const parsed = parseResultObject(payload ?? result)
  const candidates: unknown[] = []

  const base = parsed ?? payload ?? result
  const arrayCandidate = extractArrayCandidate(base)

  if (arrayCandidate) {
    candidates.push(...arrayCandidate)
  } else if (isPlainObject(base)) {
    candidates.push(base as Record<string, unknown>)
  }

  const first = candidates.find((item) => isPlainObject(item)) as Record<string, unknown> | undefined
  return first ?? null
}

export function extractBrightDataArray(result: unknown): Record<string, unknown>[] {
  const payload = normalizeResultPayload(result)
  const parsed = parseResultObject(payload ?? result)
  const candidates: unknown[] = []

  const base = parsed ?? payload ?? result
  const arrayCandidate = extractArrayCandidate(base)

  if (arrayCandidate) {
    candidates.push(...arrayCandidate)
  }

  return candidates.filter((item): item is Record<string, unknown> => isPlainObject(item))
}
