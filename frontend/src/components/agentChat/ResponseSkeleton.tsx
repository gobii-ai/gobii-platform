import { useState, useEffect, useRef, useCallback } from 'react'
import { getEstimatedResponseTime, recordResponseTime } from '../../util/responseTimeTracker'

// Progress tuning constants
const BASE_TARGET_PCT = 88 // Where we aim to be around the estimated time
const TAIL_TARGET_PCT = 100 // Slow creep upper bound
const TAIL_DURATION_MULTIPLIER = 2.5 // Extra time (multiplied by estimate) to reach the tail target
const MAX_INITIAL_ELAPSED_MS = 15000 // Cap how old a start time can be for the visual display
const INITIAL_PROGRESS_CAP = 12 // Keep the initial visual start low to avoid jumpy entrances
const SMOOTHING_FACTOR = 0.08 // Fraction of remaining gap to move per frame
const MAX_STEP_PER_FRAME = 1.5 // Clamp per-frame movement to stay steady

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

// Smooth curve that keeps moving: fast early, then slow creep toward 100%
function calculateProgress(elapsed: number, estimated: number): number {
  const safeEstimated = Math.max(estimated, 1000)
  const clampedElapsed = Math.max(0, elapsed)
  const normalized = Math.min(1, clampedElapsed / safeEstimated)

  // Front half: ease-out up to BASE_TARGET_PCT by the estimated time
  const baseProgress = easeOutCubic(normalized) * BASE_TARGET_PCT

  if (normalized < 1) {
    return Math.min(baseProgress, TAIL_TARGET_PCT)
  }

  // Tail: gentle linear creep toward 100% over an additional window
  const tailDuration = safeEstimated * TAIL_DURATION_MULTIPLIER
  const overtime = clampedElapsed - safeEstimated
  const tailProgress = Math.min(overtime / tailDuration, 1) * (TAIL_TARGET_PCT - BASE_TARGET_PCT)

  return Math.min(baseProgress + tailProgress, TAIL_TARGET_PCT)
}

function getDisplayStartTime(startTime?: number | null): number {
  const now = Date.now()
  if (!startTime) return now
  const lowerBound = now - MAX_INITIAL_ELAPSED_MS
  return Math.max(startTime, lowerBound)
}

type ResponseSkeletonProps = {
  startTime?: number | null
  hidden?: boolean
}

export function ResponseSkeleton({ startTime, hidden }: ResponseSkeletonProps) {
  // Separate the real start (for metrics) from the display start (for the visual cap)
  const displayStartTime = getDisplayStartTime(startTime)
  const initialTargetProgress = calculateProgress(Date.now() - displayStartTime, getEstimatedResponseTime())
  const initialProgress = Math.min(initialTargetProgress, INITIAL_PROGRESS_CAP)

  const [progress, setProgress] = useState(initialProgress)
  const progressRef = useRef(initialProgress)
  const displayStartTimeRef = useRef(displayStartTime)
  const actualStartTimeRef = useRef(startTime ?? Date.now())
  const estimatedTimeRef = useRef(getEstimatedResponseTime())
  const rafRef = useRef<number | null>(null)

  // Reset when startTime changes (e.g., new tool call, thinking, or message)
  useEffect(() => {
    const newActualStartTime = startTime ?? Date.now()
    const newDisplayStartTime = getDisplayStartTime(startTime)
    actualStartTimeRef.current = newActualStartTime
    displayStartTimeRef.current = newDisplayStartTime
    estimatedTimeRef.current = getEstimatedResponseTime()
    const nextTarget = calculateProgress(Date.now() - newDisplayStartTime, estimatedTimeRef.current)
    const nextProgress = Math.min(nextTarget, INITIAL_PROGRESS_CAP)
    progressRef.current = nextProgress
    setProgress(nextProgress)
  }, [startTime])

  const animate = useCallback(() => {
    const elapsed = Date.now() - displayStartTimeRef.current
    const targetProgress = calculateProgress(elapsed, estimatedTimeRef.current)

    setProgress((current) => {
      const delta = targetProgress - current
      if (Math.abs(delta) < 0.05) {
        progressRef.current = current
        return current
      }
      const step = Math.sign(delta) * Math.min(Math.abs(delta) * SMOOTHING_FACTOR, MAX_STEP_PER_FRAME)
      const next = current + step
      progressRef.current = next
      return next
    })

    rafRef.current = requestAnimationFrame(animate)
  }, [])

  useEffect(() => {
    const startTimeValue = actualStartTimeRef.current

    // Small delay for smoother entrance (only if starting fresh)
    const delay = startTime ? 0 : 80
    const timer = setTimeout(() => {
      rafRef.current = requestAnimationFrame(animate)
    }, delay)

    return () => {
      clearTimeout(timer)
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
      }
      // Record the actual response time when component unmounts
      const duration = Date.now() - startTimeValue
      // Only record reasonable durations (between 200ms and 60s)
      if (duration >= 200 && duration <= 60000) {
        recordResponseTime(duration)
      }
    }
  }, [animate, startTime])

  return (
    <div
      className="response-progress-container"
      hidden={hidden}
      aria-hidden={hidden ? 'true' : undefined}
    >
      <div className="response-progress-track">
        <div
          className="response-progress-fill"
          style={{ width: `${progress}%` }}
        >
          <div className="response-progress-glow" />
        </div>
      </div>
    </div>
  )
}
