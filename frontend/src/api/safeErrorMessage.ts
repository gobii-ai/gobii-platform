import { HttpError } from './http'

export function safeErrorMessage(error: unknown): string {
  if (error instanceof HttpError) {
    const body = error.body
    if (body && typeof body === 'object') {
      const maybeDetail = (body as { detail?: unknown }).detail
      const maybeError = (body as { error?: unknown }).error
      if (typeof maybeDetail === 'string' && maybeDetail.trim()) {
        return maybeDetail
      }
      if (typeof maybeError === 'string' && maybeError.trim()) {
        return maybeError
      }
    }
    if (typeof body === 'string' && body.trim()) {
      return body
    }
    return 'Request failed. Please try again.'
  }
  if (error instanceof Error && error.message) {
    return error.message
  }
  return 'Request failed. Please try again.'
}

