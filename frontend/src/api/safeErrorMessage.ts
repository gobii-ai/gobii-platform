import { HttpError } from './http'

const DEFAULT_ERROR_MESSAGE = 'Request failed. Please try again.'

export function safeErrorMessage(error: unknown, fallback = DEFAULT_ERROR_MESSAGE): string {
  if (error instanceof HttpError) {
    const body = error.body
    if (body && typeof body === 'object') {
      for (const key of ['message', 'detail', 'error']) {
        const value = (body as Record<string, unknown>)[key]
        if (typeof value === 'string' && value.trim()) {
          return value
        }
      }
    }
    if (typeof body === 'string' && body.trim()) {
      if (isHtmlResponse(body)) {
        return fallback
      }
      return body
    }
    if (typeof error.statusText === 'string' && error.statusText.trim()) {
      return error.statusText
    }
    return fallback
  }
  if (error && typeof error === 'object' && 'message' in error) {
    const message = (error as { message: unknown }).message
    if (typeof message === 'string' && message.trim()) {
      if (isHtmlResponse(message)) {
        return fallback
      }
      return message
    }
  }
  return fallback
}

function isHtmlResponse(value: string): boolean {
  const normalized = value.slice(0, 200).toLowerCase()
  return normalized.includes('<!doctype') || normalized.includes('<html') || normalized.includes('<body')
}
