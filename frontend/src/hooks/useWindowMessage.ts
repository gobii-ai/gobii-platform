import { useEffect } from 'react'

export function useWindowMessage<T extends { type?: unknown }>(type: string, onMessage: (message: T) => void) {
  useEffect(() => {
    const handleMessage = (event: MessageEvent<T>) => {
      if (event.origin === window.location.origin && event.data?.type === type) {
        onMessage(event.data)
      }
    }
    window.addEventListener('message', handleMessage)
    return () => window.removeEventListener('message', handleMessage)
  }, [onMessage, type])
}
