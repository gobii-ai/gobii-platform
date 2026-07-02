import { useEffect, useState } from 'react'

import { formatRelativeTimestamp } from '../util/time'

function refreshDelayMs(timestampMs: number): number {
  const ageMs = Math.abs(Date.now() - timestampMs)
  if (ageMs < 60_000) return 5_000
  if (ageMs < 60 * 60_000) return 30_000
  if (ageMs < 24 * 60 * 60_000) return 5 * 60_000
  return 30 * 60_000
}

export function useRelativeTimestamp(value?: string | null): string | null {
  const timestampMs = value ? Date.parse(value) : NaN
  const [nowMs, setNowMs] = useState<number | null>(null)

  useEffect(() => {
    setNowMs(Date.now())
  }, [])

  useEffect(() => {
    if (Number.isNaN(timestampMs) || nowMs === null) return
    const timeoutId = window.setTimeout(() => setNowMs(Date.now()), refreshDelayMs(timestampMs))
    return () => window.clearTimeout(timeoutId)
  }, [nowMs, timestampMs])

  return Number.isNaN(timestampMs) || nowMs === null ? null : formatRelativeTimestamp(value, new Date(nowMs))
}
