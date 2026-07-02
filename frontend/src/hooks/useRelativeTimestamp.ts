import { useEffect, useMemo, useState } from 'react'

import { formatRelativeTimestamp } from '../util/time'

const SUB_MINUTE_REFRESH_MS = 5_000
const RECENT_REFRESH_MS = 30_000
const HOURLY_REFRESH_MS = 5 * 60_000
const OLDER_REFRESH_MS = 30 * 60_000

function parseTimestampMs(value?: string | null): number | null {
  if (!value) {
    return null
  }
  const timestampMs = Date.parse(value)
  return Number.isNaN(timestampMs) ? null : timestampMs
}

function refreshDelayMs(timestampMs: number, nowMs: number): number {
  const ageMs = Math.abs(nowMs - timestampMs)
  if (ageMs < 60_000) {
    return SUB_MINUTE_REFRESH_MS
  }
  if (ageMs < 60 * 60_000) {
    return RECENT_REFRESH_MS
  }
  if (ageMs < 24 * 60 * 60_000) {
    return HOURLY_REFRESH_MS
  }
  return OLDER_REFRESH_MS
}

export function useRelativeTimestamp(value?: string | null): string | null {
  const timestampMs = useMemo(() => parseTimestampMs(value), [value])
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    if (timestampMs === null) {
      return
    }

    let timeoutId: number | null = null
    let cancelled = false

    const refresh = () => {
      const nextNowMs = Date.now()
      if (cancelled) {
        return
      }
      setNowMs(nextNowMs)
      timeoutId = window.setTimeout(refresh, refreshDelayMs(timestampMs, nextNowMs))
    }

    refresh()

    return () => {
      cancelled = true
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId)
      }
    }
  }, [timestampMs])

  return useMemo(() => {
    if (timestampMs === null) {
      return null
    }
    return formatRelativeTimestamp(value, new Date(nowMs))
  }, [nowMs, timestampMs, value])
}
