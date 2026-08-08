import { useEffect } from 'react'

export function useWindowMessage<T extends { type?: unknown }>(type: string, onMessage: (message: T) => void) {
  useEffect(() => {
    const deliver = (message: T) => {
      if (message?.type === type) {
        onMessage(message)
      }
    }
    const handleMessage = (event: MessageEvent<T>) => {
      if (event.origin === window.location.origin) {
        deliver(event.data)
      }
    }
    const handleStorage = (event: StorageEvent) => {
      if (!event.key?.startsWith('gobii:oauth_complete:') || !event.newValue) {
        return
      }
      try {
        deliver(JSON.parse(event.newValue) as T)
        localStorage.removeItem(event.key)
      } catch {
        // Ignore unrelated or malformed completion entries.
      }
    }
    window.addEventListener('message', handleMessage)
    window.addEventListener('storage', handleStorage)
    return () => {
      window.removeEventListener('message', handleMessage)
      window.removeEventListener('storage', handleStorage)
    }
  }, [onMessage, type])
}
