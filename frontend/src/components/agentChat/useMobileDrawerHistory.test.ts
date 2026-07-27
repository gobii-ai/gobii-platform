/**
 * The Android back gesture is the reflexive way to dismiss an overlay; with no history entry
 * for the mobile drawer it navigated the browser instead (bug #432). These pin the marker-entry
 * contract the hook keeps with the real history stack.
 */
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMobileDrawerHistory } from './useMobileDrawerHistory'

const KEY = '__gobiiMobileDrawer'

function firePop(state: unknown) {
  act(() => {
    window.dispatchEvent(new PopStateEvent('popstate', { state }))
  })
}

describe('useMobileDrawerHistory', () => {
  beforeEach(() => {
    window.history.replaceState({}, '')
  })

  it('pushes a marker entry when the drawer opens on mobile', () => {
    const push = vi.spyOn(window.history, 'pushState')
    const { rerender } = renderHook(
      ({ open }) => useMobileDrawerHistory(open, true, () => {}),
      { initialProps: { open: false } },
    )
    expect(push).not.toHaveBeenCalled()

    rerender({ open: true })

    expect(push).toHaveBeenCalledTimes(1)
    expect((push.mock.calls[0][0] as Record<string, unknown>)[KEY]).toBe(true)
    push.mockRestore()
  })

  it('closes the drawer when the back gesture lands below the marker', () => {
    const close = vi.fn()
    renderHook(() => useMobileDrawerHistory(true, true, close))

    firePop({})

    expect(close).toHaveBeenCalledTimes(1)
  })

  it('steps through a ghost marker left by a UI close instead of eating the back press', () => {
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    renderHook(() => useMobileDrawerHistory(false, true, () => {}))

    firePop({ [KEY]: true })

    expect(back).toHaveBeenCalledTimes(1)
    back.mockRestore()
  })

  it('does nothing on desktop', () => {
    const push = vi.spyOn(window.history, 'pushState')
    const close = vi.fn()
    const { rerender } = renderHook(
      ({ open }) => useMobileDrawerHistory(open, false, close),
      { initialProps: { open: false } },
    )
    rerender({ open: true })
    firePop({})

    expect(push).not.toHaveBeenCalled()
    expect(close).not.toHaveBeenCalled()
    push.mockRestore()
  })

  it('sheds a stale marker left behind by a reload', () => {
    window.history.replaceState({ [KEY]: true, other: 1 }, '')

    renderHook(() => useMobileDrawerHistory(false, true, () => {}))

    const state = window.history.state as Record<string, unknown>
    expect(state[KEY]).toBeUndefined()
    expect(state.other).toBe(1)
  })
})
