import { clearStoredConsoleContext, readStoredConsoleContext } from '../util/consoleContextStorage'

export class HttpError extends Error {
  public readonly status: number
  public readonly statusText: string
  public readonly body: unknown

  constructor(status: number, statusText: string, body: unknown) {
    super(`${status} ${statusText}`)
    this.status = status
    this.statusText = statusText
    this.body = body
  }
}

let loginRedirectScheduled = false

const MARKETING_CONTEXT_HEADER_PATHS = new Set(['/contact', '/prequalify', '/qualify'])

function getBrowserTimeZone(): string | null {
  try {
    if (typeof Intl === 'undefined' || typeof Intl.DateTimeFormat !== 'function') {
      return null
    }
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone
    if (typeof tz !== 'string') {
      return null
    }
    const normalized = tz.trim()
    return normalized || null
  } catch {
    return null
  }
}

function applyConsoleContextHeaders(headers: Headers): boolean {
  const context = readStoredConsoleContext()
  if (!context) {
    return false
  }
  let applied = false
  if (!headers.has('X-Gobii-Context-Type')) {
    headers.set('X-Gobii-Context-Type', context.type)
    applied = true
  }
  if (!headers.has('X-Gobii-Context-Id')) {
    headers.set('X-Gobii-Context-Id', context.id)
    applied = true
  }
  return applied
}

function normalizePathname(pathname: string): string {
  const cleaned = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname
  return cleaned.toLowerCase()
}

function isMarketingContextHeaderPath(pathname: string): boolean {
  return MARKETING_CONTEXT_HEADER_PATHS.has(normalizePathname(pathname))
}

function resolvePathname(input: RequestInfo | URL): string | null {
  try {
    if (input instanceof URL) {
      return input.pathname
    }
    if (typeof Request !== 'undefined' && input instanceof Request) {
      return new URL(input.url).pathname
    }
    const base = typeof window !== 'undefined' ? window.location.href : 'http://localhost/'
    return new URL(String(input), base).pathname
  } catch {
    return null
  }
}

function shouldApplyConsoleContextHeaders(input: RequestInfo | URL): boolean {
  const requestPathname = resolvePathname(input)
  return !requestPathname || !isMarketingContextHeaderPath(requestPathname)
}

export function buildLoginUrl(nextUrl?: string): string {
  if (typeof window === 'undefined') {
    const params = nextUrl ? `?next=${encodeURIComponent(nextUrl)}` : ''
    return `/accounts/login/${params}`
  }
  const next = nextUrl || `${window.location.pathname}${window.location.search}${window.location.hash}` || '/'
  return `/accounts/login/?next=${encodeURIComponent(next)}`
}

function isLoginPath(url: string): boolean {
  try {
    const parsed = new URL(url, typeof window !== 'undefined' ? window.location.origin : undefined)
    return parsed.pathname.startsWith('/accounts/login')
  } catch {
    return false
  }
}

export function scheduleLoginRedirect(nextUrl?: string): void {
  if (typeof window === 'undefined') {
    return
  }
  if (loginRedirectScheduled) {
    return
  }
  if (window.location.pathname.startsWith('/accounts/login')) {
    return
  }
  loginRedirectScheduled = true
  window.location.assign(buildLoginUrl(nextUrl))
}

function maybeRedirectToLogin(response: Response): void {
  if (typeof window === 'undefined') {
    return
  }
  if (loginRedirectScheduled) {
    return
  }
  const needsRedirect = response.status === 401 || (response.redirected && isLoginPath(response.url))
  if (!needsRedirect) {
    return
  }
  scheduleLoginRedirect()
}

async function jsonFetchInternal<T>(
  input: RequestInfo | URL,
  init: RequestInit,
  allowRetry: boolean,
): Promise<T> {
  const { headers: initHeaders, ...restInit } = init
  const headers = new Headers(initHeaders ?? undefined)

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }
  if (!headers.has('X-Gobii-Timezone')) {
    const browserTimeZone = getBrowserTimeZone()
    if (browserTimeZone) {
      headers.set('X-Gobii-Timezone', browserTimeZone)
    }
  }
  const appliedContextHeaders = shouldApplyConsoleContextHeaders(input) ? applyConsoleContextHeaders(headers) : false

  const response = await fetch(input, {
    credentials: 'same-origin',
    ...restInit,
    headers,
  })

  maybeRedirectToLogin(response)

  const contentType = response.headers.get('content-type') ?? ''
  const isJson = contentType.includes('application/json')

  let payload: unknown = null
  try {
    if (response.status !== 204) {
      payload = isJson ? await response.json() : await response.text()
    }
  } catch (error) {
    // Ignore JSON parse errors for non-JSON payloads.
    if (isJson) {
      throw error
    }
  }

  if (!response.ok) {
    if (allowRetry && appliedContextHeaders && response.status === 403) {
      clearStoredConsoleContext()
      const retryHeaders = new Headers(initHeaders ?? undefined)
      retryHeaders.delete('X-Gobii-Context-Type')
      retryHeaders.delete('X-Gobii-Context-Id')
      return jsonFetchInternal<T>(
        input,
        {
          ...restInit,
          headers: retryHeaders,
        },
        false,
      )
    }
    throw new HttpError(response.status, response.statusText, payload)
  }

  return (payload === null ? undefined : (payload as T)) as T
}

export async function jsonFetch<T>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  return jsonFetchInternal(input, init, true)
}

function getCsrfCookieName(): string {
  if (typeof document === 'undefined') {
    return 'csrftoken'
  }
  const meta = document.querySelector('meta[name="csrf-cookie-name"]')
  const name = meta?.getAttribute('content')?.trim()
  return name || 'csrftoken'
}

function getCookieValue(name: string): string {
  if (typeof document === 'undefined') {
    return ''
  }
  const cookies = document.cookie.split(';')
  for (const cookie of cookies) {
    const [rawKey, ...rest] = cookie.trim().split('=')
    if (rawKey === name) {
      return decodeURIComponent(rest.join('='))
    }
  }
  return ''
}

export function getCsrfToken(): string {
  return getCookieValue(getCsrfCookieName())
}

type JsonRequestInit = RequestInit & {
  json?: unknown
  includeCsrf?: boolean
  csrfToken?: string
}

export async function jsonRequest<T>(input: RequestInfo | URL, init: JsonRequestInit = {}): Promise<T> {
  const { json, includeCsrf = false, csrfToken = '', headers, ...rest } = init
  const finalHeaders = new Headers(headers ?? undefined)
  if (json !== undefined) {
    finalHeaders.set('Content-Type', 'application/json')
  }
  if (includeCsrf) {
    finalHeaders.set('X-CSRFToken', csrfToken || getCsrfToken())
  }

  const body = json !== undefined ? JSON.stringify(json) : rest.body

  return jsonFetch<T>(input, {
    ...rest,
    headers: finalHeaders,
    body,
  })
}
