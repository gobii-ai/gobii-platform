import { useState } from 'react'
import { act, fireEvent, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useTimelineScrollController } from './useTimelineScrollController'

type HarnessProps = {
  contentVersion: string
}

let animationFrameCallbacks: Map<number, FrameRequestCallback>
let nextAnimationFrameId: number

function createScrollViewport({
  clientHeight = 400,
  scrollHeight = 1_000,
  scrollTop = 500,
} = {}): HTMLDivElement {
  const viewport = document.createElement('div')
  const currentScrollHeight = scrollHeight
  let currentScrollTop = scrollTop

  Object.defineProperties(viewport, {
    clientHeight: {
      configurable: true,
      get: () => clientHeight,
    },
    scrollHeight: {
      configurable: true,
      get: () => currentScrollHeight,
    },
    scrollTop: {
      configurable: true,
      get: () => currentScrollTop,
      set: (value: number) => {
        currentScrollTop = Math.max(0, Math.min(value, currentScrollHeight - clientHeight))
      },
    },
  })

  return viewport
}

function flushNextAnimationFrame() {
  const next = animationFrameCallbacks.entries().next().value as [number, FrameRequestCallback] | undefined
  if (!next) {
    return
  }
  animationFrameCallbacks.delete(next[0])
  act(() => next[1](0))
}

function flushAnimationFrames() {
  for (let frame = 0; frame < 20 && animationFrameCallbacks.size > 0; frame += 1) {
    const callbacks = Array.from(animationFrameCallbacks.values())
    animationFrameCallbacks.clear()
    act(() => callbacks.forEach((callback) => callback(frame)))
  }
  expect(animationFrameCallbacks.size).toBe(0)
}

function useScrollHarness({ contentVersion }: HarnessProps) {
  const [pinned, setPinned] = useState(true)
  const controller = useTimelineScrollController({
    activeAgentId: 'agent-1',
    autoScrollPinned: pinned,
    contentVersion,
    eventCount: 1,
    fetchNextPage: async () => undefined,
    fetchPreviousPage: async () => undefined,
    hasNextPage: false,
    hasPreviousPage: false,
    initialLoading: false,
    isFetchNextPageError: false,
    isFetchPreviousPageError: false,
    isFetchingNextPage: false,
    isFetchingPreviousPage: false,
    isNewAgent: false,
    pageCount: 1,
    setAutoScrollPinned: setPinned,
    switchingAgentId: null,
  })
  return { controller, pinned }
}

describe('useTimelineScrollController', () => {
  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(10_000)
    animationFrameCallbacks = new Map()
    nextAnimationFrameId = 0
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      nextAnimationFrameId += 1
      animationFrameCallbacks.set(nextAnimationFrameId, callback)
      return nextAnimationFrameId
    })
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      animationFrameCallbacks.delete(id)
    })
    vi.stubGlobal('ResizeObserver', class ResizeObserver {
      observe() {}
      disconnect() {}
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('does not re-pin when an upward wheel overtakes a live Thinking scroll event', () => {
    const { result, rerender } = renderHook(
      ({ contentVersion }: HarnessProps) => useScrollHarness({ contentVersion }),
      { initialProps: { contentVersion: 'thinking:1' } },
    )
    const viewport = createScrollViewport()

    act(() => result.current.controller.timelineRef(viewport))
    flushNextAnimationFrame()
    expect(viewport.scrollTop).toBe(600)

    // The bottom assignment has landed, but its queued scroll event has not.
    fireEvent.wheel(viewport, { deltaY: -40 })
    expect(result.current.pinned).toBe(false)
    viewport.scrollTop = 550
    fireEvent.scroll(viewport)

    expect(result.current.pinned).toBe(false)
    rerender({ contentVersion: 'thinking:2' })
    flushAnimationFrames()
    expect(viewport.scrollTop).toBe(550)
  })

  it('preserves a native manual position through Thinking updates and resumes at the live edge', () => {
    const { result, rerender } = renderHook(
      ({ contentVersion }: HarnessProps) => useScrollHarness({ contentVersion }),
      { initialProps: { contentVersion: 'thinking:1' } },
    )
    const viewport = createScrollViewport()

    act(() => result.current.controller.timelineRef(viewport))
    flushNextAnimationFrame()
    expect(viewport.scrollTop).toBe(600)

    viewport.scrollTop = 300
    fireEvent.scroll(viewport)

    expect(result.current.pinned).toBe(false)
    rerender({ contentVersion: 'thinking:2' })
    flushAnimationFrames()
    expect(viewport.scrollTop).toBe(300)

    rerender({ contentVersion: 'thinking:done' })
    flushAnimationFrames()
    expect(viewport.scrollTop).toBe(300)

    // Scrolling back to the live edge only re-pins when a real gesture drove it; a bare scroll
    // event could just as well be a reflow moving the content under the reader.
    fireEvent.wheel(viewport, { deltaY: 120 })
    viewport.scrollTop = 550
    fireEvent.scroll(viewport)
    expect(result.current.pinned).toBe(true)

    rerender({ contentVersion: 'reply:1' })
    flushAnimationFrames()
    expect(viewport.scrollTop).toBe(600)
  })

  it('does not re-pin a parked reader when a reflow lands them near the bottom', () => {
    const { result } = renderHook(
      ({ contentVersion }: HarnessProps) => useScrollHarness({ contentVersion }),
      { initialProps: { contentVersion: 'thinking:1' } },
    )
    const viewport = createScrollViewport()

    act(() => result.current.controller.timelineRef(viewport))
    flushNextAnimationFrame()

    // The reader scrolls up and parks.
    fireEvent.wheel(viewport, { deltaY: -120 })
    viewport.scrollTop = 300
    fireEvent.scroll(viewport)
    expect(result.current.pinned).toBe(false)

    // Time passes with no further input. A layout change — a stream card collapsing — fires a
    // scroll event that leaves them near the bottom through no action of their own.
    vi.spyOn(Date, 'now').mockReturnValue(60_000)
    viewport.scrollTop = 560
    fireEvent.scroll(viewport)

    expect(result.current.pinned).toBe(false)
  })
})
