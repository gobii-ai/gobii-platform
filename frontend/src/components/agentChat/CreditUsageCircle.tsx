import type { CSSProperties } from 'react'

import type { CreditAwarenessPayload } from '../../types/agentChat'

type CreditUsageCircleProps = {
  creditAwareness?: CreditAwarenessPayload | null
  onOpen?: () => void
  className?: string
}

function clampPercent(value: number | null | undefined): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return 0
  }
  return Math.max(0, Math.min(100, value))
}

export function CreditUsageCircle({ creditAwareness, onOpen, className = '' }: CreditUsageCircleProps) {
  if (!onOpen) {
    return null
  }

  const todayPercent = clampPercent(
    creditAwareness?.dailyCredits?.softPercentUsed
      ?? creditAwareness?.dailyCredits?.percentUsed
      ?? null,
  )
  const totalPercent = clampPercent(creditAwareness?.quota?.used_pct ?? null)
  const label = `${Math.round(todayPercent)}% used today. ${Math.round(totalPercent)}% used total.`

  return (
    <button
      type="button"
      className={`credit-usage-circle ${className}`.trim()}
      onClick={onOpen}
      aria-label={`Show credit usage. ${label}`}
      title={label}
      style={{ '--credit-percent': `${todayPercent}%` } as CSSProperties}
    />
  )
}
