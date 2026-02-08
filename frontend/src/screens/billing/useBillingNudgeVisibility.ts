import { useEffect, useState } from 'react'

type BillingNudgeVisibilityOptions = {
  enabled: boolean
  actionsElementId?: string
}

export function useBillingNudgeVisibility({
  enabled,
  actionsElementId = 'billing-summary-actions',
}: BillingNudgeVisibilityOptions) {
  const [summaryActionsVisible, setSummaryActionsVisible] = useState(false)
  const [nearTop, setNearTop] = useState(true)

  // Show the bottom nudge only while the real Update button area is still below the viewport.
  useEffect(() => {
    if (!enabled) {
      setSummaryActionsVisible(false)
      setNearTop(true)
      return
    }
    if (typeof document === 'undefined' || typeof window === 'undefined') return

    const el = document.getElementById(actionsElementId)
    if (!el) {
      setSummaryActionsVisible(false)
      setNearTop(true)
      return
    }

    const check = () => {
      const rect = el.getBoundingClientRect()
      const inView = rect.top < window.innerHeight && rect.bottom > 0
      const belowFold = rect.top >= window.innerHeight
      setSummaryActionsVisible(inView)
      // Repurpose nearTop as "actions are still below the fold" so the nudge doesn't
      // disappear just because the user scrolled a bit within the page.
      setNearTop(belowFold)
    }

    check()
    window.addEventListener('scroll', check, { passive: true })
    window.addEventListener('resize', check)
    return () => {
      window.removeEventListener('scroll', check)
      window.removeEventListener('resize', check)
    }
  }, [actionsElementId, enabled])

  return { summaryActionsVisible, nearTop }
}
