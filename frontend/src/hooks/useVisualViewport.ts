import { useEffect, useState } from 'react'

export function useVisualViewport() {
  const [viewportHeight, setViewportHeight] = useState<number | undefined>(() => {
    if (typeof window !== 'undefined' && window.visualViewport) {
      return window.visualViewport.height
    }
    return undefined
  })

  useEffect(() => {
    if (typeof window === 'undefined' || !window.visualViewport) {
      return
    }

    const handleResize = () => {
      if (window.visualViewport) {
        setViewportHeight(window.visualViewport.height)
      }
    }

    window.visualViewport.addEventListener('resize', handleResize)
    window.visualViewport.addEventListener('scroll', handleResize)

    return () => {
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', handleResize)
        window.visualViewport.removeEventListener('scroll', handleResize)
      }
    }
  }, [])

  return viewportHeight
}
