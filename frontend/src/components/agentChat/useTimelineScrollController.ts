import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefCallback,
} from 'react'

const NEAR_BOTTOM_PX = 96
const UNPIN_PX = 160
const TOP_LOAD_PX = 160
const PROGRAMMATIC_SCROLL_MS = 180
const SCROLLABLE_EPSILON_PX = 1
const PREPEND_RESTORE_GUARD_MS = 250

type TimelineScrollControllerOptions = {
  activeAgentId: string | null
  autoScrollPinned: boolean
  contentVersion: string
  eventCount: number
  fetchPreviousPage: () => Promise<unknown>
  hasMoreOlder: boolean
  hasPreviousPage: boolean
  initialLoading: boolean
  isFetchPreviousPageError: boolean
  isFetchingPreviousPage: boolean
  isNewAgent: boolean
  loadingOlder: boolean
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
  hasMoreOlder,
  hasPreviousPage,
  initialLoading,
  isFetchPreviousPageError,
  isFetchingPreviousPage,
  isNewAgent,
  loadingOlder,
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

  const [timelineNode, setTimelineNode] = useState<HTMLDivElement | null>(null)
  const [contentNode, setContentNode] = useState<HTMLDivElement | null>(null)
  const [composerNode, setComposerNode] = useState<HTMLDivElement | null>(null)
  const [isNearBottom, setIsNearBottom] = useState(true)
  const [timelineCanScroll, setTimelineCanScroll] = useState(false)

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
    const scrollable = canScroll(container)
    setTimelineCanScroll((current) => (current === scrollable ? current : scrollable))
  }, [])

  const scrollToBottomNow = useCallback(() => {
    const container = containerRef.current
    if (!container) {
      return
    }
    programmaticScrollUntilRef.current = Date.now() + PROGRAMMATIC_SCROLL_MS
    container.scrollTop = container.scrollHeight
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

  const requestPreviousPage = useCallback(() => {
    if (
      fetchOlderInFlightRef.current
      || !hasPreviousPage
      || isFetchingPreviousPage
      || isFetchPreviousPageError
    ) {
      return
    }

    cancelPendingBottomScroll()
    prependAnchorRef.current = capturePrependAnchor()
    setPinned(false)
    fetchOlderInFlightRef.current = true
    void fetchPreviousPage().finally(() => {
      fetchOlderInFlightRef.current = false
    })
  }, [
    fetchPreviousPage,
    hasPreviousPage,
    isFetchPreviousPageError,
    isFetchingPreviousPage,
    pageCount,
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
  }, [activeAgentId])

  useEffect(() => {
    const container = timelineNode
    if (!container) {
      return
    }

    const handleScroll = () => {
      const previousScrollTop = lastScrollTopRef.current
      const nextScrollTop = container.scrollTop
      lastScrollTopRef.current = nextScrollTop
      syncMeasurements(container)

      if (
        Date.now() < programmaticScrollUntilRef.current
        || Date.now() < ignorePinUntilRef.current
        || prependAnchorRef.current
      ) {
        return
      }

      const distance = bottomDistance(container)
      if (distance <= NEAR_BOTTOM_PX) {
        setPinned(true)
      } else if (distance > UNPIN_PX && nextScrollTop < previousScrollTop - 2) {
        setPinned(false)
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
    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [
    eventCount,
    initialLoading,
    isNewAgent,
    requestPreviousPage,
    setPinned,
    switchingAgentId,
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
    if (!container || typeof ResizeObserver === 'undefined') {
      return
    }

    const observer = new ResizeObserver(() => {
      syncMeasurements(container)
      if (pinnedRef.current && !prependAnchorRef.current) {
        scrollToBottomAcrossFrames(2)
      }
    })
    observer.observe(container)
    if (contentNode) {
      observer.observe(contentNode)
    }
    if (composerNode) {
      observer.observe(composerNode)
    }
    return () => observer.disconnect()
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

  const showOlderLoadButton = (
    !initialLoading
    && !isNewAgent
    && !switchingAgentId
    && eventCount > 0
    && hasMoreOlder
    && !loadingOlder
    && !timelineCanScroll
  )

  return {
    autoScrollPinnedRef: pinnedRef,
    isNearBottom,
    pinAndJumpToBottom,
    requestPreviousPage,
    scrollOnComposerFocus,
    scrollToBottom,
    showOlderLoadButton,
    timelineContentRef,
    timelineRef,
    composerShellRef,
  }
}
