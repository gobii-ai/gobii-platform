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

  const displayedLengthRef = useRef(0)
  const animationFrameRef = useRef<number | null>(null)
  const lastUpdateTimeRef = useRef(Date.now())
  const waitingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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
    // Disabled mode: show content immediately
    if (disabled) {
      setDisplayedContent(targetContent)
      displayedLengthRef.current = targetContent.length
      setIsWaiting(false)
      setIsAnimating(false)
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
        const newLength = Math.min(currentLength + charsPerFrame, targetLength)
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
  }, [targetContent, isStreaming, charsPerFrame, frameIntervalMs, waitingThresholdMs, disabled, cancelAnimation])

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
    displayedContent: disabled ? targetContent : displayedContent,
    isWaiting: disabled ? false : isWaiting,
    isAnimating: disabled ? false : isAnimating,
  }
}
