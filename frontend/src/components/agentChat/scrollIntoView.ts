type ScrollIntoViewOptions = {
  behavior?: ScrollBehavior
  marginBottom?: number
  marginTop?: number
}

const DEFAULT_MARGIN = 16
const DEBUG_SCROLL_HELPER = import.meta.env.DEV

function debugLog(...args: unknown[]) {
  if (!DEBUG_SCROLL_HELPER) {
    return
  }
  // eslint-disable-next-line no-console
  console.debug('[scrollIntoViewIfNeeded]', ...args)
}

function findScrollParent(element: HTMLElement): HTMLElement | null {
  let parent: HTMLElement | null = element.parentElement

  while (parent && parent !== document.body) {
    const style = window.getComputedStyle(parent)
    const overflowY = style.overflowY
    const overflow = style.overflow
    const isScrollable = overflowY === 'auto' || overflowY === 'scroll' || overflow === 'auto' || overflow === 'scroll'

    if (isScrollable && parent.scrollHeight > parent.clientHeight) {
      return parent
    }

    parent = parent.parentElement
  }

  return document.scrollingElement as HTMLElement | null
}

function measureViewportMargins(options: ScrollIntoViewOptions): { top: number; bottom: number } {
  const top = options.marginTop ?? DEFAULT_MARGIN

  if (typeof options.marginBottom === 'number') {
    return { top, bottom: options.marginBottom }
  }

  const composer = document.getElementById('agent-composer-shell')
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight

  if (!composer) {
    return { top, bottom: DEFAULT_MARGIN }
  }

  const composerRect = composer.getBoundingClientRect()
  const overlap = Math.max(0, viewportHeight - composerRect.top)

  return { top, bottom: overlap + DEFAULT_MARGIN }
}

function scrollElement(element: HTMLElement, delta: number, behavior: ScrollBehavior) {
  if (typeof element.scrollBy === 'function') {
    element.scrollBy({ top: delta, behavior })
  } else {
    element.scrollTop += delta
  }
}

export function scrollIntoViewIfNeeded(element: HTMLElement | null, options: ScrollIntoViewOptions = {}): void {
  if (!element) {
    return
  }

  const behavior = options.behavior ?? 'smooth'

  window.requestAnimationFrame(() => {
    const scrollParent = findScrollParent(element)
    if (!scrollParent) {
      return
    }

    const { top: marginTop, bottom: marginBottom } = measureViewportMargins(options)
    debugLog('evaluating', {
      element,
      scrollParent,
      marginTop,
      marginBottom,
    })

    if (scrollParent === document.scrollingElement || scrollParent === document.documentElement || scrollParent === document.body) {
      const rect = element.getBoundingClientRect()
      const viewportHeight = window.innerHeight || document.documentElement.clientHeight
      const visibleTop = marginTop
      const visibleBottom = viewportHeight - marginBottom

      if (rect.top < visibleTop) {
        debugLog('scrolling window up', { delta: rect.top - visibleTop, rectTop: rect.top, visibleTop })
        window.scrollBy({ top: rect.top - visibleTop, behavior })
        return
      }

      if (rect.bottom > visibleBottom) {
        const maxDelta = rect.top - visibleTop
        const neededDelta = rect.bottom - visibleBottom
        debugLog('scrolling window down', { maxDelta, neededDelta })
        window.scrollBy({ top: Math.min(neededDelta, maxDelta), behavior })
      }

      return
    }

    const rect = element.getBoundingClientRect()
    const parentRect = scrollParent.getBoundingClientRect()
    const visibleTop = parentRect.top + marginTop
    const visibleBottom = parentRect.bottom - marginBottom

    if (rect.top < visibleTop) {
      debugLog('scrolling parent up', { delta: rect.top - visibleTop, rectTop: rect.top, visibleTop })
      scrollElement(scrollParent, rect.top - visibleTop, behavior)
      return
    }

    if (rect.bottom > visibleBottom) {
      const maxDelta = rect.top - visibleTop
      const neededDelta = rect.bottom - visibleBottom
      debugLog('scrolling parent down', { maxDelta, neededDelta })
      scrollElement(scrollParent, Math.min(neededDelta, maxDelta), behavior)
    }
  })
}
