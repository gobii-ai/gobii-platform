import { useEffect, useRef } from 'react'

const DRAWER_STATE_KEY = '__gobiiMobileDrawer'

/**
 * Give the mobile drawer a history entry so the platform back gesture closes it.
 *
 * Without one, back-swiping with the drawer open navigates the browser away from the app
 * entirely (bug #432) -- on Android the swipe is the reflexive way to dismiss any overlay.
 *
 * The drawer state stays owned by React; history only carries a marker entry:
 * - Opening pushes a same-URL entry whose state carries the marker.
 * - A popstate that lands below the marker means the user backed out of the drawer: close it.
 * - A popstate that lands ON a marker while the drawer is closed is a ghost left by closing
 *   through the UI; step back again so the entry is transparent to the user.
 */
export function useMobileDrawerHistory(open: boolean, enabled: boolean, close: () => void) {
  const openRef = useRef(open)
  openRef.current = open
  const closeRef = useRef(close)
  closeRef.current = close

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') {
      return
    }
    // A reload can land on a marker entry from the previous page life; the drawer starts
    // closed, so shed the marker rather than making the next back press a no-op.
    const state = window.history.state as Record<string, unknown> | null
    if (!openRef.current && state && state[DRAWER_STATE_KEY]) {
      const { [DRAWER_STATE_KEY]: _dropped, ...rest } = state
      window.history.replaceState(rest, '')
    }
  }, [enabled])

  useEffect(() => {
    if (!enabled || !open || typeof window === 'undefined') {
      return
    }
    const state = window.history.state as Record<string, unknown> | null
    if (state && state[DRAWER_STATE_KEY]) {
      return
    }
    window.history.pushState({ ...(state ?? {}), [DRAWER_STATE_KEY]: true }, '')
  }, [enabled, open])

  useEffect(() => {
    if (!enabled || typeof window === 'undefined') {
      return
    }
    const handlePop = (event: PopStateEvent) => {
      const onMarker = Boolean(
        event.state && (event.state as Record<string, unknown>)[DRAWER_STATE_KEY],
      )
      if (openRef.current && !onMarker) {
        closeRef.current()
      } else if (!openRef.current && onMarker) {
        window.history.back()
      }
    }
    window.addEventListener('popstate', handlePop)
    return () => window.removeEventListener('popstate', handlePop)
  }, [enabled])
}
