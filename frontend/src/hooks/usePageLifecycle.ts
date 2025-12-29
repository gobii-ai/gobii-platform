import { useEffect, useRef } from 'react'

export type PageLifecycleResumeReason = 'visibility' | 'focus' | 'pageshow' | 'online'
export type PageLifecycleSuspendReason = 'visibility' | 'pagehide' | 'offline'

type PageLifecycleHandlers = {
  onResume?: (reason: PageLifecycleResumeReason) => void
  onSuspend?: (reason: PageLifecycleSuspendReason) => void
}

type PageLifecycleOptions = {
  resumeThrottleMs?: number
}

const DEFAULT_RESUME_THROTTLE_MS = 2000

export function usePageLifecycle(handlers: PageLifecycleHandlers, options: PageLifecycleOptions = {}) {
  const handlersRef = useRef(handlers)
  const lastResumeAtRef = useRef(0)
  const resumeThrottleMsRef = useRef(options.resumeThrottleMs ?? DEFAULT_RESUME_THROTTLE_MS)

  useEffect(() => {
    handlersRef.current = handlers
  }, [handlers])

  useEffect(() => {
    resumeThrottleMsRef.current = options.resumeThrottleMs ?? DEFAULT_RESUME_THROTTLE_MS
  }, [options.resumeThrottleMs])

  useEffect(() => {
    if (typeof window === 'undefined' || typeof document === 'undefined') {
      return () => undefined
    }

    const triggerResume = (reason: PageLifecycleResumeReason) => {
      const throttleMs = resumeThrottleMsRef.current
      const now = Date.now()
      if (throttleMs > 0 && now - lastResumeAtRef.current < throttleMs) {
        return
      }
      lastResumeAtRef.current = now
      handlersRef.current.onResume?.(reason)
    }

    const triggerSuspend = (reason: PageLifecycleSuspendReason) => {
      handlersRef.current.onSuspend?.(reason)
    }

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        triggerResume('visibility')
      } else {
        triggerSuspend('visibility')
      }
    }

    const handleFocus = () => {
      if (document.visibilityState === 'visible') {
        triggerResume('focus')
      }
    }

    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        triggerResume('pageshow')
      }
    }

    const handlePageHide = () => {
      triggerSuspend('pagehide')
    }

    const handleOnline = () => {
      triggerResume('online')
    }

    const handleOffline = () => {
      triggerSuspend('offline')
    }

    document.addEventListener('visibilitychange', handleVisibility)
    window.addEventListener('focus', handleFocus)
    window.addEventListener('pageshow', handlePageShow)
    window.addEventListener('pagehide', handlePageHide)
    window.addEventListener('online', handleOnline)
    window.addEventListener('offline', handleOffline)

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('focus', handleFocus)
      window.removeEventListener('pageshow', handlePageShow)
      window.removeEventListener('pagehide', handlePageHide)
      window.removeEventListener('online', handleOnline)
      window.removeEventListener('offline', handleOffline)
    }
  }, [])
}
