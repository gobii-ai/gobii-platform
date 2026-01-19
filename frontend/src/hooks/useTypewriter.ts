import { useState, useEffect, useRef, useCallback } from 'react'

type TypewriterOptions = {
  /** Characters to reveal per animation frame (default: 2) */
  charsPerFrame?: number
  /** Milliseconds between animation frames (default: 16 ~60fps) */
  frameIntervalMs?: number
  /** How long to wait before showing "waiting" state (default: 150ms) */
  waitingThresholdMs?: number
  /** Disable typewriter effect (shows content immediately) */
  disabled?: boolean
}

type TypewriterResult = {
  /** The text to display (may lag behind targetContent) */
  displayedContent: string
  /** True when we've caught up to target and are waiting for more */
  isWaiting: boolean
  /** True when animation is in progress */
  isAnimating: boolean
}

function getAdaptiveCharsPerFrame(base: number, contentLength: number): number {
  if (contentLength <= 600) return base
  if (contentLength <= 1600) return Math.max(base, 4)
  if (contentLength <= 3600) return Math.max(base, 6)
  if (contentLength <= 7000) return Math.max(base, 8)
  return Math.max(base, 12)
}

/**
 * Typewriter effect hook that animates text character-by-character.
 * Creates perceived lower latency by smoothing out network chunk delivery.
 *
 * @param targetContent - The full content received so far from network
 * @param isStreaming - Whether we're still receiving content
 * @param options - Animation configuration
 */
export function useTypewriter(
  targetContent: string,
  isStreaming: boolean,
  options: TypewriterOptions = {}
): TypewriterResult {
  const {
    charsPerFrame = 2,
    frameIntervalMs = 16,
    waitingThresholdMs = 150,
    disabled = false,
  } = options

  const [displayedContent, setDisplayedContent] = useState('')
  const [isWaiting, setIsWaiting] = useState(false)
  const [isAnimating, setIsAnimating] = useState(false)
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(false)
  const [isPageVisible, setIsPageVisible] = useState(true)

  const displayedLengthRef = useRef(0)
  const animationFrameRef = useRef<number | null>(null)
  const lastUpdateTimeRef = useRef(Date.now())
  const waitingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const targetContentRef = useRef(targetContent)

  // Track when target content changes (new network data arrived)
  const prevTargetLengthRef = useRef(0)

  const cancelAnimation = useCallback(() => {
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current)
      animationFrameRef.current = null
    }
    if (waitingTimeoutRef.current !== null) {
      clearTimeout(waitingTimeoutRef.current)
      waitingTimeoutRef.current = null
    }
  }, [])

  useEffect(() => {
    targetContentRef.current = targetContent
  }, [targetContent])

  useEffect(() => {
    if (typeof window === 'undefined' || !('matchMedia' in window)) {
      return
    }
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const legacyMedia = media as MediaQueryList & {
      addListener?: (listener: (event: MediaQueryListEvent) => void) => void
      removeListener?: (listener: (event: MediaQueryListEvent) => void) => void
    }
    const update = () => setPrefersReducedMotion(media.matches)
    update()
    if (typeof legacyMedia.addEventListener === 'function') {
      legacyMedia.addEventListener('change', update)
      return () => legacyMedia.removeEventListener('change', update)
    }
    if (typeof legacyMedia.addListener === 'function') {
      legacyMedia.addListener(update)
      return () => legacyMedia.removeListener?.(update)
    }
  }, [])

  useEffect(() => {
    if (typeof document === 'undefined') {
      return
    }
    const handleVisibilityChange = () => {
      const visible = document.visibilityState !== 'hidden'
      setIsPageVisible(visible)
      if (!visible) {
        setDisplayedContent(targetContentRef.current)
        displayedLengthRef.current = targetContentRef.current.length
        setIsWaiting(false)
        setIsAnimating(false)
        cancelAnimation()
      }
    }
    handleVisibilityChange()
    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange)
  }, [cancelAnimation])

  const adaptiveCharsPerFrame = getAdaptiveCharsPerFrame(charsPerFrame, targetContent.length)
  const motionDisabled = disabled || prefersReducedMotion || !isPageVisible

  useEffect(() => {
    // Disabled mode: show content immediately
    if (motionDisabled) {
      setDisplayedContent(targetContent)
      displayedLengthRef.current = targetContent.length
      setIsWaiting(false)
      setIsAnimating(false)
      cancelAnimation()
      return
    }

    // Not streaming and caught up: show everything
    if (!isStreaming && displayedLengthRef.current >= targetContent.length) {
      setDisplayedContent(targetContent)
      displayedLengthRef.current = targetContent.length
      setIsWaiting(false)
      setIsAnimating(false)
      return
    }

    // New content arrived - cancel waiting state
    if (targetContent.length > prevTargetLengthRef.current) {
      lastUpdateTimeRef.current = Date.now()
      setIsWaiting(false)
      if (waitingTimeoutRef.current) {
        clearTimeout(waitingTimeoutRef.current)
        waitingTimeoutRef.current = null
      }
    }
    prevTargetLengthRef.current = targetContent.length

    // Animation loop
    let lastFrameTime = 0
    const animate = (timestamp: number) => {
      // Throttle to frameIntervalMs
      if (timestamp - lastFrameTime < frameIntervalMs) {
        animationFrameRef.current = requestAnimationFrame(animate)
        return
      }
      lastFrameTime = timestamp

      const currentLength = displayedLengthRef.current
      const targetLength = targetContent.length

      if (currentLength < targetLength) {
        // Reveal more characters
        const newLength = Math.min(currentLength + adaptiveCharsPerFrame, targetLength)
        displayedLengthRef.current = newLength
        setDisplayedContent(targetContent.slice(0, newLength))
        setIsAnimating(true)
        animationFrameRef.current = requestAnimationFrame(animate)
      } else {
        // Caught up to target
        setIsAnimating(false)

        if (isStreaming) {
          // Still streaming but caught up - start waiting timer
          const timeSinceUpdate = Date.now() - lastUpdateTimeRef.current
          if (timeSinceUpdate >= waitingThresholdMs) {
            setIsWaiting(true)
          } else if (!waitingTimeoutRef.current) {
            waitingTimeoutRef.current = setTimeout(() => {
              setIsWaiting(true)
              waitingTimeoutRef.current = null
            }, waitingThresholdMs - timeSinceUpdate)
          }
        } else {
          setIsWaiting(false)
        }
      }
    }

    // Start animation if needed
    if (displayedLengthRef.current < targetContent.length) {
      if (animationFrameRef.current === null) {
        animationFrameRef.current = requestAnimationFrame(animate)
      }
    } else if (isStreaming) {
      // Already caught up but streaming - just check waiting state
      const timeSinceUpdate = Date.now() - lastUpdateTimeRef.current
      if (timeSinceUpdate >= waitingThresholdMs) {
        setIsWaiting(true)
      }
    }

    return cancelAnimation
  }, [adaptiveCharsPerFrame, cancelAnimation, frameIntervalMs, isStreaming, motionDisabled, targetContent, waitingThresholdMs])

  // Reset when content is cleared/reset
  useEffect(() => {
    if (targetContent.length === 0) {
      displayedLengthRef.current = 0
      setDisplayedContent('')
      setIsWaiting(false)
      setIsAnimating(false)
      cancelAnimation()
    }
  }, [targetContent, cancelAnimation])

  // Cleanup on unmount
  useEffect(() => {
    return cancelAnimation
  }, [cancelAnimation])

  return {
    displayedContent: motionDisabled ? targetContent : displayedContent,
    isWaiting: motionDisabled ? false : isWaiting,
    isAnimating: motionDisabled ? false : isAnimating,
  }
}
