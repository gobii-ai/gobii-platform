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

export function apiErrorMessages(error: unknown, fallback = DEFAULT_ERROR_MESSAGE): string[] {
  if (error instanceof HttpError && error.body && typeof error.body === 'object') {
    const body = error.body as Record<string, unknown>
    if (body.errors && typeof body.errors === 'object') {
      const messages = Object.values(body.errors as Record<string, unknown>).flatMap((value) => (
        Array.isArray(value) ? value.map(String) : [String(value)]
      ))
      if (messages.length > 0) {
        return messages
      }
    }
    if (body.error) {
      return [String(body.error)]
    }
  }
  return [safeErrorMessage(error, fallback)]
}

export function fieldErrorMessages(
  field: string,
  errors?: Record<string, string[]> | null,
): string[] {
  if (!errors) {
    return []
  }
  return errors[field] || errors[toSnakeCase(field)] || []
}

function toSnakeCase(value: string): string {
  return value.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`)
}

function isHtmlResponse(value: string): boolean {
  const normalized = value.slice(0, 200).toLowerCase()
  return normalized.includes('<!doctype') || normalized.includes('<html') || normalized.includes('<body')
}
