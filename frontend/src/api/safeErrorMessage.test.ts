import { describe, expect, it } from 'vitest'

import { HttpError } from './http'
import { safeErrorMessage } from './safeErrorMessage'

describe('safeErrorMessage', () => {
  it('uses JSON error details when available', () => {
    const error = new HttpError(400, 'Bad Request', { detail: 'Use a valid URL.' })

    expect(safeErrorMessage(error, 'Unable to save MCP server.')).toBe('Use a valid URL.')
  })

  it('does not expose HTML error pages', () => {
    const error = new HttpError(
      500,
      'Internal Server Error',
      '<!doctype html><html><body>Internal error</body></html>',
    )

    expect(safeErrorMessage(error, 'Unable to save MCP server.')).toBe('Unable to save MCP server.')
  })

  it('falls back to statusText when body is empty or not useful', () => {
    const error = new HttpError(401, 'Unauthorized', null)

    expect(safeErrorMessage(error, 'Fallback message.')).toBe('Unauthorized')
  })

  it('handles plain objects with a message property', () => {
    const error = { message: 'Custom plain error message' }

    expect(safeErrorMessage(error, 'Fallback message.')).toBe('Custom plain error message')
  })
})
