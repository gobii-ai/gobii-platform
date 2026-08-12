import { describe, expect, it, vi } from 'vitest'

import {
  getMessageSearchShortcutLabel,
  handleMessageSearchShortcut,
  isMessageSearchShortcut,
} from './chatShortcuts'

function shortcutEvent(overrides: Partial<Parameters<typeof isMessageSearchShortcut>[0]> = {}) {
  return {
    altKey: false,
    ctrlKey: false,
    defaultPrevented: false,
    key: 'k',
    metaKey: true,
    preventDefault: vi.fn(),
    shiftKey: false,
    ...overrides,
  }
}

describe('isMessageSearchShortcut', () => {
  it('handles unmodified Cmd/Ctrl+K for message search', () => {
    expect(isMessageSearchShortcut(shortcutEvent())).toBe(true)
    expect(isMessageSearchShortcut(shortcutEvent({ metaKey: false, ctrlKey: true }))).toBe(true)

    const preventDefault = vi.fn()
    const openSearch = vi.fn()
    expect(handleMessageSearchShortcut(shortcutEvent({ preventDefault }), openSearch)).toBe(true)
    expect(preventDefault).toHaveBeenCalledOnce()
    expect(openSearch).toHaveBeenCalledOnce()
  })

  it('passes Cmd/Ctrl+F through to native page find', () => {
    const preventDefault = vi.fn()
    const openSearch = vi.fn()
    const event = shortcutEvent({ key: 'f', preventDefault })

    expect(handleMessageSearchShortcut(event, openSearch)).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
    expect(openSearch).not.toHaveBeenCalled()
    expect(isMessageSearchShortcut(shortcutEvent({ key: 'f', metaKey: false, ctrlKey: true }))).toBe(false)
  })

  it('ignores modified Cmd/Ctrl+K combinations', () => {
    expect(isMessageSearchShortcut(shortcutEvent({ shiftKey: true }))).toBe(false)
    expect(isMessageSearchShortcut(shortcutEvent({ altKey: true }))).toBe(false)
  })
})

describe('getMessageSearchShortcutLabel', () => {
  it('uses the platform-appropriate modifier label', () => {
    expect(getMessageSearchShortcutLabel('MacIntel')).toBe('⌘K')
    expect(getMessageSearchShortcutLabel('Win32')).toBe('Ctrl K')
    expect(getMessageSearchShortcutLabel('Linux x86_64')).toBe('Ctrl K')
  })
})
