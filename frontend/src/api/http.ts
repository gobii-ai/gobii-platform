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
  const response = await fetch(input, {
    credentials: 'same-origin',
    headers: {
      Accept: 'application/json',
      ...(init.headers ?? {}),
    },
    ...init,
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
