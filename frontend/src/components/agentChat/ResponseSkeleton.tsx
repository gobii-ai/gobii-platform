import { useEffect, useRef, useCallback, useLayoutEffect } from 'react'
import { getEstimatedResponseTime, recordResponseTime } from '../../util/responseTimeTracker'

// Progress tuning constants
const BASE_TARGET_PCT = 88
const TAIL_TARGET_PCT = 100
const TAIL_DURATION_MULTIPLIER = 2.5
const MAX_INITIAL_ELAPSED_MS = 15000
const INITIAL_PROGRESS_CAP = 12
const SMOOTHING_FACTOR = 0.08
const MAX_STEP_PER_FRAME = 1.5

// Visual constants
const RING_R = 16
const CIRC = 2 * Math.PI * RING_R // ≈ 100.53
const MIN_ARC = 0.12 // always show at least 12% of the ring
const MAX_ARC = 0.88 // leave a small gap at full progress

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

function calculateProgress(elapsed: number, estimated: number): number {
  const safeEstimated = Math.max(estimated, 1000)
  const clampedElapsed = Math.max(0, elapsed)
  const normalized = Math.min(1, clampedElapsed / safeEstimated)
  const baseProgress = easeOutCubic(normalized) * BASE_TARGET_PCT

  if (normalized < 1) return Math.min(baseProgress, TAIL_TARGET_PCT)

  const tailDuration = safeEstimated * TAIL_DURATION_MULTIPLIER
  const overtime = clampedElapsed - safeEstimated
  const tailProgress = Math.min(overtime / tailDuration, 1) * (TAIL_TARGET_PCT - BASE_TARGET_PCT)
  return Math.min(baseProgress + tailProgress, TAIL_TARGET_PCT)
}

function getDisplayStartTime(startTime?: number | null): number {
  const now = Date.now()
  if (!startTime) return now
  return Math.max(startTime, now - MAX_INITIAL_ELAPSED_MS)
}

function arcDashArray(progress: number): string {
  const frac = MIN_ARC + (progress / 100) * (MAX_ARC - MIN_ARC)
  return `${frac * CIRC} ${CIRC}`
}

type ResponseSkeletonProps = {
  startTime?: number | null
  hidden?: boolean
}

export function ResponseSkeleton({ startTime, hidden }: ResponseSkeletonProps) {
  const arcRef = useRef<SVGCircleElement>(null)
  const progressRef = useRef(0)
  const displayStartRef = useRef(getDisplayStartTime(startTime))
  const actualStartRef = useRef(startTime ?? Date.now())
  const estimateRef = useRef(getEstimatedResponseTime())
  const rafRef = useRef<number | null>(null)

  const syncArc = useCallback(() => {
    if (arcRef.current) {
      arcRef.current.style.strokeDasharray = arcDashArray(progressRef.current)
    }
  }, [])

  // Reset on startTime change — useLayoutEffect prevents flash
  useLayoutEffect(() => {
    displayStartRef.current = getDisplayStartTime(startTime)
    actualStartRef.current = startTime ?? Date.now()
    estimateRef.current = getEstimatedResponseTime()
    const target = calculateProgress(Date.now() - displayStartRef.current, estimateRef.current)
    progressRef.current = Math.min(target, INITIAL_PROGRESS_CAP)
    syncArc()
  }, [startTime, syncArc])

  const animate = useCallback(() => {
    const target = calculateProgress(Date.now() - displayStartRef.current, estimateRef.current)
    const cur = progressRef.current
    const delta = target - cur
    if (Math.abs(delta) >= 0.05) {
      const step = Math.sign(delta) * Math.min(Math.abs(delta) * SMOOTHING_FACTOR, MAX_STEP_PER_FRAME)
      progressRef.current = cur + step
      syncArc()
    }
    rafRef.current = requestAnimationFrame(animate)
  }, [syncArc])

  useEffect(() => {
    const startVal = actualStartRef.current
    const delay = startTime ? 0 : 80
    const timer = setTimeout(() => {
      rafRef.current = requestAnimationFrame(animate)
    }, delay)
    return () => {
      clearTimeout(timer)
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      const dur = Date.now() - startVal
      if (dur >= 200 && dur <= 60000) recordResponseTime(dur)
    }
  }, [animate, startTime])

  return (
    <div
      className="response-throbber-container"
      hidden={hidden}
      aria-hidden={hidden ? 'true' : undefined}
    >
      <div className="response-throbber" role="status" aria-label="Processing">
        <svg viewBox="0 0 44 44" className="throbber-svg" aria-hidden="true">
          <defs>
            <linearGradient id="throbber-grad" gradientUnits="userSpaceOnUse" x1="0" y1="44" x2="44" y2="0">
              <stop offset="0%" stopColor="#8b5cf6" />
              <stop offset="100%" stopColor="#7c3aed" />
            </linearGradient>
          </defs>

          {/* Faint track ring */}
          <circle cx="22" cy="22" r={RING_R} className="throbber-track" />

          {/* Progress arc — length driven by JS, rotation by CSS */}
          <circle
            ref={arcRef}
            cx="22"
            cy="22"
            r={RING_R}
            className="throbber-arc"
            strokeDasharray={`${MIN_ARC * CIRC} ${CIRC}`}
          />

          {/* Inner dashed decorative ring */}
          <circle cx="22" cy="22" r="10" className="throbber-inner" />

          {/* Outer fine decorative ring */}
          <circle cx="22" cy="22" r="19.5" className="throbber-outer" />
        </svg>

        {/* Breathing center core */}
        <div className="throbber-core" />
      </div>
    </div>
  )
}
