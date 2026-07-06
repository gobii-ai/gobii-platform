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
})
