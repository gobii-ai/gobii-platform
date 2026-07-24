import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefCallback } from 'react'

import { revealTimelineMessage } from '../../util/timelineNavigation'

const NEAR_BOTTOM_PX = 96
const TOP_LOAD_PX = 160
// Older history is fetched while the reader still has this much of it left above them, measured
// in viewports. A page of timeline renders far taller than the window and takes about a second to
// arrive, so a small fixed trigger meant every reader scrolling back hit the top, stopped, waited,
// and was then repositioned once the page landed. Starting the fetch with runway to spare keeps
// the content arriving before they get there. It also keeps the insert off scrollTop 0, where the
// browser suspends its own scroll anchoring because there is nothing above to anchor to.
const TOP_LOAD_VIEWPORTS = 1.5
const PROGRAMMATIC_SCROLL_MS = 180
const SCROLLABLE_EPSILON_PX = 1
const PREPEND_RESTORE_GUARD_MS = 250
const USER_SCROLL_DELTA_PX = 2
// How long the anchored row is held in place after older history is inserted. It covers the late
// measurement of prepended cards -- highlighting, JSON viewers, images, charts -- which lands after
// the commit that inserted them. Any reader input ends the hold immediately.
const ANCHOR_HOLD_MS = 1200
const ANCHOR_DRIFT_EPSILON_PX = 0.5
const ANCHOR_SETTLED_FRAMES = 4

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
  targetMessageId?: string | null
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
  key: string | null
  offsetTop: number
  pageCount: number
  scrollHeight: number
}

type AnchorHold = {
  anchor: PrependAnchor
  expiresAt: number
  frame: number | null
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
  targetMessageId = null,
}: TimelineScrollControllerOptions) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const pinnedRef = useRef(autoScrollPinned)
  const didInitialJumpRef = useRef(false)
  const fetchOlderInFlightRef = useRef(false)
  const scrollFrameRef = useRef<number | null>(null)
  const acrossFramesRafRef = useRef<number | null>(null)
  const contentLayoutGuardRafRef = useRef<number | null>(null)
  const followupScrollFramesRef = useRef(0)
  const programmaticScrollUntilRef = useRef(0)
  const contentLayoutChangingRef = useRef(false)
  const previousContentVersionRef = useRef(contentVersion)
  const prependAnchorRef = useRef<PrependAnchor | null>(null)
  const anchorHoldRef = useRef<AnchorHold | null>(null)
  const anchorRefreshFrameRef = useRef<number | null>(null)
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

  const guardContentLayoutChange = useCallback(() => {
    contentLayoutChangingRef.current = true
    if (contentLayoutGuardRafRef.current !== null) {
      window.cancelAnimationFrame(contentLayoutGuardRafRef.current)
    }
    contentLayoutGuardRafRef.current = window.requestAnimationFrame(() => {
      contentLayoutGuardRafRef.current = null
      contentLayoutChangingRef.current = false
    })
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
      return { element: null, key: null, offsetTop: 0, pageCount, scrollHeight: 0 }
    }

    const containerTop = container.getBoundingClientRect().top
    const items = Array.from(content.querySelectorAll<HTMLElement>('[data-timeline-item="true"]'))
    const element = items.find((item) => item.getBoundingClientRect().bottom >= containerTop) ?? items[0] ?? null
    return {
      element,
      // Remembered by identity as well as by node: merging older history can re-render the row,
      // and a detached node cannot tell us where the reader was looking.
      key: element?.dataset.timelineKey ?? null,
      offsetTop: element ? element.getBoundingClientRect().top - containerTop : 0,
      pageCount,
      scrollHeight: container.scrollHeight,
    }
  }, [contentNode, pageCount])

  /** Put the anchored row back where it was. Returns true when it had to be moved. */
  const applyPrependAnchor = useCallback((anchor: PrependAnchor): boolean => {
    const container = containerRef.current
    if (!container) {
      return false
    }

    let element = anchor.element && anchor.element.isConnected ? anchor.element : null
    if (!element && anchor.key) {
      element = container.querySelector<HTMLElement>(`[data-timeline-key="${CSS.escape(anchor.key)}"]`)
    }
    if (!element) {
      return false
    }

    const containerTop = container.getBoundingClientRect().top
    const drift = (element.getBoundingClientRect().top - containerTop) - anchor.offsetTop
    if (Math.abs(drift) < ANCHOR_DRIFT_EPSILON_PX) {
      return false
    }
    container.scrollTop += drift
    lastScrollTopRef.current = container.scrollTop
    return true
  }, [])

  const stopAnchorHold = useCallback(() => {
    const hold = anchorHoldRef.current
    if (hold?.frame != null) {
      window.cancelAnimationFrame(hold.frame)
    }
    anchorHoldRef.current = null
  }, [])

  /**
   * Keep the anchored row pinned while the newly inserted history settles.
   *
   * A single correction at commit time is not enough: prepended cards finish measuring after that
   * commit -- code highlighting, JSON viewers, images, charts -- and every late resize above the
   * reader moves the content under them with nothing to put it back. Re-assert the offset each
   * frame for a short window instead, and stop the moment the reader scrolls.
   */
  const startAnchorHold = useCallback((anchor: PrependAnchor) => {
    stopAnchorHold()
    const hold: AnchorHold = { anchor, expiresAt: Date.now() + ANCHOR_HOLD_MS, frame: null }
    anchorHoldRef.current = hold

    let settledFrames = 0
    const step = () => {
      if (anchorHoldRef.current !== hold) {
        return
      }
      const moved = applyPrependAnchor(anchor)
      settledFrames = moved ? 0 : settledFrames + 1
      // Once nothing has shifted for several frames the prepended content has finished
      // measuring, so there is no reason to keep reading layout every frame.
      if (settledFrames >= ANCHOR_SETTLED_FRAMES || Date.now() >= hold.expiresAt) {
        anchorHoldRef.current = null
        return
      }
      hold.frame = window.requestAnimationFrame(step)
    }
    hold.frame = window.requestAnimationFrame(step)
  }, [applyPrependAnchor, stopAnchorHold])

  const restorePrependAnchor = useCallback(() => {
    const container = containerRef.current
    const anchor = prependAnchorRef.current
    if (!container || !anchor) {
      return
    }

    const anchorStillRendered = Boolean(
      (anchor.element && anchor.element.isConnected)
      || (anchor.key && container.querySelector(`[data-timeline-key="${CSS.escape(anchor.key)}"]`)),
    )
    applyPrependAnchor(anchor)
    if (!anchorStillRendered && anchor.scrollHeight > 0) {
      // The row is gone entirely; preserving distance from the bottom is the best remaining guess.
      container.scrollTop += container.scrollHeight - anchor.scrollHeight
      lastScrollTopRef.current = container.scrollTop
    }

    startAnchorHold(anchor)

    ignorePinUntilRef.current = Date.now() + PREPEND_RESTORE_GUARD_MS
    prependAnchorRef.current = null
    syncMeasurements(container)
  }, [applyPrependAnchor, startAnchorHold, syncMeasurements])

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
    // A hold from the previous page must not keep asserting an old offset against this one.
    stopAnchorHold()
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
    stopAnchorHold,
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
    stopAnchorHold()
    pointerActiveRef.current = false
    touchYRef.current = null
  }, [activeAgentId, targetMessageId])

  useEffect(() => {
    const container = timelineNode
    if (!container) {
      return
    }

    const handleWheel = (event: WheelEvent) => {
      // The reader is driving again; never move the viewport out from under them.
      stopAnchorHold()
      if (event.deltaY < 0 && canScrollUp(container)) {
        suspendAutoFollow()
      }
    }

    const handleTouchStart = (event: TouchEvent) => {
      stopAnchorHold()
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
      stopAnchorHold()
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

      // Older history takes about a second to arrive and the reader keeps scrolling the whole
      // time. An anchor measured when the fetch started describes where they were, not where they
      // are, and restoring it would drag them back by everything they scrolled in between.
      // Re-measure on every scroll so the restore uses the last frame before the insert.
      const pendingAnchor = prependAnchorRef.current
      if (pendingAnchor && anchorRefreshFrameRef.current === null) {
        // Scroll events can outpace frames; one refresh per frame is all the restore can use.
        anchorRefreshFrameRef.current = window.requestAnimationFrame(() => {
          anchorRefreshFrameRef.current = null
          const stillPending = prependAnchorRef.current
          if (stillPending) {
            prependAnchorRef.current = { ...capturePrependAnchor(), pageCount: stillPending.pageCount }
          }
        })
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

      if (meaningfulScrollUp && !contentLayoutChangingRef.current) {
        suspendAutoFollow()
      } else if (scrollingDown && distance <= NEAR_BOTTOM_PX) {
        setPinned(true)
      }

      if (
        container.scrollTop <= Math.max(TOP_LOAD_PX, container.clientHeight * TOP_LOAD_VIEWPORTS)
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
    capturePrependAnchor,
    eventCount,
    initialLoading,
    isNewAgent,
    requestPreviousPage,
    setPinned,
    stopAnchorHold,
    switchingAgentId,
    suspendAutoFollow,
    syncMeasurements,
    timelineNode,
  ])

  useLayoutEffect(() => {
    if (previousContentVersionRef.current === contentVersion) {
      return
    }
    previousContentVersionRef.current = contentVersion
    // React content replacement can move scrollTop without user input. Keep
    // native scroll detection enabled again once the committed layout settles.
    guardContentLayoutChange()
  }, [contentVersion, guardContentLayoutChange])

  useLayoutEffect(() => {
    const anchor = prependAnchorRef.current
    if (!anchor) {
      return
    }

    if (pageCount > anchor.pageCount) {
      restorePrependAnchor()
      return
    }

    // fetchOlderInFlightRef is set synchronously when the fetch is requested, while the query's
    // own flag only turns true a tick later. Consulting the flag alone let any re-render landing
    // in that gap throw the anchor away, so the page arrived with nothing holding the reader's
    // place -- one of the intermittent jumps.
    if (!isFetchingPreviousPage && !fetchOlderInFlightRef.current) {
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
      if (targetMessageId && contentNode) {
        const timeout = revealTimelineMessage(targetMessageId, {
          root: contentNode,
          highlight: true,
        })
        if (timeout !== null) {
          setPinned(false)
          return () => window.clearTimeout(timeout)
        }
      }
      pinAndJumpToBottom()
    }
  }, [contentNode, eventCount, initialLoading, isNewAgent, pinAndJumpToBottom, setPinned, targetMessageId])

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
      guardContentLayoutChange()
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
  }, [composerNode, contentNode, guardContentLayoutChange, scrollToBottomAcrossFrames, syncMeasurements, timelineNode])

  useEffect(() => () => {
    if (scrollFrameRef.current !== null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
    }
    if (acrossFramesRafRef.current !== null) {
      window.cancelAnimationFrame(acrossFramesRafRef.current)
    }
    if (contentLayoutGuardRafRef.current !== null) {
      window.cancelAnimationFrame(contentLayoutGuardRafRef.current)
    }
    contentLayoutChangingRef.current = false
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
