const DEFAULT_HEIGHT_VAR = '--app-viewport-height'
const DEFAULT_BOTTOM_INSET_VAR = '--app-viewport-bottom-inset'
const GLOBAL_FLAG = '__gobiiViewportVarsInstalled'

type ViewportVarOptions = {
  heightVar?: string
  bottomInsetVar?: string
  target?: HTMLElement
}

function computeBottomInset(viewport: VisualViewport): number {
  const layoutHeight = window.innerHeight || viewport.height + viewport.offsetTop
  return Math.max(0, layoutHeight - (viewport.height + viewport.offsetTop))
}

export function installViewportCssVars(options: ViewportVarOptions = {}): () => void {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return () => undefined
  }

  const existingCleanup = (window as unknown as Record<string, () => void>)[GLOBAL_FLAG]
  if (existingCleanup) {
    return existingCleanup
  }

  const root = options.target ?? document.documentElement
  const heightVar = options.heightVar || DEFAULT_HEIGHT_VAR
  const bottomInsetVar = options.bottomInsetVar || DEFAULT_BOTTOM_INSET_VAR

  const applyVars = () => {
    const viewport = window.visualViewport
    const height = viewport?.height ?? window.innerHeight ?? document.documentElement.clientHeight ?? 0
    root.style.setProperty(heightVar, `${height}px`)

    if (viewport) {
      root.style.setProperty(bottomInsetVar, `${computeBottomInset(viewport)}px`)
    } else {
      root.style.setProperty(bottomInsetVar, '0px')
    }
  }

  applyVars()

  const listeners: Array<[EventTarget, string, EventListener]> = [
    [window, 'resize', applyVars],
  ]

  if (window.visualViewport) {
    listeners.push([window.visualViewport, 'resize', applyVars], [window.visualViewport, 'scroll', applyVars])
  }

  listeners.forEach(([target, event, handler]) => target.addEventListener(event, handler))

  const cleanup = () => {
    listeners.forEach(([target, event, handler]) => target.removeEventListener(event, handler))
    root.style.removeProperty(heightVar)
    root.style.removeProperty(bottomInsetVar)
  }

  ;(window as unknown as Record<string, () => void>)[GLOBAL_FLAG] = cleanup

  return cleanup
}
