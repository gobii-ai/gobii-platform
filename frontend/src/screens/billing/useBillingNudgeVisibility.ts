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

  // Hide the bottom "Review and update" nudge once the user can see the real Update button.
  useEffect(() => {
    if (!enabled) {
      setSummaryActionsVisible(false)
      return
    }
    if (typeof document === 'undefined' || typeof window === 'undefined') {
      return
    }

    const el = document.getElementById(actionsElementId)
    if (!el) {
      setSummaryActionsVisible(false)
      return
    }

    if (typeof window.IntersectionObserver !== 'function') {
      const check = () => {
        const rect = el.getBoundingClientRect()
        const inView = rect.top < window.innerHeight && rect.bottom > 0
        setSummaryActionsVisible(inView)
      }
      check()
      window.addEventListener('scroll', check, { passive: true })
      window.addEventListener('resize', check)
      return () => {
        window.removeEventListener('scroll', check)
        window.removeEventListener('resize', check)
      }
    }

    const observer = new window.IntersectionObserver(
      (entries) => {
        const entry = entries[0]
        setSummaryActionsVisible(Boolean(entry?.isIntersecting))
      },
      { threshold: 0.05 },
    )
    observer.observe(el)
    return () => observer.disconnect()
  }, [actionsElementId, enabled])

  // If the user is already deep in the page, the nudge adds noise.
  useEffect(() => {
    if (!enabled) {
      setNearTop(true)
      return
    }
    if (typeof window === 'undefined') {
      return
    }

    const update = () => {
      setNearTop(window.scrollY < 240)
    }
    update()
    window.addEventListener('scroll', update, { passive: true })
    return () => window.removeEventListener('scroll', update)
  }, [enabled])

  return { summaryActionsVisible, nearTop }
}

