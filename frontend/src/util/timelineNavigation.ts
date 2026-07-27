type RevealTimelineMessageOptions = {
  root?: ParentNode
  block?: ScrollLogicalPosition
  behavior?: ScrollBehavior
  highlight?: boolean
}

function scrollWithinTimeline(
  target: HTMLElement,
  block: ScrollLogicalPosition,
  behavior: ScrollBehavior,
): void {
  const container = target.closest<HTMLElement>('#timeline-shell')
  if (!container) {
    target.scrollIntoView({ block, behavior })
    return
  }

  const containerRect = container.getBoundingClientRect()
  const targetRect = target.getBoundingClientRect()
  const targetStyle = window.getComputedStyle(target)
  const marginTop = Number.parseFloat(targetStyle.scrollMarginTop) || 0
  const marginBottom = Number.parseFloat(targetStyle.scrollMarginBottom) || 0
  const start = targetRect.top - containerRect.top - marginTop
  const end = targetRect.bottom - containerRect.bottom + marginBottom
  let top = start

  if (block === 'center') {
    top -= (container.clientHeight - targetRect.height) / 2
  } else if (block === 'end') {
    top = end
  } else if (block === 'nearest') {
    if (targetRect.top >= containerRect.top + marginTop
      && targetRect.bottom <= containerRect.bottom - marginBottom) {
      return
    }
    top = targetRect.top < containerRect.top ? start : end
  }

  container.scrollBy({ top, behavior })
}

export function revealTimelineMessage(
  messageId: string,
  {
    root = document,
    block = 'start',
    behavior,
    highlight = false,
  }: RevealTimelineMessageOptions = {},
): number | null {
  const escaped = typeof CSS !== 'undefined' && typeof CSS.escape === 'function'
    ? CSS.escape(messageId)
    : messageId.replace(/["\\]/g, '\\$&')
  const target = root.querySelector<HTMLElement>(`[data-message-id="${escaped}"]`)
  if (!target) return null

  const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  scrollWithinTimeline(
    target,
    block,
    behavior ?? (reducedMotion ? 'auto' : 'smooth'),
  )
  if (!highlight) return null

  target.classList.remove('message-search-target')
  window.requestAnimationFrame(() => target.classList.add('message-search-target'))
  return window.setTimeout(() => target.classList.remove('message-search-target'), 2200)
}
