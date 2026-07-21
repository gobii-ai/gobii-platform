import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefCallback } from 'react'

const NEAR_BOTTOM_PX = 96
const TOP_LOAD_PX = 160
const PROGRAMMATIC_SCROLL_MS = 180
const SCROLLABLE_EPSILON_PX = 1
const PREPEND_RESTORE_GUARD_MS = 250
const USER_SCROLL_DELTA_PX = 2

type TimelineScrollControllerOptions = {
  activeAgentId: string | null
  autoScrollPinned: boolean
  contentVersion: string
  eventCount: number
  fetchPreviousPage: () => Promise<unknown>
  hasPreviousPage: boolean
  initialLoading: boolean
  isFetchPreviousPageError: boolean
  isFetchingPreviousPage: boolean
  isNewAgent: boolean
  pageCount: number
  setAutoScrollPinned: (pinned: boolean) => void
  switchingAgentId: string | null
}

function bottomDistance(container: HTMLElement): number {
  return container.scrollHeight - container.scrollTop - container.clientHeight
}

function canScroll(container: HTMLElement | null): boolean {
  return Boolean(container && container.scrollHeight > container.clientHeight + SCROLLABLE_EPSILON_PX)
}

function canScrollUp(container: HTMLElement): boolean {
  return canScroll(container) && container.scrollTop > 0
}

function isEditableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (
    target.isContentEditable
    || target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target instanceof HTMLSelectElement
  )
}

type PrependAnchor = {
  element: HTMLElement | null
  offsetTop: number
  pageCount: number
  scrollHeight: number
}

export function useTimelineScrollController({
  activeAgentId,
  autoScrollPinned,
  contentVersion,
  eventCount,
  fetchPreviousPage,
  hasPreviousPage,
  initialLoading,
  isFetchPreviousPageError,
  isFetchingPreviousPage,
  isNewAgent,
  pageCount,
  setAutoScrollPinned,
  switchingAgentId,
}: TimelineScrollControllerOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const pinnedRef = useRef(autoScrollPinned)
  const didInitialJumpRef = useRef(false)
  const fetchOlderInFlightRef = useRef(false)
  const scrollFrameRef = useRef<number | null>(null)
  const acrossFramesRafRef = useRef<number | null>(null)
  const followupScrollFramesRef = useRef(0)
  const programmaticScrollUntilRef = useRef(0)
  const prependAnchorRef = useRef<PrependAnchor | null>(null)
  const ignorePinUntilRef = useRef(0)
  const lastScrollTopRef = useRef(0)
  const pointerActiveRef = useRef(false)
  const touchYRef = useRef<number | null>(null)

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const [contentNode, setContentNode] = useState<HTMLDivElement | null>(null)
  const [composerNode, setComposerNode] = useState<HTMLDivElement | null>(null)
  const [isNearBottom, setIsNearBottom] = useState(true)

  useEffect(() => {
    pinnedRef.current = autoScrollPinned
  }, [autoScrollPinned])

  const setPinned = useCallback((nextPinned: boolean) => {
    if (pinnedRef.current === nextPinned) {
      return
    }
    pinnedRef.current = nextPinned
    setAutoScrollPinned(nextPinned)
  }, [setAutoScrollPinned])

  const syncMeasurements = useCallback((container = containerRef.current) => {
    if (!container) {
      return
    }
    const nearBottom = bottomDistance(container) <= NEAR_BOTTOM_PX
    setIsNearBottom((current) => (current === nearBottom ? current : nearBottom))
  }, [])

  const scrollToBottomNow = useCallback(() => {
    const container = containerRef.current
    if (!container) {
      return
    }
    programmaticScrollUntilRef.current = Date.now() + PROGRAMMATIC_SCROLL_MS
    container.scrollTop = container.scrollHeight
    lastScrollTopRef.current = container.scrollTop
    setIsNearBottom(true)
  }, [])

  const scrollToBottom = useCallback(() => {
    if (scrollFrameRef.current !== null) {
      return
    }
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null
      scrollToBottomNow()
    })
  }, [scrollToBottomNow])

  const cancelPendingBottomScroll = useCallback(() => {
    followupScrollFramesRef.current = 0
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
      scrollFrameRef.current = null
    }
    if (acrossFramesRafRef.current !== null) {
      window.cancelAnimationFrame(acrossFramesRafRef.current)
      acrossFramesRafRef.current = null
    }
  }, [])

  const suspendAutoFollow = useCallback(() => {
    programmaticScrollUntilRef.current = 0
    cancelPendingBottomScroll()
    setPinned(false)
  }, [cancelPendingBottomScroll, setPinned])

  const scrollToBottomAcrossFrames = useCallback((frames: number) => {
    if (acrossFramesRafRef.current !== null) {
      window.cancelAnimationFrame(acrossFramesRafRef.current)
      acrossFramesRafRef.current = null
    }
    followupScrollFramesRef.current = frames
    const run = () => {
      if (followupScrollFramesRef.current <= 0) {
        acrossFramesRafRef.current = null
        return
      }
      followupScrollFramesRef.current -= 1
      scrollToBottomNow()
      acrossFramesRafRef.current = window.requestAnimationFrame(run)
    }
    acrossFramesRafRef.current = window.requestAnimationFrame(run)
  }, [scrollToBottomNow])

  const capturePrependAnchor = useCallback((): PrependAnchor => {
    const container = containerRef.current
    const content = contentNode
    if (!container || !content) {
      return { element: null, offsetTop: 0, pageCount, scrollHeight: 0 }
    }

    const containerTop = container.getBoundingClientRect().top
    const items = Array.from(content.querySelectorAll<HTMLElement>('[data-timeline-item="true"]'))
    const element = items.find((item) => item.getBoundingClientRect().bottom >= containerTop) ?? items[0] ?? null
    return {
      element,
      offsetTop: element ? element.getBoundingClientRect().top - containerTop : 0,
      pageCount,
      scrollHeight: container.scrollHeight,
    }
  }, [contentNode, pageCount])

  const restorePrependAnchor = useCallback(() => {
    const container = containerRef.current
    const anchor = prependAnchorRef.current
    if (!container || !anchor) {
      return
    }

    if (anchor.element && anchor.element.isConnected) {
      const containerTop = container.getBoundingClientRect().top
      const nextOffsetTop = anchor.element.getBoundingClientRect().top - containerTop
      container.scrollTop += nextOffsetTop - anchor.offsetTop
    } else if (anchor.scrollHeight > 0) {
      container.scrollTop += container.scrollHeight - anchor.scrollHeight
    }
    lastScrollTopRef.current = container.scrollTop

    ignorePinUntilRef.current = Date.now() + PREPEND_RESTORE_GUARD_MS
    prependAnchorRef.current = null
    syncMeasurements(container)
  }, [syncMeasurements])

  const pinAndJumpToBottom = useCallback(() => {
    pinnedRef.current = true
    setAutoScrollPinned(true)
    scrollToBottomNow()
    scrollToBottomAcrossFrames(3)
  }, [scrollToBottomAcrossFrames, scrollToBottomNow, setAutoScrollPinned])

  const requestPreviousPage = useCallback((options?: { preservePinned?: boolean }) => {
    if (
      fetchOlderInFlightRef.current
      || !hasPreviousPage
      || isFetchingPreviousPage
      || isFetchPreviousPageError
    ) {
      return
    }

    cancelPendingBottomScroll()
    const shouldRestorePinned = Boolean(options?.preservePinned && pinnedRef.current)
    prependAnchorRef.current = capturePrependAnchor()
    if (!options?.preservePinned) {
      setPinned(false)
    }
    fetchOlderInFlightRef.current = true
    void fetchPreviousPage().finally(() => {
      fetchOlderInFlightRef.current = false
      if (shouldRestorePinned) {
        setPinned(true)
      }
    })
  }, [
    fetchPreviousPage,
    hasPreviousPage,
    isFetchPreviousPageError,
    isFetchingPreviousPage,
    cancelPendingBottomScroll,
    capturePrependAnchor,
    setPinned,
  ])

  const timelineRef: RefCallback<HTMLDivElement> = useCallback((node) => {
    containerRef.current = node
    lastScrollTopRef.current = node?.scrollTop ?? 0
    setTimelineNode(node)
    syncMeasurements(node)
  }, [syncMeasurements])

  const timelineContentRef: RefCallback<HTMLDivElement> = useCallback((node) => {
    setContentNode(node)
  }, [])

  const composerShellRef: RefCallback<HTMLDivElement> = useCallback((node) => {
    setComposerNode(node)
  }, [])

  useEffect(() => {
    didInitialJumpRef.current = false
    fetchOlderInFlightRef.current = false
    prependAnchorRef.current = null
    pointerActiveRef.current = false
    touchYRef.current = null
  }, [activeAgentId])

  useEffect(() => {
    const container = timelineNode
    if (!container) {
      return
    }

    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY < 0 && canScrollUp(container)) {
        suspendAutoFollow()
      }
    }

    const handleTouchStart = (event: TouchEvent) => {
      touchYRef.current = event.touches[0]?.clientY ?? null
    }

    const handleTouchMove = (event: TouchEvent) => {
      const nextTouchY = event.touches[0]?.clientY ?? null
      const previousTouchY = touchYRef.current
      touchYRef.current = nextTouchY
      if (
        nextTouchY !== null
        && previousTouchY !== null
        && nextTouchY > previousTouchY + USER_SCROLL_DELTA_PX
        && canScrollUp(container)
      ) {
        suspendAutoFollow()
      }
    }

    const handleTouchEnd = () => {
      touchYRef.current = null
    }

    const handlePointerDown = () => {
      pointerActiveRef.current = true
    }

    const handlePointerEnd = () => {
      pointerActiveRef.current = false
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (isEditableTarget(event.target) || !canScrollUp(container)) {
        return
      }
      const scrollsUp = event.key === 'ArrowUp'
        || event.key === 'PageUp'
        || event.key === 'Home'
        || (event.key === ' ' && event.shiftKey)
      if (scrollsUp) {
        suspendAutoFollow()
      }
    }

    const handleScroll = () => {
      const previousScrollTop = lastScrollTopRef.current
      const nextScrollTop = container.scrollTop
      const scrollingUp = nextScrollTop < previousScrollTop
      const meaningfulScrollUp = nextScrollTop < previousScrollTop - USER_SCROLL_DELTA_PX
      const scrollingDown = nextScrollTop > previousScrollTop
      lastScrollTopRef.current = nextScrollTop
      syncMeasurements(container)

      if (scrollingUp && pointerActiveRef.current) {
        suspendAutoFollow()
      }

      const distance = bottomDistance(container)
      // A bottom-follow write cannot leave the viewport beyond the live-edge threshold.
      const movedAwayFromLiveEdge = meaningfulScrollUp && distance > NEAR_BOTTOM_PX
      if (
        (
          Date.now() < programmaticScrollUntilRef.current
          && !movedAwayFromLiveEdge
        )
        || Date.now() < ignorePinUntilRef.current
        || prependAnchorRef.current
      ) {
        return
      }

      // Layout changes can move scrollTop upward without user input. Wheel, touch,
      // pointer, and keyboard handlers above own the decision to stop following.
      if (scrollingDown && distance <= NEAR_BOTTOM_PX) {
        setPinned(true)
      }

      if (
        container.scrollTop <= TOP_LOAD_PX
        && canScroll(container)
        && didInitialJumpRef.current
        && !initialLoading
        && !isNewAgent
        && !switchingAgentId
        && eventCount > 0
      ) {
        requestPreviousPage()
      }
    }

    syncMeasurements(container)
    container.addEventListener('wheel', handleWheel, { passive: true })
    container.addEventListener('touchstart', handleTouchStart, { passive: true })
    container.addEventListener('touchmove', handleTouchMove, { passive: true })
    container.addEventListener('touchend', handleTouchEnd, { passive: true })
    container.addEventListener('touchcancel', handleTouchEnd, { passive: true })
    container.addEventListener('pointerdown', handlePointerDown, { passive: true })
    container.addEventListener('scroll', handleScroll, { passive: true })
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('pointerup', handlePointerEnd, { passive: true })
    window.addEventListener('pointercancel', handlePointerEnd, { passive: true })
    return () => {
      container.removeEventListener('wheel', handleWheel)
      container.removeEventListener('touchstart', handleTouchStart)
      container.removeEventListener('touchmove', handleTouchMove)
      container.removeEventListener('touchend', handleTouchEnd)
      container.removeEventListener('touchcancel', handleTouchEnd)
      container.removeEventListener('pointerdown', handlePointerDown)
      container.removeEventListener('scroll', handleScroll)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('pointerup', handlePointerEnd)
      window.removeEventListener('pointercancel', handlePointerEnd)
    }
  }, [
    eventCount,
    initialLoading,
    isNewAgent,
    requestPreviousPage,
    setPinned,
    switchingAgentId,
    suspendAutoFollow,
    syncMeasurements,
    timelineNode,
  ])

  useLayoutEffect(() => {
    const anchor = prependAnchorRef.current
    if (!anchor) {
      return
    }

    if (pageCount > anchor.pageCount) {
      restorePrependAnchor()
      return
    }

    if (!isFetchingPreviousPage) {
      prependAnchorRef.current = null
    }
  }, [contentVersion, isFetchingPreviousPage, pageCount, restorePrependAnchor])

  useEffect(() => {
    if (isNewAgent) {
      didInitialJumpRef.current = true
      pinAndJumpToBottom()
      return
    }

    if (!initialLoading && eventCount > 0 && !didInitialJumpRef.current) {
      didInitialJumpRef.current = true
      pinAndJumpToBottom()
    }
  }, [eventCount, initialLoading, isNewAgent, pinAndJumpToBottom])

  useEffect(() => {
    syncMeasurements()
    if (pinnedRef.current && !prependAnchorRef.current) {
      scrollToBottomAcrossFrames(2)
    }
  }, [contentVersion, scrollToBottomAcrossFrames, syncMeasurements])

  useEffect(() => {
    const container = timelineNode
    if (
      !container
      || initialLoading
      || isNewAgent
      || switchingAgentId
      || eventCount === 0
      || !hasPreviousPage
      || isFetchPreviousPageError
      || isFetchingPreviousPage
      || canScroll(container)
    ) {
      return
    }

    requestPreviousPage({ preservePinned: true })
  }, [
    contentVersion,
    eventCount,
    hasPreviousPage,
    initialLoading,
    isFetchPreviousPageError,
    isFetchingPreviousPage,
    isNewAgent,
    requestPreviousPage,
    switchingAgentId,
    timelineNode,
  ])

  useEffect(() => {
    const container = timelineNode
    if (!container || typeof ResizeObserver === 'undefined') {
      return
    }

    const updateComposerHeight = () => {
      if (!composerNode) {
        return
      }
      const height = composerNode.getBoundingClientRect().height
      document.documentElement.style.setProperty('--composer-height', `${height}px`)
      document.getElementById('jump-to-latest')?.style.setProperty('--composer-height', `${height}px`)
    }

    const observer = new ResizeObserver(() => {
      syncMeasurements(container)
      updateComposerHeight()
      if (pinnedRef.current && !prependAnchorRef.current) {
        scrollToBottomAcrossFrames(2)
      }
    })
    updateComposerHeight()
    observer.observe(container)
    if (contentNode) {
      observer.observe(contentNode)
    }
    if (composerNode) {
      observer.observe(composerNode)
    }
    return () => {
      observer.disconnect()
      document.documentElement.style.removeProperty('--composer-height')
      document.getElementById('jump-to-latest')?.style.removeProperty('--composer-height')
    }
  }, [composerNode, contentNode, scrollToBottomAcrossFrames, syncMeasurements, timelineNode])

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
    }
    if (acrossFramesRafRef.current !== null) {
      window.cancelAnimationFrame(acrossFramesRafRef.current)
    }
    followupScrollFramesRef.current = 0
  }, [])

  const scrollOnComposerFocus = useCallback(() => {
    if (typeof window === 'undefined') {
      return
    }
    const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0
    if (isTouch) {
      pinAndJumpToBottom()
    }
  }, [pinAndJumpToBottom])

  return {
    autoScrollPinnedRef: pinnedRef,
    isNearBottom,
    pinAndJumpToBottom,
    scrollOnComposerFocus,
    scrollToBottom,
    timelineContentRef,
    timelineRef,
    composerShellRef,
  }
}
