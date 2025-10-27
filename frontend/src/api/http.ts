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

export async function jsonFetch<T>(input: RequestInfo | URL, init: RequestInit = {}): Promise<T> {
  const { headers: initHeaders, ...restInit } = init
  const headers = new Headers(initHeaders ?? undefined)

  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  const response = await fetch(input, {
    credentials: 'same-origin',
    ...restInit,
    headers,
  })

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
    throw new HttpError(response.status, response.statusText, payload)
  }

  return (payload === null ? undefined : (payload as T)) as T
}

export function getCsrfToken(): string {
  if (typeof document === 'undefined') {
    return ''
  }
  const match = document.cookie.match(/csrftoken=([^;]+)/)
  return match ? decodeURIComponent(match[1]) : ''
}

type JsonRequestInit = RequestInit & {
  json?: unknown
  includeCsrf?: boolean
}

export async function jsonRequest<T>(input: RequestInfo | URL, init: JsonRequestInit = {}): Promise<T> {
  const { json, includeCsrf = false, headers, ...rest } = init
  const finalHeaders = new Headers(headers ?? undefined)
  if (json !== undefined) {
    finalHeaders.set('Content-Type', 'application/json')
  }
  if (includeCsrf) {
    finalHeaders.set('X-CSRFToken', getCsrfToken())
  }

  const body = json !== undefined ? JSON.stringify(json) : rest.body

  return jsonFetch<T>(input, {
    ...rest,
    headers: finalHeaders,
    body,
  })
}
