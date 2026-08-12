import { describe, expect, it } from 'vitest'

import { formatAbsoluteTimestamp } from './time'

describe('formatAbsoluteTimestamp', () => {
  it('includes the date, seconds, and viewer timezone', () => {
    expect(formatAbsoluteTimestamp('2026-08-12T19:24:18Z', 'America/New_York'))
      .toBe('Aug 12, 2026, 3:24:18 PM EDT')
  })

  it('returns null for missing or invalid timestamps', () => {
    expect(formatAbsoluteTimestamp(null, 'America/New_York')).toBeNull()
    expect(formatAbsoluteTimestamp('not-a-date', 'America/New_York')).toBeNull()
  })

  it('falls back to the browser timezone when the configured timezone is invalid', () => {
    expect(formatAbsoluteTimestamp('2026-08-12T19:24:18Z', 'Not/A_Timezone')).toBeTruthy()
  })
})
