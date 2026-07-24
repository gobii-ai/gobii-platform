type RevealTimelineMessageOptions = {
  root?: ParentNode
  block?: ScrollLogicalPosition
  behavior?: ScrollBehavior
  highlight?: boolean
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
  target.scrollIntoView({
    block,
    behavior: behavior ?? (reducedMotion ? 'auto' : 'smooth'),
  })
  if (!highlight) return null

  target.classList.remove('message-search-target')
  window.requestAnimationFrame(() => target.classList.add('message-search-target'))
  return window.setTimeout(() => target.classList.remove('message-search-target'), 2200)
}
