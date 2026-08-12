type SearchShortcutEvent = Pick<
  globalThis.KeyboardEvent,
  'altKey' | 'ctrlKey' | 'defaultPrevented' | 'key' | 'metaKey' | 'preventDefault' | 'shiftKey'
>

export function isMessageSearchShortcut(event: SearchShortcutEvent): boolean {
  return !event.defaultPrevented
    && event.key.toLocaleLowerCase() === 'k'
    && (event.metaKey || event.ctrlKey)
    && !event.altKey
    && !event.shiftKey
}

export function handleMessageSearchShortcut(event: SearchShortcutEvent, openSearch: () => void): boolean {
  if (!isMessageSearchShortcut(event)) {
    return false
  }
  event.preventDefault()
  openSearch()
  return true
}

export function getMessageSearchShortcutLabel(
  platform = typeof navigator === 'undefined' ? '' : navigator.platform,
): string {
  return /Mac|iPod|iPhone|iPad/.test(platform) ? '⌘K' : 'Ctrl K'
}
