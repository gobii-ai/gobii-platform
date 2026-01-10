import { useState, useEffect, useRef, useCallback } from 'react'
import { getEstimatedResponseTime, recordResponseTime } from '../../util/responseTimeTracker'

// Asymptotic ease-out: smooth, satisfying, never completes prematurely
function calculateProgress(elapsed: number, estimated: number): number {
  // Slower curve - reaches ~70% at estimated time, caps at 92%
  const k = 1.2 / estimated
  const asymptotic = 1 - Math.exp(-k * elapsed)
  return Math.min(asymptotic * 92, 92)
}

export function ResponseSkeleton() {
  const [progress, setProgress] = useState(0)
  const startTimeRef = useRef(Date.now())
  const estimatedTimeRef = useRef(getEstimatedResponseTime())
  const rafRef = useRef<number | null>(null)

  const animate = useCallback(() => {
    const elapsed = Date.now() - startTimeRef.current
    const newProgress = calculateProgress(elapsed, estimatedTimeRef.current)
    setProgress(newProgress)
    rafRef.current = requestAnimationFrame(animate)
  }, [])

  useEffect(() => {
    const startTime = startTimeRef.current

    // Small delay for smoother entrance
    const timer = setTimeout(() => {
      rafRef.current = requestAnimationFrame(animate)
    }, 80)

    return () => {
      clearTimeout(timer)
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current)
      }
      // Record the actual response time when component unmounts
      const duration = Date.now() - startTime
      // Only record reasonable durations (between 200ms and 60s)
      if (duration >= 200 && duration <= 60000) {
        recordResponseTime(duration)
      }
    }
  }, [animate])

  return (
    <div className="response-progress-container">
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
