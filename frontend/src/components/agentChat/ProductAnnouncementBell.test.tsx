import { act, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ProductAnnouncementBell } from './ProductAnnouncementBell'

const { useProductAnnouncementsMock } = vi.hoisted(() => ({
  useProductAnnouncementsMock: vi.fn(),
}))

vi.mock('../../hooks/useProductAnnouncements', () => ({
  useProductAnnouncements: (enabled: boolean) => {
    useProductAnnouncementsMock(enabled)
    return {
      data: { announcements: [], unreadCount: 0, hasUnread: false, recentLimit: 5 },
      isLoading: false,
      error: null,
    }
  },
  useMarkProductAnnouncementsRead: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

describe('ProductAnnouncementBell', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    useProductAnnouncementsMock.mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('defers announcement loading until browser idle fallback', async () => {
    render(<ProductAnnouncementBell />)

    expect(useProductAnnouncementsMock).toHaveBeenLastCalledWith(false)
    await act(() => vi.advanceTimersByTimeAsync(1_500))
    expect(useProductAnnouncementsMock).toHaveBeenLastCalledWith(true)
  })
})

