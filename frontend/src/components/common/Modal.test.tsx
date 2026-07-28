/**
 * Back (browser button or mobile swipe) must dismiss an open dialog in place rather
 * than navigate the app underneath it, and closing any other way must not leave
 * stray history entries behind.
 */
import { render, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { Modal } from './Modal'

function openModal(onClose: () => void, extra: Partial<Parameters<typeof Modal>[0]> = {}) {
  return render(
    <Modal title="Test dialog" onClose={onClose} {...extra}>
      <p>body</p>
    </Modal>,
  )
}

describe('Modal history dismissal', () => {
  it('pushes a sentinel entry on open and closes on back', async () => {
    const onClose = vi.fn()
    openModal(onClose)

    expect(window.history.state?.__modalKey).toBeTruthy()

    window.history.back()
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })

  it('consumes the sentinel when closed without the back gesture', async () => {
    const onClose = vi.fn()
    const { unmount } = openModal(onClose)

    expect(window.history.state?.__modalKey).toBeTruthy()
    unmount()

    await waitFor(() => expect(window.history.state?.__modalKey).toBeUndefined())
    expect(onClose).not.toHaveBeenCalled()
  })

  it('does not touch history when not dismissible', () => {
    const before = window.history.state?.__modalKey
    const onClose = vi.fn()
    const { unmount } = openModal(onClose, { dismissible: false })

    expect(window.history.state?.__modalKey).toBe(before)
    unmount()
  })

  it('closes only the top dialog of a stack per back gesture', async () => {
    const closeOuter = vi.fn()
    const closeInner = vi.fn()
    const outer = openModal(closeOuter)
    const inner = openModal(closeInner)

    window.history.back()
    await waitFor(() => expect(closeInner).toHaveBeenCalledTimes(1))
    expect(closeOuter).not.toHaveBeenCalled()

    inner.unmount()
    window.history.back()
    await waitFor(() => expect(closeOuter).toHaveBeenCalledTimes(1))
    outer.unmount()
  })
})
