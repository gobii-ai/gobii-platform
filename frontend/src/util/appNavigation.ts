import type { MouseEvent } from 'react'

export const APP_NAVIGATE_EVENT = 'gobii:app:navigate'

export function navigateWithinApp(path: string): boolean {
  if (typeof window === 'undefined' || !window.location.pathname.startsWith('/app')) {
    return false
  }
  window.dispatchEvent(new CustomEvent(APP_NAVIGATE_EVENT, {
    detail: { path },
  }))
  return true
}

export function handleAppAnchorClick(event: MouseEvent<HTMLAnchorElement>, path: string): boolean {
  if (
    event.defaultPrevented
    || event.button !== 0
    || event.metaKey
    || event.altKey
    || event.ctrlKey
    || event.shiftKey
  ) {
    return false
  }

  if (!navigateWithinApp(path)) {
    return false
  }

  event.preventDefault()
  return true
}
