import { useEffect } from 'react'

type WindowMessageOptions = {
  origin?: string
  storageKeyPrefix?: string
}

export function useWindowMessage<T extends { type?: unknown }>(
  type: string,
  onMessage: (message: T) => void,
  { origin, storageKeyPrefix = 'gobii:oauth_complete:' }: WindowMessageOptions = {},
) {
  useEffect(() => {
    const expectedOrigin = origin ?? window.location.origin
    const deliver = (message: T) => {
      if (message?.type === type) {
        onMessage(message)
      }
    }
    const handleMessage = (event: MessageEvent<T>) => {
      if (event.origin === expectedOrigin) {
        deliver(event.data)
      }
    }
    const handleStorage = (event: StorageEvent) => {
      if (!storageKeyPrefix || !event.key?.startsWith(storageKeyPrefix) || !event.newValue) {
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
  }, [onMessage, origin, storageKeyPrefix, type])
}
